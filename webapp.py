#!/usr/bin/env python3
"""
Web UI for the MeshCom <-> Email gateway.

Pages:
  /            status dashboard (daemon alive?, counters, last 10 messages)
  /messages    full paginated message history
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
import secrets
import time
from datetime import date, datetime, time as datetime_time, timedelta
from functools import wraps

from flask import Flask, abort, flash, request, redirect, url_for, render_template, Response, session

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
        "max_mesh_payload": (20, 250),
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


@app.route("/send", methods=["POST"])
@requires_auth
def send_message():
    require_csrf_token()
    cfg = common.load_config()
    group = str(cfg.get("mesh_group", "")).strip()
    message = request.form.get("message", "").strip()
    max_length = int(cfg.get("max_mesh_payload", 150))

    if not message:
        flash("Zpráva nesmí být prázdná.", "error")
    elif len(message) > max_length:
        flash(f"Zpráva má {len(message)} znaků; limit pro MeshCom je {max_length}.", "error")
    elif not group:
        flash("Není nastavena cílová MeshCom skupina.", "error")
    else:
        try:
            gateway.send_udp_message(cfg, group, message)
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

    return redirect(url_for("messages", _anchor="send-message"))


@app.route("/messages")
@requires_auth
def messages():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 25
    rows = common.get_recent_messages(limit=per_page, offset=(page - 1) * per_page)
    counts = common.count_messages()
    total_pages = max((counts["total"] + per_page - 1) // per_page, 1)
    return render_template(
        "messages.html", rows=rows, page=page, total_pages=total_pages,
        cfg=common.load_config(),
    )


@app.route("/stations")
@requires_auth
def stations():
    today = date.today()
    default_start = (today - timedelta(days=29)).isoformat()
    start_value = request.args.get("start", default_start)
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
    selected_station = request.args.get("station", "").strip().upper()
    selected_channel = request.args.get("channel", "").strip().upper()
    summary = common.get_station_summary(since, until)
    history_count = common.count_station_history(since, until, selected_station)
    history = common.get_station_history(since, until, selected_station, limit=500)
    heard_message_count = common.count_heard_rf_messages(since, until, selected_channel)
    heard_messages = common.get_heard_rf_messages(since, until, selected_channel, limit=500)

    for row in summary:
        row["first_seen_label"] = datetime.fromtimestamp(row["first_seen"]).strftime("%Y-%m-%d %H:%M:%S")
        row["last_seen_label"] = datetime.fromtimestamp(row["last_seen"]).strftime("%Y-%m-%d %H:%M:%S")
    for row in history:
        row["observed_at_label"] = datetime.fromtimestamp(row["observed_at"]).strftime("%Y-%m-%d %H:%M:%S")
    for row in heard_messages:
        row["observed_at_label"] = datetime.fromtimestamp(row["observed_at"]).strftime("%Y-%m-%d %H:%M:%S")

    return render_template(
        "stations.html",
        summary=summary,
        history=history,
        history_count=history_count,
        heard_messages=heard_messages,
        heard_message_count=heard_message_count,
        selected_station=selected_station,
        selected_channel=selected_channel,
        start_date=start_day.isoformat(),
        end_date=end_day.isoformat(),
    )


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
            for key in ("mesh_udp_port", "smtp_port", "imap_port", "poll_interval_seconds", "max_mesh_payload"):
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
    return render_template(
        "radio.html", cfg=cfg, apps=apps, mercury_port=mercury_port,
        port_conflict=port_conflict,
        autostart_present=os.path.exists(radio_apps.AUTOSTART_FILE),
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
