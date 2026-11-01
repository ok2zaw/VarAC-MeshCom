"""
Shared config + database helpers for the MeshCom <-> Email gateway.

Both the gateway daemon (gateway.py) and the web UI (webapp.py) import
this module so they always see the same configuration and message log.
"""

import json
import math
import os
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "gateway.db")

_config_lock = threading.Lock()
_ENV_PREFIX = "MESHCOM_"

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


def _parse_env_value(raw: str):
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    elif value.startswith("'") and value.endswith("'"):
        value = value[1:-1]

    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"yes", "no"}:
        return lowered == "yes"
    if value and value.isdigit():
        return int(value)
    try:
        return int(value)
    except ValueError:
        return value


def _normalize_config_key(raw_key: str) -> str:
    key = raw_key.strip()
    if key.upper().startswith(_ENV_PREFIX):
        key = key[len(_ENV_PREFIX):]
    return key.lower()


def _read_dotenv(path: str | None = None) -> dict:
    dotenv_path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(dotenv_path):
        return {}

    overrides = {}
    with open(dotenv_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            overrides[_normalize_config_key(key)] = value
    return overrides


def _env_overrides() -> dict:
    overrides = {}
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        config_key = _normalize_config_key(key)
        if config_key:
            overrides[config_key] = _parse_env_value(value)
    return overrides


def load_config() -> dict:
    with _config_lock:
        if not os.path.exists(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)

        dotenv_cfg = _read_dotenv()
        merged.update({k: _parse_env_value(v) for k, v in dotenv_cfg.items()})

        env_cfg = _env_overrides()
        merged.update(env_cfg)
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


def ensure_maildir_layout(maildir_root: str) -> dict:
    root = maildir_root or os.path.join(BASE_DIR, "Maildir")
    os.makedirs(root, exist_ok=True)
    layout = {
        "root": root,
        "new": os.path.join(root, "new"),
        "cur": os.path.join(root, "cur"),
        "tmp": os.path.join(root, "tmp"),
    }
    for path in layout.values():
        os.makedirs(path, exist_ok=True)
    return layout


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS udp_activity (
                direction TEXT PRIMARY KEY,
                datagrams INTEGER NOT NULL DEFAULT 0,
                last_at REAL,
                last_peer TEXT,
                last_status TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS station_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at REAL NOT NULL,
                station TEXT NOT NULL,
                heard_from TEXT NOT NULL,
                source_path TEXT NOT NULL,
                src_type TEXT NOT NULL,
                packet_type TEXT NOT NULL,
                msg_id TEXT,
                rssi INTEGER,
                snr INTEGER,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                battery_percent INTEGER,
                lora_mod INTEGER,
                max_hop INTEGER,
                firmware TEXT,
                fw_sub TEXT,
                temp1 REAL,
                temp2 REAL,
                humidity REAL,
                qfe REAL,
                qnh REAL,
                gas REAL,
                co2 REAL,
                pressure_alt REAL,
                digital_inputs TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_station_observations_time "
            "ON station_observations(observed_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_station_observations_station_time "
            "ON station_observations(station, observed_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS heard_rf_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at REAL NOT NULL,
                station TEXT NOT NULL,
                source_path TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                channel TEXT NOT NULL,
                body TEXT NOT NULL,
                msg_id TEXT,
                rssi INTEGER,
                snr INTEGER,
                firmware TEXT,
                fw_sub TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_heard_rf_messages_time "
            "ON heard_rf_messages(observed_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_heard_rf_messages_channel_time "
            "ON heard_rf_messages(channel, observed_at DESC)"
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
        web_to_mesh = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE direction='web_to_mesh'"
        ).fetchone()["c"]
        errors = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE status='error'"
        ).fetchone()["c"]
        return {
            "total": total,
            "mesh_to_email": mesh_to_email,
            "email_to_mesh": email_to_mesh,
            "web_to_mesh": web_to_mesh,
            "errors": errors,
        }
    finally:
        conn.close()


def record_udp_activity(direction: str, peer: str = None, status: str = "ok",
                        error: str = None, count_datagram: bool = True) -> None:
    if direction not in {"rx", "tx"}:
        raise ValueError("direction must be 'rx' or 'tx'")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO udp_activity "
            "(direction, datagrams, last_at, last_peer, last_status, last_error) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(direction) DO UPDATE SET "
            "datagrams=udp_activity.datagrams + excluded.datagrams, "
            "last_at=excluded.last_at, last_peer=excluded.last_peer, "
            "last_status=excluded.last_status, last_error=excluded.last_error",
            (direction, int(count_datagram), time.time(), peer, status, error),
        )
        conn.commit()
    finally:
        conn.close()


