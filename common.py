"""
Shared config + database helpers for the MeshCom <-> Email gateway.

Both the gateway daemon (gateway.py) and the web UI (webapp.py) import
this module so they always see the same configuration and message log.
"""

import json
import math
import os
import re
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "gateway.db")
# Default paths live in the home of the account running the services.
HOME_DIR = os.path.expanduser("~")
WINE_PREFIX = os.path.join(HOME_DIR, ".wine")

_config_lock = threading.Lock()
UDP_PACKET_LOG_LIMIT = 300
UDP_PACKET_PAYLOAD_LIMIT = 2000
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
    "mesh_email_forward": False,   # "EMAIL: alias|addr text" in mesh_group mails it instead of mail_to
    "mesh_email_addresses": False, # also allow plain addresses, not only aliases
    "mesh_email_all_domains": False,
    "mesh_email_domains": "",      # comma-separated domains allowed for plain addresses
    "maildir_new": os.path.join(HOME_DIR, "Maildir", "new"),
    "maildir_cur": os.path.join(HOME_DIR, "Maildir", "cur"),
    "imap_host": "",
    "imap_port": 993,
    "imap_user": "",
    "imap_password": "",
    "imap_use_ssl": True,
    "imap_folder": "INBOX",
    "poll_interval_seconds": 5,
    "max_mesh_payload": 150,       # bytes (UTF-8); firmware 4.40a drops longer ExtUDP texts
    "max_email_parts": 4,          # an e-mail is cut after this many MeshCom messages
    "email_part_delay_seconds": 5,
    "callsign": "OK2ZAW-10",
    "display_utc": False,          # web shows times in UTC instead of the Pi's time zone
    "web_username": "admin",
    "web_password": "changeme",
    "radio_display": ":0",
    "radio_wine_prefix": WINE_PREFIX,
    "radio_vara_path": os.path.join(WINE_PREFIX, "drive_c", "VARA", "VARA.exe"),
    "radio_vara_autostart": False,
    "radio_vara_delay": 10,
    "radio_mercury_command": "mercury",
    "radio_mercury_args": "-x alsa -p 8400",
    "radio_mercury_autostart": False,
    "radio_mercury_delay": 10,
    "radio_mercury_gui": False,
    "radio_mercury_gui_port": 10088,
    "radio_mercury_no_waterfall": False,
    "radio_varac_path": os.path.join(WINE_PREFIX, "drive_c", "VarAC", "VarAC.exe"),
    "radio_varac_autostart": False,
    "radio_varac_delay": 40,
    # LoRa TNC defaults follow the OK2ZAW dual-TNC board (IRQ 5 / 19).
    # The frequency is left empty on purpose so nothing transmits until set.
    **{
        f"{tnc}_{key}": value
        for tnc, irq_pin, cs_id, tcp_port in (("tnc1", 5, 0, 10001), ("tnc2", 19, 1, 10002))
        for key, value in {
            "enabled": False,
            "autostart": False,
            "delay": 0,
            "tcp_host": "127.0.0.1",
            "tcp_port": tcp_port,
            "bus_id": 0,
            "cs_id": cs_id,
            "irq_pin": irq_pin,
            "frequency_mhz": "",
            "spreading_factor": 12,
            "bandwidth": 125000,
            "coding_rate": 5,
            "preamble": 8,
            "output_power": 15,
            "sync_word": "0x12",
        }.items()
    },
    "linbpq_path": os.path.join(HOME_DIR, "linbpq", "linbpq"),
    "linbpq_autostart": False,
    "linbpq_delay": 15,
    "linbpq_web_port": 8008,
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
            CREATE TABLE IF NOT EXISTS node_position (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_aliases (
                alias TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_position (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outgoing_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                source TEXT NOT NULL,         -- 'web' or 'email'
                dst TEXT NOT NULL,
                body TEXT NOT NULL,
                msg_id TEXT,                  -- assigned by the node, learned from its echo
                seq TEXT,                     -- {NNN} of direct messages, used by text ACKs
                status TEXT NOT NULL,
                status_at REAL NOT NULL,
                ack_from TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outgoing_messages_msg_id ON outgoing_messages(msg_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS udp_packets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                direction TEXT NOT NULL,      -- 'rx' or 'tx'
                peer TEXT,
                size INTEGER NOT NULL,
                status TEXT,
                payload TEXT NOT NULL
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
                fw_sub TEXT,
                src_type TEXT NOT NULL DEFAULT 'lora'
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(heard_rf_messages)")}
        if "src_type" not in columns:
            conn.execute("ALTER TABLE heard_rf_messages ADD COLUMN src_type TEXT NOT NULL DEFAULT 'lora'")
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


def record_udp_packet(direction: str, peer: str, data: bytes, status: str = None) -> None:
    """Keep the raw datagram for the UDP monitor (only the newest are retained)."""
    if direction not in {"rx", "tx"}:
        raise ValueError("direction must be 'rx' or 'tx'")
    payload = data.decode("utf-8", errors="replace")[:UDP_PACKET_PAYLOAD_LIMIT]
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO udp_packets (ts, direction, peer, size, status, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), direction, peer, len(data), status, payload),
        )
        conn.execute("DELETE FROM udp_packets WHERE id <= ?",
                     (cursor.lastrowid - UDP_PACKET_LOG_LIMIT,))
        conn.commit()
    finally:
        conn.close()


def get_udp_packets(limit: int = 100, direction: str = None) -> list[dict]:
    query = "SELECT * FROM udp_packets"
    params = []
    if direction in {"rx", "tx"}:
        query += " WHERE direction = ?"
        params.append(direction)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), UDP_PACKET_LOG_LIMIT)))
    conn = get_db()
    try:
        return [dict(row) for row in conn.execute(query, params).fetchall()]
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


