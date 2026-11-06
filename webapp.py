#!/usr/bin/env python3
"""
Web UI for the MeshCom <-> Email gateway.

Pages:
  /            status dashboard (daemon alive?, counters, last 10 messages)
  /messages    full paginated message history
  /mesh        mesh text messages sorted by group (*, number, callsign)
  /udp         UDP monitor (raw datagrams to/from the MeshCom node)
  /config      edit configuration (writes config.json; the daemon
               re-reads it on its next loop, no restart needed for
               most settings)

Run:
    pip install flask
    python3 webapp.py
Then open http://<raspberry-pi-ip>:8899/

Protected with HTTP basic auth using web_username/web_password from
config.json. Set web_password to empty string to disable auth (not
recommended if the Pi is reachable from outside your LAN).
"""

import hmac
import ipaddress
import os
import re
import secrets
import socket
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from functools import wraps

from flask import Flask, abort, flash, request, redirect, url_for, render_template, Response, session

import bpq_config
import bpq_services
import common
import gateway
import radio_apps
import system_time
import update_manager

app = Flask(__name__)
app.secret_key = os.environ.get("MESHCOM_FLASK_SECRET") or secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
common.init_db()


def csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def require_csrf_token():
    expected = session.get("_csrf_token", "")
    submitted = request.form.get("csrf_token", "")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        abort(400, description="Neplatný nebo chybějící bezpečnostní token.")


app.jinja_env.globals["csrf_token"] = csrf_token


MAIL_DOMAIN = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def validate_config_values(form_data, cfg):
    errors = {}
    numeric_fields = {
        "mesh_udp_port": (1, 65535),
        "smtp_port": (1, 65535),
        "imap_port": (1, 65535),
        "poll_interval_seconds": (1, 86400),
        "max_mesh_payload": (20, gateway.MESHCOM_MAX_TEXT_BYTES),
        "max_email_parts": (1, 20),
        "email_part_delay_seconds": (0, 300),
    }

    for field, (minimum, maximum) in numeric_fields.items():
        raw_value = form_data.get(field)
        if raw_value is None or raw_value == "":
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            errors[field] = "musí být celé číslo"
            continue

        if value < minimum or value > maximum:
            errors[field] = f"musí být v rozsahu {minimum} až {maximum}"

    if form_data.get("imap_host") and not form_data.get("imap_user"):
        errors["imap_user"] = "je potřeba pro IMAP server"

    if form_data.get("imap_user") and not form_data.get("imap_host"):
        errors["imap_host"] = "je potřeba pro IMAP uživatele"

    smtp_host = str(form_data.get("smtp_host", "")).strip().lower().strip("[]")
    smtp_user = str(form_data.get("smtp_user", "")).strip()
    local_smtp = smtp_host == "localhost"
    if not local_smtp and smtp_host:
        try:
            local_smtp = ipaddress.ip_address(smtp_host).is_loopback
        except ValueError:
            pass

    for domain in str(form_data.get("mesh_email_domains", "")).split(","):
        domain = domain.strip().lstrip("@")
        if domain and not MAIL_DOMAIN.fullmatch(domain):
            errors["mesh_email_domains"] = f"neplatná doména {domain}"
            break

    if smtp_host and not smtp_user and not local_smtp:
        errors["smtp_user"] = "je potřeba pro SMTP server"

    return {"ok": not errors, "errors": errors}


def check_auth(username, password, cfg):
    if not cfg.get("web_password"):
        return True  # auth disabled
    return username == cfg.get("web_username") and password == cfg.get("web_password")


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        cfg = common.load_config()
        if not cfg.get("web_password"):
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password, cfg):
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="MeshCom Gateway"'},
            )
        return f(*args, **kwargs)
    return decorated


@app.route("/")
@requires_auth
def status():
    cfg = common.load_config()
    now = time.time()
    hb = common.get_heartbeat("gateway")
    alive = hb is not None and (now - hb) < 30
    age = round(now - hb) if hb else None
    counts = common.count_messages()
    recent = format_message_rows(common.get_recent_messages(limit=10), cfg)
    udp = common.get_udp_activity()
    return render_template(
        "status.html",
        cfg=cfg, alive=alive, age=age, counts=counts, recent=recent,
        udp=udp, now=now,
    )


# Same group numbers as the GROUP: tag in an e-mail subject.
SEND_GROUP = re.compile(r"\d{1,5}")


