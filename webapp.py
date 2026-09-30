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

import time
from functools import wraps

from flask import Flask, request, redirect, url_for, render_template, Response

import common

app = Flask(__name__)
common.init_db()


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

    if form_data.get("smtp_host") and not form_data.get("smtp_user"):
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
    hb = common.get_heartbeat("gateway")
    alive = hb is not None and (time.time() - hb) < 30
    age = round(time.time() - hb) if hb else None
    counts = common.count_messages()
    recent = common.get_recent_messages(limit=10)
    return render_template(
        "status.html",
        cfg=cfg, alive=alive, age=age, counts=counts, recent=recent,
    )


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
    )


@app.route("/config", methods=["GET", "POST"])
@requires_auth
def config_page():
    cfg = common.load_config()
    saved = False
    errors = {}
    if request.method == "POST":
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8899, debug=False)
