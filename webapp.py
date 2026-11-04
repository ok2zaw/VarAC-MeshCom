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
from datetime import date, datetime, time as datetime_time, timedelta
from functools import wraps

from flask import Flask, abort, flash, request, redirect, url_for, render_template, Response, session

import bpq_services
import common
import gateway
import radio_apps
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
    recent = common.get_recent_messages(limit=10)
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
    rows = common.get_recent_messages(limit=per_page, offset=(page - 1) * per_page)
    counts = common.count_messages()
    total_pages = max((counts["total"] + per_page - 1) // per_page, 1)
    outgoing = common.get_outgoing_messages(limit=20)
    # Groups heard in the last 30 days, offered as suggestions in the send form.
    known_groups = [row["channel"] for row in common.get_rf_channel_summary(time.time() - 30 * 86400, time.time())
                    if row["kind"] == "group"]
    for row in outgoing:
        row["created_label"] = datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        row["status_label_time"] = datetime.fromtimestamp(row["status_at"]).strftime("%H:%M:%S")
    return render_template(
        "messages.html", rows=rows, page=page, total_pages=total_pages,
        cfg=cfg, outgoing=outgoing, max_length=gateway.mesh_text_limit(cfg),
        send_group=request.args.get("group", "").strip() or str(cfg.get("mesh_group", "")).strip(),
        known_groups=known_groups,
    )


def parse_date_range(default_days: int):
    """Inclusive ?start=&end= dates as (start_day, end_day, since, until)."""
    today = date.today()
    start_value = request.args.get("start", (today - timedelta(days=default_days - 1)).isoformat())
    end_value = request.args.get("end", today.isoformat())
    try:
        start_day = date.fromisoformat(start_value)
        end_day = date.fromisoformat(end_value)
    except ValueError:
        abort(400, description="Neplatný formát data; použijte RRRR-MM-DD.")
    if start_day > end_day:
        abort(400, description="Datum od musí být před datem do.")
    since = datetime.combine(start_day, datetime_time.min).timestamp()
    until = datetime.combine(end_day + timedelta(days=1), datetime_time.min).timestamp()
    return start_day, end_day, since, until


@app.route("/mesh")
@requires_auth
def mesh_messages():
    cfg = common.load_config()
    start_day, end_day, since, until = parse_date_range(default_days=7)
    channel = request.args.get("channel", "").strip().upper()
    kind = request.args.get("kind", "")
    if channel or kind not in {"group", "direct"}:
        kind = ""
    channels = common.get_rf_channel_summary(since, until)
    for row in channels:
        row["last_at_label"] = datetime.fromtimestamp(row["last_at"]).strftime("%Y-%m-%d %H:%M")
    rows = common.get_heard_rf_messages(since, until, channel, limit=500, kind=kind)
    own_callsign = str(cfg.get("callsign", "")).strip().upper()
    delivery = common.get_delivery_by_msg_id([row["msg_id"] for row in rows if row["station"] == own_callsign])
    for row in rows:
        row["observed_at_label"] = datetime.fromtimestamp(row["observed_at"]).strftime("%Y-%m-%d %H:%M:%S")
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
        row["ts_label"] = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M:%S")
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
    start_day, end_day, since, until = parse_date_range(default_days=30)
    selected_station = request.args.get("station", "").strip().upper()
    summary = common.get_station_summary(since, until)
    history_count = common.count_station_history(since, until, selected_station)
    history = common.get_station_history(since, until, selected_station, limit=500)

    own_position = common.get_own_position()
    now = time.time()
    map_stations = []
    for row in summary:
        row["presence"] = common.station_presence(row["last_seen"], now)
        row["presence_icon"], row["presence_label"] = common.STATION_PRESENCE[row["presence"]]
        row["age_label"] = common.age_label(now - row["last_seen"])
        row["first_seen_label"] = datetime.fromtimestamp(row["first_seen"]).strftime("%Y-%m-%d %H:%M:%S")
        row["last_seen_label"] = datetime.fromtimestamp(row["last_seen"]).strftime("%Y-%m-%d %H:%M:%S")
        row["direct"] = row["heard_from"] == row["station"]
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
                            station=row["station"], _anchor="station-history")})
    for row in history:
        row["observed_at_label"] = datetime.fromtimestamp(row["observed_at"]).strftime("%Y-%m-%d %H:%M:%S")

    return render_template(
        "stations.html",
        summary=summary,
        map_stations=map_stations,
        own_position=own_position,
        node_position=common.get_node_position(),
        station_paths=[{"station": row["station"], "source_path": row["source_path"],
                        "observations": row["observations"]}
                       for row in common.get_station_paths(since, until)],
        own_callsign=str(common.load_config().get("callsign", "")).strip().upper(),
        history=history,
        history_count=history_count,
        selected_station=selected_station,
        start_date=start_day.isoformat(),
        end_date=end_day.isoformat(),
    )


@app.route("/stations/position", methods=["POST"])
@requires_auth
def station_position():
    require_csrf_token()
    back = {key: request.form[key] for key in ("start", "end") if request.form.get(key)}
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
            "imap_host", "imap_user", "imap_folder", "callsign", "web_username",
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

    return render_template("config.html", cfg=cfg, saved=saved, errors=errors)


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
    mercury_gui_url = None
    mercury_gui_listening = False
    if cfg.get("radio_mercury_gui"):
        mercury_gui_url = f"http://{request.host.split(':')[0]}:{radio_apps.mercury_gui_port(cfg)}/"
        mercury_gui_listening = port_listening(radio_apps.mercury_gui_port(cfg))
    return render_template(
        "radio.html", cfg=cfg, apps=apps, mercury_port=mercury_port,
        mercury_gui_url=mercury_gui_url, mercury_gui_listening=mercury_gui_listening,
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
    try:
        port = int(form_data.get("linbpq_web_port", ""))
    except (TypeError, ValueError):
        port = 0
    if 1 <= port <= 65535:
        new_cfg["linbpq_web_port"] = port
    else:
        errors.append("Port webu LinBPQ musí být v rozsahu 1 až 65535.")
    errors.extend(bpq_services.validate(new_cfg))
    return new_cfg, errors


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
    return render_template(
        "bpq.html", cfg=cfg, services=services, tncs=bpq_services.TNCS,
        bandwidths=bpq_services.BANDWIDTHS,
        bpq_web_url=f"http://{request.host.split(':')[0]}:{cfg.get('linbpq_web_port', 8008)}/",
    )


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