@app.route("/send", methods=["POST"])
@requires_auth
def send_message():
    require_csrf_token()
    cfg = common.load_config()
    default_group = str(cfg.get("mesh_group", "")).strip()
    group = request.form.get("group", default_group).strip().lstrip("#")
    message = request.form.get("message", "").strip()
    max_length = gateway.mesh_text_limit(cfg)
    # Keep a group typed by hand in the form after the redirect.
    back = {"group": group} if group and group != default_group else {}

    if group and group != default_group and not SEND_GROUP.fullmatch(group):
        flash("Skupina musí být číslo o 1 až 5 číslicích.", "error")
    elif not message:
        flash("Zpráva nesmí být prázdná.", "error")
    elif gateway.utf8_len(message) > max_length:
        flash(f"Zpráva má {gateway.utf8_len(message)} bajtů; limit pro MeshCom je {max_length} "
              "(písmena s diakritikou zabírají 2 bajty).", "error")
    elif not group:
        flash("Není nastavena cílová MeshCom skupina.", "error")
    else:
        try:
            gateway.send_udp_message(cfg, group, message, source="web")
        except OSError as exc:
            common.log_message("web_to_mesh", group, "Web → MeshCom", message,
                               status="error", error=str(exc))
            flash(f"Odeslání do MeshCom selhalo: {exc}", "error")
        else:
            common.log_message("web_to_mesh", group, "Web → MeshCom", message)
            flash(
                f"UDP datagram byl předán k odeslání do skupiny {group}; MeshCom nepotvrzuje doručení.",
                "success",
            )

    return redirect(url_for("messages", **back, _anchor="send-message"))