def get_udp_activity() -> dict:
    result = {
        direction: {
            "datagrams": 0,
            "last_at": None,
            "last_peer": None,
            "last_status": None,
            "last_error": None,
        }
        for direction in ("rx", "tx")
    }
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM udp_activity").fetchall()
        for row in rows:
            result[row["direction"]] = dict(row)
        return result
    finally:
        conn.close()


def _station_number(packet: dict, key: str, integer: bool = False):
    value = packet.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if integer and not -(2 ** 31) <= number < 2 ** 31:
        return None
    return int(number) if integer else number


def record_station_observation(packet: dict, own_callsign: str = "") -> bool:
    packet_type = packet.get("type")
    if packet.get("src_type") != "lora" or not isinstance(packet_type, str) or packet_type not in {"pos", "msg", "tele"}:
        return False

    source_path = packet.get("src")
    if not isinstance(source_path, str):
        return False
    path = [part.strip().upper() for part in source_path.split(",") if part.strip()]
    own_callsign = str(own_callsign or "").strip().upper()
    if not path or path[0] == own_callsign:
        return False

    latitude = _station_number(packet, "lat")
    latitude_direction = str(packet.get("lat_dir", "")).upper()
    if latitude is not None and latitude_direction in {"N", "S"}:
        latitude = abs(latitude) * (-1 if latitude_direction == "S" else 1)
    else:
        latitude = None

    longitude = _station_number(packet, "long")
    longitude_direction = str(packet.get("long_dir", "")).upper()
    if longitude is not None and longitude_direction in {"E", "W"}:
        longitude = abs(longitude) * (-1 if longitude_direction == "W" else 1)
    else:
        longitude = None

    if latitude is not None and not -90 <= latitude <= 90:
        latitude = None
    if longitude is not None and not -180 <= longitude <= 180:
        longitude = None
    if latitude == 0 and longitude == 0:
        latitude = None
        longitude = None

    observation = {
        "observed_at": time.time(),
        "station": path[0],
        "heard_from": path[-1],
        "source_path": ",".join(path),
        "src_type": "lora",
        "packet_type": packet_type,
        "msg_id": str(packet.get("msg_id", ""))[:32] or None,
        "rssi": _station_number(packet, "rssi", integer=True),
        "snr": _station_number(packet, "snr", integer=True),
        "latitude": latitude,
        "longitude": longitude,
        "altitude": _station_number(packet, "alt"),
        "battery_percent": _station_number(packet, "batt", integer=True),
        "lora_mod": _station_number(packet, "lora_mod", integer=True),
        "max_hop": _station_number(packet, "max_hop", integer=True),
        "firmware": str(packet["firmware"])[:32] if packet.get("firmware") is not None else None,
        "fw_sub": str(packet["fw_sub"])[:8] if packet.get("fw_sub") is not None else None,
        "temp1": _station_number(packet, "temp1"),
        "temp2": _station_number(packet, "temp2"),
        "humidity": _station_number(packet, "hum"),
        "qfe": _station_number(packet, "qfe"),
        "qnh": _station_number(packet, "qnh"),
        "gas": _station_number(packet, "gas"),
        "co2": _station_number(packet, "co2"),
        "pressure_alt": _station_number(packet, "pressure_alt"),
        "digital_inputs": str(packet["din"])[:16] if packet.get("din") is not None else None,
    }

    conn = get_db()
    try:
        columns = ", ".join(observation)
        placeholders = ", ".join(f":{column}" for column in observation)
        conn.execute(
            f"INSERT INTO station_observations ({columns}) VALUES ({placeholders})",
            observation,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def record_other_rf_message(packet: dict, own_callsign: str = "",
                            configured_group: str = "") -> bool:
    body = packet.get("msg")
    destination_path = packet.get("dst")
    source_path = packet.get("src")
    if (
        packet.get("src_type") != "lora"
        or packet.get("type") != "msg"
        or not isinstance(body, str)
        or not body.strip()
        or not isinstance(destination_path, str)
        or not isinstance(source_path, str)
    ):
        return False

    source_parts = [part.strip().upper() for part in source_path.split(",") if part.strip()]
    destination_parts = [part.strip().upper() for part in destination_path.split(",") if part.strip()]
    if not source_parts or not destination_parts:
        return False

    station = source_parts[0]
    configured_group = str(configured_group or "").strip().upper()
    channel = destination_parts[-1]
    if station == str(own_callsign or "").strip().upper() or channel == configured_group:
        return False

    observation = {
        "observed_at": time.time(),
        "station": station,
        "source_path": ",".join(source_parts),
        "destination_path": ",".join(destination_parts),
        "channel": channel,
        "body": body[:2048],
        "msg_id": str(packet.get("msg_id", ""))[:32] or None,
        "rssi": _station_number(packet, "rssi", integer=True),
        "snr": _station_number(packet, "snr", integer=True),
        "firmware": str(packet["firmware"])[:32] if packet.get("firmware") is not None else None,
        "fw_sub": str(packet["fw_sub"])[:8] if packet.get("fw_sub") is not None else None,
    }
    conn = get_db()
    try:
        columns = ", ".join(observation)
        placeholders = ", ".join(f":{column}" for column in observation)
        conn.execute(
            f"INSERT INTO heard_rf_messages ({columns}) VALUES ({placeholders})",
            observation,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_heard_rf_messages(since: float, until: float, channel: str = "",
                          limit: int = 500) -> list[dict]:
    query = (
        "SELECT * FROM heard_rf_messages "
        "WHERE observed_at >= ? AND observed_at < ?"
    )
    params = [since, until]
    if channel:
        query += " AND channel = ?"
        params.append(channel.strip().upper())
    query += " ORDER BY observed_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_heard_rf_messages(since: float, until: float, channel: str = "") -> int:
    query = "SELECT COUNT(*) FROM heard_rf_messages WHERE observed_at >= ? AND observed_at < ?"
    params = [since, until]
    if channel:
        query += " AND channel = ?"
        params.append(channel.strip().upper())

    conn = get_db()
    try:
        return conn.execute(query, params).fetchone()[0]
    finally:
        conn.close()


def get_station_summary(since: float, until: float, limit: int = 500) -> list[dict]:
    query = """
        WITH filtered AS (
            SELECT * FROM station_observations
            WHERE observed_at >= ? AND observed_at < ?
        ),
        stats AS (
            SELECT station, COUNT(*) AS observations,
                   MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen
            FROM filtered GROUP BY station
        ),
        latest AS (
            SELECT item.* FROM filtered AS item
            JOIN (SELECT station, MAX(id) AS id FROM filtered GROUP BY station) AS newest
              ON newest.id = item.id
        ),
        latest_signal AS (
            SELECT item.* FROM filtered AS item
            JOIN (
                SELECT station, MAX(id) AS id FROM filtered
                WHERE rssi IS NOT NULL OR snr IS NOT NULL GROUP BY station
            ) AS newest ON newest.id = item.id
        ),
        latest_position AS (
            SELECT item.* FROM filtered AS item
            JOIN (
                SELECT station, MAX(id) AS id FROM filtered
                WHERE packet_type = 'pos' AND latitude IS NOT NULL AND longitude IS NOT NULL
                GROUP BY station
            ) AS newest ON newest.id = item.id
        ),
        latest_telemetry AS (
            SELECT item.* FROM filtered AS item
            JOIN (
                SELECT station, MAX(id) AS id FROM filtered
                WHERE packet_type = 'tele' GROUP BY station
            ) AS newest ON newest.id = item.id
        )
        SELECT stats.station, stats.observations, stats.first_seen, stats.last_seen,
               latest.heard_from, latest.source_path, latest.packet_type AS last_packet_type,
               latest_signal.rssi, latest_signal.snr,
               latest_position.latitude, latest_position.longitude,
               latest_position.altitude, latest_position.battery_percent,
               latest_position.lora_mod, latest_position.max_hop,
               latest_position.firmware, latest_position.fw_sub,
               latest_telemetry.temp1, latest_telemetry.temp2,
               latest_telemetry.humidity, latest_telemetry.qfe,
               latest_telemetry.qnh, latest_telemetry.gas,
               latest_telemetry.co2, latest_telemetry.pressure_alt,
               latest_telemetry.digital_inputs
        FROM stats
        LEFT JOIN latest ON latest.station = stats.station
        LEFT JOIN latest_signal ON latest_signal.station = stats.station
        LEFT JOIN latest_position ON latest_position.station = stats.station
        LEFT JOIN latest_telemetry ON latest_telemetry.station = stats.station
        ORDER BY stats.last_seen DESC
        LIMIT ?
    """
    conn = get_db()
    try:
        rows = conn.execute(query, (since, until, max(1, min(limit, 1000)))).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_station_history(since: float, until: float, station: str = "",
                        limit: int = 500, offset: int = 0) -> list[dict]:
    query = (
        "SELECT * FROM station_observations "
        "WHERE observed_at >= ? AND observed_at < ?"
    )
    params = [since, until]
    if station:
        query += " AND station = ?"
        params.append(station.strip().upper())
    query += " ORDER BY observed_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend((max(1, min(limit, 1000)), max(0, offset)))

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_station_history(since: float, until: float, station: str = "") -> int:
    query = "SELECT COUNT(*) FROM station_observations WHERE observed_at >= ? AND observed_at < ?"
    params = [since, until]
    if station:
        query += " AND station = ?"
        params.append(station.strip().upper())

    conn = get_db()
    try:
        return conn.execute(query, params).fetchone()[0]
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
