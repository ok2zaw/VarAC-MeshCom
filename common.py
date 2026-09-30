"""
Shared config + database helpers for the MeshCom <-> Email gateway.

Both the gateway daemon (gateway.py) and the web UI (webapp.py) import
this module so they always see the same configuration and message log.
"""

import json
import os
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "gateway.db")

_config_lock = threading.Lock()

DEFAULT_CONFIG = {
    "mesh_node_ip": "192.168.1.50",
    "mesh_udp_port": 1799,
    "listen_ip": "0.0.0.0",
    "mesh_group": "12345",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_user": "gateway@example.com",
    "smtp_password": "changeme",
    "smtp_use_tls": True,
    "mail_from": "meshcom-gateway@example.com",
    "mail_to": "you@example.com",
    "maildir_new": "/home/pi/Maildir/new",
    "maildir_cur": "/home/pi/Maildir/cur",
    "imap_host": "",
    "imap_port": 993,
    "imap_user": "",
    "imap_password": "",
    "imap_use_ssl": True,
    "imap_folder": "INBOX",
    "poll_interval_seconds": 5,
    "max_mesh_payload": 150,
    "callsign": "OK2ZAW-10",
    "web_username": "admin",
    "web_password": "changeme",
}


def load_config() -> dict:
    with _config_lock:
        if not os.path.exists(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        return merged


def save_config(cfg: dict) -> None:
    with _config_lock:
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_PATH)


def archive_mail_file(src_path: str, archive_dir: str | None = None) -> str | None:
    if not src_path or not os.path.exists(src_path):
        return None

    destination_dir = archive_dir or os.path.join(os.path.dirname(src_path), "cur")
    os.makedirs(destination_dir, exist_ok=True)

    filename = os.path.basename(src_path)
    destination_path = os.path.join(destination_dir, filename)
    counter = 1
    while os.path.exists(destination_path):
        stem, ext = os.path.splitext(filename)
        destination_path = os.path.join(destination_dir, f"{stem}-{counter}{ext}")
        counter += 1

    os.replace(src_path, destination_path)
    return destination_path


# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                direction TEXT NOT NULL,      -- 'mesh_to_email' or 'email_to_mesh'
                peer TEXT,                    -- callsign / group id or email sender
                subject TEXT,
                body TEXT,
                status TEXT NOT NULL,         -- 'ok' or 'error'
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeat (
                name TEXT PRIMARY KEY,
                ts REAL NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def log_message(direction: str, peer: str, subject: str, body: str,
                 status: str = "ok", error: str = None) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO messages (ts, direction, peer, subject, body, status, error) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)",
            (direction, peer, subject, body, status, error),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_messages(limit: int = 50, offset: int = 0):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_messages() -> dict:
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        mesh_to_email = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE direction='mesh_to_email'"
        ).fetchone()["c"]
        email_to_mesh = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE direction='email_to_mesh'"
        ).fetchone()["c"]
        errors = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE status='error'"
        ).fetchone()["c"]
        return {
            "total": total,
            "mesh_to_email": mesh_to_email,
            "email_to_mesh": email_to_mesh,
            "errors": errors,
        }
    finally:
        conn.close()


# ------------------------------------------------------------------
# Heartbeat (so the web UI can tell if the gateway daemon is alive)
# ------------------------------------------------------------------

def set_heartbeat(name: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO heartbeat (name, ts) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET ts=excluded.ts",
            (name, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def get_heartbeat(name: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT ts FROM heartbeat WHERE name=?", (name,)
        ).fetchone()
        return row["ts"] if row else None
    finally:
        conn.close()