def _packet_position(packet: dict) -> tuple:
    """Signed (latitude, longitude) from MeshCom lat/lat_dir, long/long_dir; None if unusable."""
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
    return latitude, longitude


def record_own_position(packet: dict, own_callsign: str = "") -> bool:
    """Keep the position the own node beacons (src_type "node"), for the map and distances."""
    source = str(packet.get("src", "")).split(",")[0].strip().upper()
    if (packet.get("type") != "pos" or packet.get("src_type") != "node"
            or not source or source != str(own_callsign or "").strip().upper()):
        return False
    latitude, longitude = _packet_position(packet)
    if latitude is None or longitude is None:
        return False
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO node_position (id, latitude, longitude, altitude, updated_at) "
            "VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET latitude=excluded.latitude, "
            "longitude=excluded.longitude, altitude=excluded.altitude, updated_at=excluded.updated_at",
            (latitude, longitude, _station_number(packet, "alt"), time.time()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_node_position() -> dict | None:
    """Position from the own node's beacon (None until one arrives)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM node_position WHERE id = 1").fetchone()
        return dict(row) | {"source": "node"} if row else None
    finally:
        conn.close()


def get_manual_position() -> dict | None:
    """Position picked on the station map (None if not set)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM manual_position WHERE id = 1").fetchone()
        return dict(row) | {"altitude": None, "source": "manual"} if row else None
    finally:
        conn.close()


def get_own_position() -> dict | None:
    """Reference point for the map and distances: the map pick wins over the node beacon."""
    return get_manual_position() or get_node_position()


def set_manual_position(latitude: float, longitude: float) -> None:
    latitude, longitude = float(latitude), float(longitude)
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Souřadnice jsou mimo rozsah.")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO manual_position (id, latitude, longitude, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET latitude=excluded.latitude, "
            "longitude=excluded.longitude, updated_at=excluded.updated_at",
            (round(latitude, 6), round(longitude, 6), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


EMAIL_ALIAS = re.compile(r"[A-Z0-9][A-Z0-9._/-]{0,31}")
EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def get_email_aliases() -> dict[str, str]:
    """Aliases for "EMAIL: <alias>" mesh messages, uppercase alias -> address."""
    conn = get_db()
    try:
        return {row["alias"]: row["email"] for row in conn.execute("SELECT alias, email FROM email_aliases ORDER BY alias")}
    finally:
        conn.close()


def set_email_alias(alias: str, email: str) -> str:
    alias, email = str(alias).strip().upper(), str(email).strip()
    if not EMAIL_ALIAS.fullmatch(alias):
        raise ValueError("Alias musí mít 1–32 znaků: písmena, číslice, tečka, pomlčka, podtržítko nebo lomítko.")
    if not EMAIL_ADDRESS.fullmatch(email):
        raise ValueError(f"Neplatná e-mailová adresa {email}.")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO email_aliases (alias, email, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET email=excluded.email, updated_at=excluded.updated_at",
            (alias, email, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return alias


def delete_email_alias(alias: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM email_aliases WHERE alias = ?", (str(alias).strip().upper(),))
        conn.commit()
    finally:
        conn.close()


def clear_manual_position() -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM manual_position WHERE id = 1")
        conn.commit()
    finally:
        conn.close()


# Station presence from the time since it was last heard (same defaults as MeshCom WebDesk).
STATION_RECENT_SECONDS = 45 * 60
STATION_UNCERTAIN_SECONDS = 120 * 60
STATION_PRESENCE = {
    "recent": ("🟢", "aktivní"),
    "uncertain": ("🟡", "nejisté"),
    "gone": ("⚪", "pryč"),
}


def station_presence(last_seen: float, now: float = None) -> str:
    age = (time.time() if now is None else now) - last_seen
    if age < STATION_RECENT_SECONDS:
        return "recent"
    return "uncertain" if age < STATION_UNCERTAIN_SECONDS else "gone"


def age_label(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"před {seconds // 60} min"
    if seconds < 86400:
        return f"před {seconds // 3600} h {seconds % 3600 // 60} min"
    return f"před {seconds // 86400} d"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine, mean Earth radius)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((phi2 - phi1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


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

    latitude, longitude = _packet_position(packet)

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


# Trailing "{034" is the node's per-message sequence number, not text.
_SEQUENCE_SUFFIX = re.compile(r"\{\d{3}\}?$")
# Delivery receipts: "ack034", "OK1ABC-1:ack034", "OK1ABC-1 :rej034".
_ACK_TEXT = re.compile(r"^(?:\S+\s*:)?(?:ack|rej)\d+$", re.IGNORECASE)
RF_MESSAGE_DUPLICATE_WINDOW = 24 * 3600


def clean_message_text(text: str) -> str:
    return _SEQUENCE_SUFFIX.sub("", text.strip()).strip()


def channel_kind(channel: str) -> str:
    """'broadcast' for *, 'group' for a numeric group, otherwise 'direct'."""
    if channel == "*":
        return "broadcast"
    return "group" if channel.isdigit() else "direct"


def _normalized_msg_id(value) -> str | None:
    msg_id = str(value or "").strip().upper()[:32]
    if not msg_id:
        return None
    try:
        # Firmware retries of the same message count the attempt in bits 10-11.
        return f"{int(msg_id, 16) & ~0xC00:08X}"
    except ValueError:
        return msg_id


def record_rf_message(packet: dict, own_callsign: str = "") -> bool:
    """Store a mesh text message for the per-group view; False if skipped.

    Stores messages heard over LoRa or via the internet gateway (src_type
    "udp") and the node's echo of our own messages.
    Duplicates (the node reports retries with the same msg_id), delivery
    receipts and {CET} time beacons are skipped.
    """
    body = packet.get("msg")
    destination_path = packet.get("dst")
    source_path = packet.get("src")
    src_type = packet.get("src_type") or "lora"
    if (
        packet.get("type") != "msg"
        or src_type not in {"lora", "node", "udp"}
        or not isinstance(body, str)
        or not isinstance(destination_path, str)
        or not isinstance(source_path, str)
    ):
        return False

    source_parts = [part.strip().upper() for part in source_path.split(",") if part.strip()]
    destination_parts = [part.strip().upper() for part in destination_path.split(",") if part.strip()]
    body = clean_message_text(body)
    if not source_parts or not destination_parts or not body:
        return False
    if _ACK_TEXT.match(body) or body.startswith("{CET}"):
        return False

    station = source_parts[0]
    own_callsign = str(own_callsign or "").strip().upper()
    # src_type "node" repeats frames the node relays; only its echo of our
    # own messages is new information.
    if src_type == "node" and station != own_callsign:
        return False

    now = time.time()
    msg_id = _normalized_msg_id(packet.get("msg_id"))
    channel = destination_parts[-1]
    conn = get_db()
    try:
        if msg_id:
            duplicate = conn.execute(
                "SELECT 1 FROM heard_rf_messages WHERE station = ? AND msg_id = ? AND observed_at >= ?",
                (station, msg_id, now - RF_MESSAGE_DUPLICATE_WINDOW),
            ).fetchone()
        else:
            duplicate = conn.execute(
                "SELECT 1 FROM heard_rf_messages WHERE station = ? AND channel = ? AND body = ? "
                "AND observed_at >= ?",
                (station, channel, body[:2048], now - 60),
            ).fetchone()
        if duplicate:
            return False

        observation = {
            "observed_at": now,
            "station": station,
            "source_path": ",".join(source_parts),
            "destination_path": ",".join(destination_parts),
            "channel": channel,
            "body": body[:2048],
            "msg_id": msg_id,
            "rssi": _station_number(packet, "rssi", integer=True) if src_type == "lora" else None,
            "snr": _station_number(packet, "snr", integer=True) if src_type == "lora" else None,
            "firmware": str(packet["firmware"])[:32] if packet.get("firmware") is not None else None,
            "fw_sub": str(packet["fw_sub"])[:8] if packet.get("fw_sub") is not None else None,
            "src_type": src_type,
        }
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


# Delivery states of our own messages, in rising order of certainty. The
# node's JSON ack (firmware 4.40a) uses status 0 heard, 1 gateway, 2 ACK,
# 3 failed, 4 held by a store-and-forward mailbox.
DELIVERY_STATES = {
    "sent": (0, "předáno uzlu"),
    "echo": (1, "uzel vysílá"),
    "heard": (2, "slyšel soused"),
    "held": (3, "drží schránka"),
    "failed": (4, "nedoručeno"),
    "gateway": (5, "doručeno přes bránu"),
    "delivered": (5, "potvrzeno příjemcem"),
}
_ACK_STATUS_CODES = {0: "heard", 1: "gateway", 2: "delivered", 3: "failed", 4: "held"}
_SEQUENCE_NUMBER = re.compile(r"\{(\d{3})\}?$")
_ACK_PARTS = re.compile(r"^(?:(\S+?)\s*:)?(ack|rej)(\d+)$", re.IGNORECASE)
OUTGOING_ECHO_WINDOW = 600


def delivery_label(status: str) -> str:
    return DELIVERY_STATES.get(status, (0, status))[1]


def record_outgoing_message(source: str, dst: str, body: str) -> int:
    now = time.time()
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO outgoing_messages (created_at, source, dst, body, status, status_at) "
            "VALUES (?, ?, ?, ?, 'sent', ?)",
            (now, source, str(dst).strip().upper(), body, now),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _raise_delivery_status(conn, row_id: int, status: str, ack_from: str = None) -> bool:
    row = conn.execute("SELECT status FROM outgoing_messages WHERE id = ?", (row_id,)).fetchone()
    if row is None or DELIVERY_STATES[status][0] <= DELIVERY_STATES.get(row["status"], (0,))[0]:
        return False
    conn.execute(
        "UPDATE outgoing_messages SET status = ?, status_at = ?, ack_from = COALESCE(?, ack_from) "
        "WHERE id = ?",
        (status, time.time(), ack_from, row_id),
    )
    return True


def track_delivery(packet: dict, own_callsign: str = "") -> str | None:
    """Update our sent messages from a node echo or an ACK; returns the new state.

    1. The node echoes every message it sends for us (src_type "node",
       src = own callsign); the echo carries the msg_id that later ACKs use.
    2. A relay heard repeating it (src_type "lora", src = own callsign).
    3. Firmware 4.40a: {"type":"ack","msg_id":..,"status":0-4,"from":..}.
    4. Older firmware: a text ACK "ack034" / "OK2ZAW-10:ack034" to us.
    """
    own_callsign = str(own_callsign or "").strip().upper()
    msg_id = _normalized_msg_id(packet.get("msg_id"))
    conn = get_db()
    try:
        status = None
        if packet.get("type") == "ack":
            code = packet.get("status")
            status = _ACK_STATUS_CODES.get(code) if isinstance(code, int) else None
            if not status or not msg_id:
                return None
            row = conn.execute("SELECT id FROM outgoing_messages WHERE msg_id = ? ORDER BY id DESC",
                               (msg_id,)).fetchone()
            sender = str(packet.get("from") or "").strip().upper() or None
            changed = row is not None and _raise_delivery_status(conn, row["id"], status, sender)
        elif packet.get("type") == "msg" and isinstance(packet.get("msg"), str):
            source = str(packet.get("src", "")).split(",")[0].strip().upper()
            destination = str(packet.get("dst", "")).split(",")[-1].strip().upper()
            text = packet["msg"].strip()
            ack = _ACK_PARTS.match(text)
            if ack and source != own_callsign:
                target = (ack.group(1) or destination).strip().upper()
                if target != own_callsign:
                    return None
                status = "failed" if ack.group(2).lower() == "rej" else "delivered"
                row = conn.execute(
                    "SELECT id FROM outgoing_messages WHERE seq = ? AND dst = ? AND created_at >= ? "
                    "ORDER BY id DESC",
                    (f"{int(ack.group(3)) % 1000:03d}", source, time.time() - 86400),
                ).fetchone()
                changed = row is not None and _raise_delivery_status(conn, row["id"], status, source)
            elif source == own_callsign and packet.get("src_type") == "node":
                row = conn.execute(
                    "SELECT id FROM outgoing_messages WHERE msg_id IS NULL AND dst = ? AND body = ? "
                    "AND created_at >= ? ORDER BY id DESC",
                    (destination, clean_message_text(text), time.time() - OUTGOING_ECHO_WINDOW),
                ).fetchone()
                if row is None:
                    return None
                sequence = _SEQUENCE_NUMBER.search(text)
                conn.execute("UPDATE outgoing_messages SET msg_id = ?, seq = ? WHERE id = ?",
                             (msg_id, sequence.group(1) if sequence else None, row["id"]))
                status = "echo"
                changed = _raise_delivery_status(conn, row["id"], status) or True
            elif source == own_callsign and msg_id:
                status = "heard"
                row = conn.execute("SELECT id FROM outgoing_messages WHERE msg_id = ? ORDER BY id DESC",
                                   (msg_id,)).fetchone()
                changed = row is not None and _raise_delivery_status(conn, row["id"], status)
            else:
                return None
        else:
            return None
        conn.commit()
        return status if changed else None
    finally:
        conn.close()


def get_outgoing_messages(limit: int = 25) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM outgoing_messages ORDER BY id DESC LIMIT ?",
                            (max(1, min(int(limit), 500)),)).fetchall()
    finally:
        conn.close()
    return [dict(row, status_label=delivery_label(row["status"])) for row in rows]


def get_delivery_by_msg_id(msg_ids: list[str]) -> dict:
    ids = [msg_id for msg_id in set(msg_ids) if msg_id]
    if not ids:
        return {}
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT msg_id, status, ack_from FROM outgoing_messages WHERE msg_id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
    finally:
        conn.close()
    return {row["msg_id"]: dict(row, status_label=delivery_label(row["status"])) for row in rows}


def _rf_message_filter(since: float, until: float, channel: str, kind: str) -> tuple[str, list]:
    where = "observed_at >= ? AND observed_at < ?"
    params = [since, until]
    if channel:
        where += " AND channel = ?"
        params.append(channel.strip().upper())
    elif kind == "direct":
        where += " AND channel <> '*' AND channel GLOB '*[^0-9]*'"
    elif kind == "group":
        where += " AND channel NOT GLOB '*[^0-9]*' AND channel <> ''"
    return where, params


def get_rf_channel_summary(since: float, until: float) -> list[dict]:
    """Message count and last activity per destination (group, * or callsign)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT channel, COUNT(*) AS messages, MAX(observed_at) AS last_at "
            "FROM heard_rf_messages WHERE observed_at >= ? AND observed_at < ? "
            "GROUP BY channel ORDER BY last_at DESC",
            (since, until),
        ).fetchall()
    finally:
        conn.close()
    result = [dict(row, kind=channel_kind(row["channel"])) for row in rows]
    order = {"broadcast": 0, "group": 1, "direct": 2}
    result.sort(key=lambda row: (order[row["kind"]],
                                 int(row["channel"]) if row["kind"] == "group" else 0,
                                 row["channel"]))
    return result


def get_heard_rf_messages(since: float, until: float, channel: str = "",
                          limit: int = 500, kind: str = "") -> list[dict]:
    where, params = _rf_message_filter(since, until, channel, kind)
    query = f"SELECT * FROM heard_rf_messages WHERE {where} ORDER BY observed_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_heard_rf_messages(since: float, until: float, channel: str = "", kind: str = "") -> int:
    where, params = _rf_message_filter(since, until, channel, kind)
    query = f"SELECT COUNT(*) FROM heard_rf_messages WHERE {where}"

    conn = get_db()
    try:
        return conn.execute(query, params).fetchone()[0]
    finally:
        conn.close()


def get_station_paths(since: float, until: float, limit: int = 1000) -> list[dict]:
    """Distinct RF paths (origin,relays...,heard_from) per station in the period, newest first."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT station, source_path, COUNT(*) AS observations, MAX(observed_at) AS last_seen "
            "FROM station_observations WHERE observed_at >= ? AND observed_at < ? "
            "GROUP BY station, source_path ORDER BY last_seen DESC LIMIT ?",
            (since, until, limit),
        ).fetchall()
        return [dict(row) for row in rows]
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