@app.route("/messages")
@requires_auth
def messages():
    cfg = common.load_config()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 25
    rows = format_message_rows(common.get_recent_messages(limit=per_page, offset=(page - 1) * per_page), cfg)
    counts = common.count_messages()
    total_pages = max((counts["total"] + per_page - 1) // per_page, 1)
    outgoing = common.get_outgoing_messages(limit=20)
    # Groups heard in the last 30 days, offered as suggestions in the send form.
    known_groups = [row["channel"] for row in common.get_rf_channel_summary(time.time() - 30 * 86400, time.time())
                    if row["kind"] == "group"]
    for row in outgoing:
        row["created_label"] = format_ts(row["created_at"], cfg)
        row["status_label_time"] = format_ts(row["status_at"], cfg, "%H:%M:%S")
    return render_template(
        "messages.html", rows=rows, page=page, total_pages=total_pages,
        cfg=cfg, outgoing=outgoing, max_length=gateway.mesh_text_limit(cfg),
        send_group=request.args.get("group", "").strip() or str(cfg.get("mesh_group", "")).strip(),
        known_groups=known_groups,
    )


def format_ts(epoch: float, cfg: dict, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Epoch seconds in the Pi's time zone, or in UTC when display_utc is on."""
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    if cfg.get("display_utc"):
        return moment.strftime(fmt) + " UTC"
    return moment.astimezone().strftime(fmt)


def format_message_rows(rows: list, cfg: dict) -> list:
    """The message log stores ts as SQLite datetime('now'), i.e. UTC text."""
    for row in rows:
        try:
            stamp = datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        row["ts"] = format_ts(stamp.timestamp(), cfg)
    return rows


def parse_date_range(default_days: int, cfg: dict):
    """Inclusive ?start=&end= dates as (start_day, end_day, since, until), in the display time zone."""
    zone = timezone.utc if cfg.get("display_utc") else None
    today = datetime.now(zone).date()
    start_value = request.args.get("start", (today - timedelta(days=default_days - 1)).isoformat())
    end_value = request.args.get("end", today.isoformat())
    try:
        start_day = date.fromisoformat(start_value)
        end_day = date.fromisoformat(end_value)
    except ValueError:
        abort(400, description="Neplatný formát data; použijte RRRR-MM-DD.")
    if start_day > end_day:
        abort(400, description="Datum od musí být před datem do.")
    since = datetime.combine(start_day, datetime_time.min, zone).timestamp()
    until = datetime.combine(end_day + timedelta(days=1), datetime_time.min, zone).timestamp()
    return start_day, end_day, since, until


@app.route("/mesh")
@requires_auth
def mesh_messages():
    cfg = common.load_config()
    start_day, end_day, since, until = parse_date_range(default_days=7, cfg=cfg)
    channel = request.args.get("channel", "").strip().upper()
    kind = request.args.get("kind", "")
    if channel or kind not in {"group", "direct"}:
        kind = ""
    channels = common.get_rf_channel_summary(since, until)
    for row in channels:
        row["last_at_label"] = format_ts(row["last_at"], cfg, "%Y-%m-%d %H:%M")
    rows = common.get_heard_rf_messages(since, until, channel, limit=500, kind=kind)
    own_callsign = str(cfg.get("callsign", "")).strip().upper()
    delivery = common.get_delivery_by_msg_id([row["msg_id"] for row in rows if row["station"] == own_callsign])
    for row in rows:
        row["observed_at_label"] = format_ts(row["observed_at"], cfg)
        row["kind"] = common.channel_kind(row["channel"])
        row["delivery"] = delivery.get(row["msg_id"]) if row["station"] == own_callsign else None
    return render_template(
        "mesh.html",
        cfg=cfg, channels=channels, rows=rows,
        total=common.count_heard_rf_messages(since, until, channel, kind=kind),
        channel=channel, kind=kind,
        own_callsign=own_callsign,
        main_group=str(cfg.get("mesh_group", "")).strip().upper(),
        start_date=start_day.isoformat(), end_date=end_day.isoformat(),
    )


def local_ip_towards(host: str):
    """Address of this machine on the route to host (the node's --extudpip)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, 1))  # UDP connect only selects a route, sends nothing
        return sock.getsockname()[0]
    except (OSError, UnicodeError):
        return None
    finally:
        sock.close()


@app.route("/udp")
@requires_auth
def udp_monitor():
    cfg = common.load_config()
    now = time.time()
    direction = request.args.get("direction", "")
    if direction not in {"rx", "tx"}:
        direction = ""
    packets = common.get_udp_packets(limit=200, direction=direction or None)
    for row in packets:
        row["ts_label"] = format_ts(row["ts"], cfg)
    gateway_hb = common.get_heartbeat("gateway")
    listener_hb = common.get_heartbeat(gateway.UDP_LISTENER_HEARTBEAT)
    return render_template(
        "udp.html",
        cfg=cfg, now=now, packets=packets, direction=direction,
        udp=common.get_udp_activity(),
        gateway_alive=gateway_hb is not None and now - gateway_hb < 30,
        listener_alive=listener_hb is not None and now - listener_hb < 30,
        local_ip=local_ip_towards(str(cfg.get("mesh_node_ip", ""))),
    )


@app.route("/stations")
@requires_auth
def stations():
    cfg = common.load_config()
    start_day, end_day, since, until = parse_date_range(default_days=30, cfg=cfg)
    selected_station = request.args.get("station", "").strip().upper()
    # Default: only stations reachable over LoRa; "all" also shows frames a gateway took from the internet.
    paths_mode = "all" if request.args.get("paths") == "all" else "rf"
    own_callsign = str(cfg.get("callsign", "")).strip().upper()
    own_position = common.get_own_position()
    max_hops, max_link_km = common.station_path_limits(cfg)
    path_check = common.rf_path_checker(until, own_callsign, own_position, max_hops, max_link_km)
    active_check = path_check if paths_mode == "rf" else None
    summary = common.get_station_summary(since, until, path_check=active_check)
    history_count = common.count_station_history(since, until, selected_station, path_check=active_check)
    history = common.get_station_history(since, until, selected_station, limit=500, path_check=active_check)
    hidden_stations = common.get_hidden_stations(since, until, path_check) if paths_mode == "rf" else []
    for row in hidden_stations:
        row["problem_label"] = common.RF_PATH_PROBLEMS[row["problem"]].format(
            max_hops=max_hops, max_link_km=max_link_km)
        row["last_seen_label"] = format_ts(row["last_seen"], cfg)
    paths_arg = "all" if paths_mode == "all" else None  # keeps the mode in links; None drops the argument

    now = time.time()
    map_stations = []
    for row in summary:
        row["presence"] = common.station_presence(row["last_seen"], now)
        row["presence_icon"], row["presence_label"] = common.STATION_PRESENCE[row["presence"]]
        row["age_label"] = common.age_label(now - row["last_seen"])
        row["first_seen_label"] = format_ts(row["first_seen"], cfg)
        row["last_seen_label"] = format_ts(row["last_seen"], cfg)
        row["direct"] = row["heard_from"] == row["station"]
        row["hops"] = len(common.path_calls(row["source_path"])) - 1
        row["path_problem"] = path_check(row["source_path"])
        row["distance_km"] = None
        if row["latitude"] is None or row["longitude"] is None:
            continue
        if own_position:
            row["distance_km"] = round(common.distance_km(
                own_position["latitude"], own_position["longitude"], row["latitude"], row["longitude"]), 1)
        map_stations.append({
            key: row[key] for key in (
                "station", "latitude", "longitude", "direct", "heard_from", "source_path", "rssi",
                "snr", "altitude", "battery_percent", "last_seen_label", "distance_km", "observations",
                "presence", "presence_icon", "presence_label", "age_label")
        } | {"url": url_for("stations", start=start_day.isoformat(), end=end_day.isoformat(),
                            station=row["station"], paths=paths_arg, _anchor="station-history")})
    for row in history:
        row["observed_at_label"] = format_ts(row["observed_at"], cfg)
        row["hops"] = len(common.path_calls(row["source_path"])) - 1
        row["path_problem"] = path_check(row["source_path"])

    station_paths = common.get_station_paths(since, until, path_check=active_check)
    # Relays without a position in the period: use their last known beacon so paths stay connected.
    placed = {row["station"] for row in map_stations} | {own_callsign}
    missing = {call for row in station_paths for call in row["source_path"].split(",")} - placed
    relay_positions = [
        {"station": row["station"], "latitude": row["latitude"], "longitude": row["longitude"],
         "last_seen_label": format_ts(row["observed_at"], cfg)}
        for row in common.get_last_positions(missing, until).values()
    ]

    return render_template(
        "stations.html",
        summary=summary,
        map_stations=map_stations,
        own_position=own_position,
        node_position=common.get_node_position(),
        relay_positions=relay_positions,
        station_paths=[{"station": row["station"], "source_path": row["source_path"],
                        "observations": row["observations"]}
                       for row in station_paths],
        own_callsign=own_callsign,
        history=history,
        history_count=history_count,
        selected_station=selected_station,
        start_date=start_day.isoformat(),
        end_date=end_day.isoformat(),
        paths_mode=paths_mode,
        paths_arg=paths_arg,
        hidden_stations=hidden_stations,
        max_hops=max_hops,
        max_link_km=max_link_km,
    )


@app.route("/stations/position", methods=["POST"])
@requires_auth
def station_position():
    require_csrf_token()
    back = {key: request.form[key] for key in ("start", "end", "paths") if request.form.get(key)}
    if request.form.get("action") == "clear":
        common.clear_manual_position()
        flash("Ruční poloha zrušena; vzdálenosti se měří od polohy z majáku uzlu.", "success")
    else:
        try:
            common.set_manual_position(request.form.get("latitude", ""), request.form.get("longitude", ""))
        except ValueError:
            flash("Neplatné souřadnice.", "error")
        else:
            flash("Poloha uzlu uložena; vzdálenosti se měří od ní.", "success")
    return redirect(url_for("stations", **back, _anchor="station-map-heading"))


@app.route("/config", methods=["GET", "POST"])
@requires_auth
def config_page():
    cfg = common.load_config()
    saved = False
    errors = {}
    if request.method == "POST":
        require_csrf_token()
        new_cfg = dict(cfg)
        for key in (
            "mesh_node_ip", "mesh_group", "smtp_host", "smtp_user",
            "mail_from", "mail_to", "maildir_new", "maildir_cur",
            "imap_host", "imap_user", "imap_folder", "callsign", "web_username", "mesh_email_domains",
        ):
            new_cfg[key] = request.form.get(key, cfg.get(key, ""))

        validation = validate_config_values(request.form, new_cfg)
        errors = validation["errors"]

        if validation["ok"]:
            for key in ("mesh_udp_port", "smtp_port", "imap_port", "poll_interval_seconds", "max_mesh_payload",
                        "max_email_parts", "email_part_delay_seconds"):
                raw = request.form.get(key)
                if raw is not None and raw != "":
                    new_cfg[key] = int(raw)

            new_cfg["smtp_use_tls"] = request.form.get("smtp_use_tls") == "on"
            new_cfg["imap_use_ssl"] = request.form.get("imap_use_ssl") == "on"
            for key in ("mesh_email_forward", "mesh_email_addresses", "mesh_email_all_domains"):
                new_cfg[key] = request.form.get(key) == "on"
            new_cfg["mesh_email_domains"] = ", ".join(
                d.strip().lower().lstrip("@") for d in new_cfg["mesh_email_domains"].split(",") if d.strip())

            # Only overwrite passwords if a new value was actually typed in,
            # so the form doesn't need to round-trip secrets in plaintext.
            new_smtp_pw = request.form.get("smtp_password")
            if new_smtp_pw:
                new_cfg["smtp_password"] = new_smtp_pw

            new_imap_pw = request.form.get("imap_password")
            if new_imap_pw:
                new_cfg["imap_password"] = new_imap_pw

            new_web_pw = request.form.get("web_password")
            if new_web_pw:
                new_cfg["web_password"] = new_web_pw

            common.save_config(new_cfg)
            cfg = new_cfg
            saved = True

    return render_template("config.html", cfg=cfg, saved=saved, errors=errors,
                           email_aliases=common.get_email_aliases(),
                           time_status=system_time.status(), timezones=system_time.timezones())


@app.route("/config/time", methods=["POST"])
@requires_auth
def system_time_settings():
    require_csrf_token()
    done = []
    try:
        if request.form.get("action") == "set_time":
            system_time.set_time(request.form.get("manual_time", ""))
            flash("Čas Raspberry Pi nastaven.", "success")
        else:
            display_utc = request.form.get("display_utc") == "on"
            cfg = common.load_config()
            if display_utc != bool(cfg.get("display_utc")):
                common.save_config({**cfg, "display_utc": display_utc})
                done.append("časy na webu v UTC" if display_utc else "časy na webu v čase Raspberry Pi")
            current = system_time.status()
            zone = request.form.get("timezone", "").strip()
            if zone and zone != current["timezone"]:
                system_time.set_timezone(zone)
                done.append(f"časové pásmo {zone}")
            ntp = request.form.get("ntp") == "on"
            if ntp != current["ntp"]:
                system_time.set_ntp(ntp)
                done.append("NTP zapnuto" if ntp else "NTP vypnuto")
            servers = " ".join(system_time.parse_ntp_servers(request.form.get("ntp_servers", "")))
            if servers != " ".join(current["servers"].split()):
                system_time.request_ntp_servers(servers)
                done.append(f"NTP servery {servers}" if servers else "výchozí NTP servery")
            flash("Uloženo: " + ", ".join(done) + "." if done else "Beze změny.", "success")
    except system_time.TimeError as exc:
        if done:
            flash("Uloženo: " + ", ".join(done) + ".", "success")
        flash(f"Čas: {exc}", "error")
    return redirect(url_for("config_page", _anchor="system-time"))


@app.route("/config/aliases", methods=["POST"])
@requires_auth
def email_aliases():
    require_csrf_token()
    alias = request.form.get("alias", "")
    if request.form.get("action") == "delete":
        common.delete_email_alias(alias)
        flash(f"Alias {alias} smazán.", "success")
    else:
        try:
            alias = common.set_email_alias(alias, request.form.get("email", ""))
        except ValueError as exc:
            flash(str(exc), "error")
        else:
            flash(f"Alias {alias} uložen.", "success")
    return redirect(url_for("config_page", _anchor="email-aliases"))


RADIO_TEXT_FIELDS = (
    "radio_display", "radio_wine_prefix", "radio_vara_path",
    "radio_mercury_command", "radio_mercury_args", "radio_varac_path",
)


def save_radio_settings(form_data, cfg):
    new_cfg = dict(cfg)
    errors = []
    for key in RADIO_TEXT_FIELDS:
        new_cfg[key] = str(form_data.get(key, cfg.get(key, ""))).strip()
    new_cfg["radio_mercury_gui"] = form_data.get("radio_mercury_gui") == "on"
    new_cfg["radio_mercury_no_waterfall"] = form_data.get("radio_mercury_no_waterfall") == "on"
    try:
        gui_port = int(form_data.get("radio_mercury_gui_port", ""))
    except (TypeError, ValueError):
        gui_port = 0
    modem_port = radio_apps.mercury_port(new_cfg)
    if not 1024 <= gui_port <= 65535:
        errors.append("Mercury: port webového GUI musí být 1024 až 65535.")
    elif gui_port in radio_apps.BROWSER_BLOCKED_PORTS:
        errors.append(f"Mercury: port {gui_port} prohlížeče blokují (ERR_UNSAFE_PORT), zvolte jiný, např. "
                      f"{radio_apps.DEFAULT_MERCURY_GUI_PORT}.")
    elif gui_port in {modem_port, modem_port + 1}:
        errors.append(f"Mercury: port webového GUI koliduje s TCP portem modemu {modem_port}/{modem_port + 1}.")
    else:
        new_cfg["radio_mercury_gui_port"] = gui_port
    for app_name, spec in radio_apps.APPS.items():
        new_cfg[f"radio_{app_name}_autostart"] = form_data.get(f"radio_{app_name}_autostart") == "on"
        raw_delay = form_data.get(f"radio_{app_name}_delay", "")
        try:
            delay = int(raw_delay)
        except (TypeError, ValueError):
            errors.append(f"{spec['label']}: zpoždění musí být celé číslo.")
            continue
        if not 0 <= delay <= radio_apps.MAX_DELAY_SECONDS:
            errors.append(f"{spec['label']}: zpoždění musí být 0 až {radio_apps.MAX_DELAY_SECONDS} s.")
            continue
        new_cfg[f"radio_{app_name}_delay"] = delay

    for key in ("radio_vara_path", "radio_varac_path"):
        if new_cfg[key] and not new_cfg[key].lower().endswith(".exe"):
            errors.append(f"{key}: cesta musí končit na .exe.")
    if os.path.basename(new_cfg["radio_mercury_command"] or "mercury") != "mercury":
        errors.append("radio_mercury_command: povolen je jen program mercury.")
    return new_cfg, errors


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """True if something accepts TCP connections on the local port."""
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


@app.route("/radio", methods=["GET", "POST"])
@requires_auth
def radio():
    cfg = common.load_config()
    if request.method == "POST":
        require_csrf_token()
        action = request.form.get("action")
        if action == "save":
            new_cfg, errors = save_radio_settings(request.form, cfg)
            if errors:
                for message in errors:
                    flash(message, "error")
            else:
                common.save_config(new_cfg)
                try:
                    present = radio_apps.sync_autostart_entry(new_cfg)
                except OSError as exc:
                    flash(f"Nastavení uloženo, ale autostart se nepodařilo zapsat: {exc}", "error")
                else:
                    flash("Nastavení uloženo; automatický start je "
                          + ("zapnutý." if present else "vypnutý."), "success")
        elif action in {"start", "stop", "restart"}:
            app_name = request.form.get("app", "")
            if app_name not in radio_apps.APPS:
                abort(400, description="Neznámý program.")
            label = radio_apps.APPS[app_name]["label"]
            try:
                if action == "start":
                    started = radio_apps.start(app_name, cfg)
                    flash(f"{label} spuštěn." if started else f"{label} už běží.", "success")
                elif action == "stop":
                    radio_apps.stop(app_name)
                    flash(f"{label} zastaven.", "success")
                else:
                    radio_apps.restart(app_name, cfg)
                    flash(f"{label} restartován.", "success")
            except radio_apps.RadioError as exc:
                flash(f"{label}: {exc}", "error")
        else:
            abort(400, description="Neznámá akce.")
        return redirect(url_for("radio"))

    apps = []
    for app_name, spec in radio_apps.APPS.items():
        apps.append({
            "name": app_name,
            "label": spec["label"],
            "status": radio_apps.status(app_name),
            "log": radio_apps.recent_log(app_name),
            "autostart": bool(cfg.get(f"radio_{app_name}_autostart")),
            "delay": cfg.get(f"radio_{app_name}_delay", 0),
        })
    mercury_port = radio_apps.mercury_port(cfg)
    port_conflict = mercury_port == radio_apps.DEFAULT_MODEM_PORT and (
        cfg.get("radio_vara_autostart") and cfg.get("radio_mercury_autostart")
        or all(item["status"]["state"] == "active" for item in apps if item["name"] in {"vara", "mercury"})
    )
    mercury_gui_port = radio_apps.mercury_gui_port(cfg)
    mercury_gui_url = None
    mercury_gui_listening = False
    if cfg.get("radio_mercury_gui"):
        mercury_gui_url = f"http://{request.host.split(':')[0]}:{mercury_gui_port}/"
        mercury_gui_listening = port_listening(mercury_gui_port)
    return render_template(
        "radio.html", cfg=cfg, apps=apps, mercury_port=mercury_port,
        mercury_gui_port=mercury_gui_port, mercury_gui_url=mercury_gui_url,
        mercury_gui_listening=mercury_gui_listening,
        port_conflict=port_conflict,
        autostart_present=os.path.exists(radio_apps.AUTOSTART_FILE),
    )


BPQ_TNC_FIELDS = (
    "tcp_host", "tcp_port", "bus_id", "cs_id", "irq_pin", "frequency_mhz",
    "spreading_factor", "bandwidth", "coding_rate", "preamble", "output_power", "sync_word",
)


def save_bpq_settings(form_data, cfg):
    new_cfg = dict(cfg)
    errors = []
    for tnc in bpq_services.TNCS:
        new_cfg[f"{tnc}_enabled"] = form_data.get(f"{tnc}_enabled") == "on"
        for field in BPQ_TNC_FIELDS:
            key = f"{tnc}_{field}"
            new_cfg[key] = str(form_data.get(key, cfg.get(key, ""))).strip()
    new_cfg["linbpq_path"] = str(form_data.get("linbpq_path", cfg.get("linbpq_path", ""))).strip()
    for service, spec in bpq_services.SERVICES.items():
        new_cfg[f"{service}_autostart"] = form_data.get(f"{service}_autostart") == "on"
        try:
            delay = int(form_data.get(f"{service}_delay", ""))
        except (TypeError, ValueError):
            errors.append(f"{spec['label']}: zpoždění musí být celé číslo.")
            continue
        if not 0 <= delay <= bpq_services.MAX_DELAY_SECONDS:
            errors.append(f"{spec['label']}: zpoždění musí být 0 až {bpq_services.MAX_DELAY_SECONDS} s.")
            continue
        new_cfg[f"{service}_delay"] = delay
    errors.extend(bpq_services.validate(new_cfg))
    return new_cfg, errors


BPQ_NODE_TEXT_FIELDS = ("bpq_nodecall", "bpq_nodealias", "bpq_locator", "bpq_info", "bpq_ctext",
                        "bpq_localnet", "bpq_sysop_user", "bpq_sysop_call", "bpq_bbs_call", "bpq_bbs_alias")


def save_bpq_node_settings(form_data, cfg):
    """Settings of the bpq32.cfg form; returns (new_cfg, error or None)."""
    new_cfg = dict(cfg)
    for key in BPQ_NODE_TEXT_FIELDS:
        value = str(form_data.get(key, cfg.get(key, ""))).replace("\r\n", "\n").strip()
        new_cfg[key] = value if key in {"bpq_info", "bpq_ctext", "bpq_sysop_user"} else value.upper()
    new_cfg["bpq_localnet"] = new_cfg["bpq_localnet"].lower()
    new_cfg["bpq_bbs_enabled"] = form_data.get("bpq_bbs_enabled") == "on"
    numeric = ["bpq_telnet_port", "linbpq_web_port"] + [
        f"{tnc}_{suffix}" for tnc in bpq_services.TNCS for suffix, *_ in bpq_config.AX25_FIELDS]
    for key in numeric:
        raw = str(form_data.get(key, cfg.get(key, ""))).strip()
        new_cfg[key] = int(raw) if raw.isdigit() else raw
    try:
        bpq_config.settings(new_cfg)
    except radio_apps.RadioError as exc:
        return new_cfg, str(exc)
    return new_cfg, None


@app.route("/bpq", methods=["GET", "POST"])
@requires_auth
def bpq():
    cfg = common.load_config()
    if request.method == "POST":
        require_csrf_token()
        action = request.form.get("action")
        if action == "save":
            new_cfg, errors = save_bpq_settings(request.form, cfg)
            if errors:
                for message in errors:
                    flash(message, "error")
            else:
                common.save_config(new_cfg)
                try:
                    unit_errors = bpq_services.write_units(new_cfg)
                except (OSError, radio_apps.RadioError) as exc:
                    flash(f"Nastavení uloženo, ale služby se nepodařilo zapsat: {exc}", "error")
                else:
                    for message in unit_errors:
                        flash(message, "error")
                    if not unit_errors:
                        flash("Nastavení uloženo. Běžící TNC a LinBPQ restartujte, aby změny použily.",
                              "success")
        elif action == "bpqcfg":
            new_cfg, error = save_bpq_node_settings(request.form, cfg)
            if error:
                flash(f"bpq32.cfg: {error}", "error")
            else:
                common.save_config(new_cfg)
                try:
                    path, backup = bpq_config.write(new_cfg, request.form.get("bpq_sysop_password", ""))
                except (OSError, radio_apps.RadioError) as exc:
                    flash(f"Nastavení uzlu uloženo, ale bpq32.cfg se nepodařilo zapsat: {exc}", "error")
                else:
                    flash(f"Zapsáno {path}" + (f" (původní soubor zálohován jako {os.path.basename(backup)})"
                                                if backup else "") + ". Restartujte LinBPQ.", "success")
            return redirect(url_for("bpq", _anchor="bpq-config"))
        elif action in {"start", "stop", "restart"}:
            service = request.form.get("service", "")
            if service not in bpq_services.SERVICES:
                abort(400, description="Neznámá služba.")
            label = bpq_services.SERVICES[service]["label"]
            try:
                if action == "start":
                    started = bpq_services.start(service)
                    flash(f"{label} se spouští." if started else f"{label} už běží.", "success")
                elif action == "stop":
                    bpq_services.stop(service)
                    flash(f"{label} zastaven.", "success")
                else:
                    bpq_services.restart(service)
                    flash(f"{label} se restartuje.", "success")
            except radio_apps.RadioError as exc:
                flash(f"{label}: {exc}", "error")
        else:
            abort(400, description="Neznámá akce.")
        return redirect(url_for("bpq"))

    services = []
    for service, spec in bpq_services.SERVICES.items():
        services.append({
            "name": service,
            "label": spec["label"],
            "unit": spec["unit"],
            "enabled": bpq_services.is_enabled(service, cfg),
            "status": bpq_services.status(service),
            "log": bpq_services.recent_log(service),
            "autostart": bool(cfg.get(f"{service}_autostart")),
            "delay": cfg.get(f"{service}_delay", 0),
        })
    try:
        bpq_preview, bpq_preview_error = bpq_config.preview(cfg), None
    except radio_apps.RadioError as exc:
        bpq_preview, bpq_preview_error = None, str(exc)
    bpq_cfg_path = bpq_config.config_path(cfg)
    return render_template(
        "bpq.html", cfg=cfg, services=services, tncs=bpq_services.TNCS, log_lines=BPQ_LOG_LINES,
        bpq_preview=bpq_preview, bpq_preview_error=bpq_preview_error, bpq_cfg_path=bpq_cfg_path,
        bpq_cfg_exists=os.path.exists(bpq_cfg_path), ax25_fields=bpq_config.AX25_FIELDS,
        bandwidths=bpq_services.BANDWIDTHS,
        bpq_web_url=f"http://{request.host.split(':')[0]}:{cfg.get('linbpq_web_port', 8008)}/",
    )


BPQ_LOG_LINES = (50, 200, 500)


@app.route("/bpq/log")
@requires_auth
def bpq_log():
    """Journal of one TNC/LinBPQ service for the live log panel on the BPQ tab."""
    service = request.args.get("service", "")
    if service not in bpq_services.SERVICES:
        abort(400, description="Neznámá služba.")
    try:
        lines = int(request.args.get("lines", BPQ_LOG_LINES[1]))
    except ValueError:
        lines = BPQ_LOG_LINES[1]
    lines = max(1, min(lines, BPQ_LOG_LINES[-1]))
    status = bpq_services.status(service)
    return {"service": service, "state": status["state"], "sub_state": status["sub_state"],
            "log": bpq_services.recent_log(service, lines)}


@app.route("/updates", methods=["GET", "POST"])
@requires_auth
def updates():
    page_error = None
    try:
        state = update_manager.current_state()
    except update_manager.UpdateError as exc:
        state = None
        page_error = str(exc)

    if request.method == "POST":
        require_csrf_token()
        action = request.form.get("action")
        if action == "check":
            try:
                update_check = update_manager.check_for_updates()
                try:
                    update_manager.check_restart_helper()
                except update_manager.UpdateError as exc:
                    update_check["can_update"] = False
                    update_check["blockers"].append(str(exc))
                session["update_check"] = update_check
                flash("Kontrola dokončena.", "success")
            except update_manager.UpdateError as exc:
                session.pop("update_check", None)
                page_error = str(exc)
        elif action == "apply":
            try:
                update_manager.check_restart_helper()
                updated_state = update_manager.apply_update()
                session.pop("update_check", None)
                flash(
                    f"Aplikace aktualizována na verzi {updated_state['version']} "
                    f"({updated_state['commit']}). Služby se nyní restartují.",
                    "success",
                )
                response = redirect(url_for("updates"))
                response.call_on_close(update_manager.launch_service_restart)
                return response
            except update_manager.UpdateError as exc:
                page_error = str(exc)
        else:
            abort(400, description="Neznámá akce aktualizace.")

    return render_template(
        "updates.html",
        state=state,
        update_check=session.get("update_check"),
        page_error=page_error,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8899, debug=False)
