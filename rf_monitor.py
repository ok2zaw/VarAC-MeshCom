"""Versioned firmware monitor ingestion and persistent RF event archive."""

import json
import re
import time

import mesh_frame

EVENT_TYPES = frozenset({"rf_rx", "node_status"})
_UINT32_MAX = 2**32 - 1
_STATUS_COUNTERS = (
    "heap_bytes", "reset_reason", "rx_seen", "rx_dropped", "rx_suppressed",
    "queue_depth", "queue_high_water", "udp_failures", "format_failures",
)


def init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rf_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at REAL NOT NULL,
            peer TEXT NOT NULL,
            packet_type TEXT NOT NULL,
            node TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            event_seq INTEGER,
            uptime_ms INTEGER NOT NULL,
            raw BLOB,
            rssi INTEGER,
            snr INTEGER,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rf_events_received ON rf_events(received_at);
        CREATE INDEX IF NOT EXISTS idx_rf_events_node_time ON rf_events(node, received_at, id);
        CREATE INDEX IF NOT EXISTS idx_rf_events_session ON rf_events(node, boot_id, event_seq);
        CREATE INDEX IF NOT EXISTS idx_rf_events_type_id ON rf_events(packet_type, id DESC);
        CREATE TABLE IF NOT EXISTS rf_frame_details (
            event_id INTEGER PRIMARY KEY,
            decoder_version INTEGER NOT NULL,
            frame_type TEXT NOT NULL,
            decode_status TEXT NOT NULL,
            source TEXT,
            destination TEXT,
            msg_id TEXT,
            details_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rf_details_source ON rf_frame_details(source, event_id);
        CREATE INDEX IF NOT EXISTS idx_rf_details_destination ON rf_frame_details(destination, event_id);
    """)
    # Upgrade older archives without changing a single original event or payload.
    # Fixed upper boundary and bounded batches also allow the other process to write.
    upper = conn.execute("SELECT MAX(id) FROM rf_events").fetchone()[0] or 0
    after = 0
    while True:
        rows = conn.execute(
            "SELECT e.id, e.raw FROM rf_events e LEFT JOIN rf_frame_details d ON d.event_id=e.id "
            "WHERE e.packet_type='rf_rx' AND e.id>? AND e.id<=? "
            "AND (d.event_id IS NULL OR d.decoder_version<>?) ORDER BY e.id LIMIT 500",
            (after, upper, mesh_frame.DECODER_VERSION),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            _save_details(conn, row["id"], row["raw"])
        conn.commit()
        after = rows[-1]["id"]


def _save_details(conn, event_id, raw):
    parsed = mesh_frame.decode_frame(raw)
    conn.execute(
        "INSERT OR REPLACE INTO rf_frame_details "
        "(event_id,decoder_version,frame_type,decode_status,source,destination,msg_id,details_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, mesh_frame.DECODER_VERSION, parsed["frame_type"], parsed["decode_status"],
         parsed["source"].upper() if parsed["source"] else None,
         parsed["destination"].upper() if parsed["destination"] else None,
         parsed["msg_id"], json.dumps(parsed, ensure_ascii=False)),
    )


def _connect():
    import common
    return common.get_db()


def _integer(packet, key, low=0, high=_UINT32_MAX):
    value = packet.get(key)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{key}: neplatné celé číslo")
    return value


def validate_event(packet):
    """Reject partial/truncated records instead of presenting them as captures."""
    if type(packet.get("monitor_version")) is not int or packet["monitor_version"] != 1:
        raise ValueError("nepodporovaná verze RF monitoru")
    kind = packet.get("type")
    if not isinstance(kind, str) or kind not in EVENT_TYPES:
        raise ValueError("nepodporovaný typ RF události")
    node = packet.get("node")
    if not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,20}", node):
        raise ValueError("neplatná značka uzlu")
    boot = packet.get("boot_id")
    if not isinstance(boot, str) or not re.fullmatch(r"[A-Fa-f0-9]{16}", boot):
        raise ValueError("neplatné ID startu uzlu")
    uptime = _integer(packet, "uptime_ms")
    seq, raw, rssi, snr = None, None, None, None
    if kind == "rf_rx":
        seq = _integer(packet, "event_seq")
        size = _integer(packet, "len", 1, 255)
        encoded = packet.get("raw_hex")
        if not isinstance(encoded, str) or not re.fullmatch(r"[A-Fa-f0-9]{%d}" % (size * 2), encoded):
            raise ValueError("raw_hex neodpovídá délce rámce")
        raw = bytes.fromhex(encoded)
        rssi = _integer(packet, "rssi", -32768, 32767)
        snr = _integer(packet, "snr", -128, 127)
    else:
        for key in _STATUS_COUNTERS:
            _integer(packet, key)
        if type(packet.get("rx_enabled")) is not bool:
            raise ValueError("chybí stav RX monitoru")
        for key, limit in (("firmware", 48), ("config_source", 24), ("profile", 80)):
            if not isinstance(packet.get(key), str) or len(packet[key]) > limit:
                raise ValueError(f"{key}: neplatný text")
    return kind, node.upper(), boot.upper(), seq, uptime, raw, rssi, snr


def record_event(packet, peer):
    values = validate_event(packet)
    payload = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    conn = _connect()
    try:
        # Deliberately no UNIQUE(msg_id) or sequence deduplication. Each arrival
        # is evidence, including RF repeats and duplicated UDP datagrams.
        cursor = conn.execute(
            "INSERT INTO rf_events (received_at, peer, packet_type, node, boot_id, "
            "event_seq, uptime_ms, raw, rssi, snr, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), peer, *values, payload),
        )
        if values[0] == "rf_rx":
            _save_details(conn, cursor.lastrowid, values[5])
        conn.commit()
    finally:
        conn.close()


def _where(since, until, kind, node, boot="", source="", destination="", frame_type="", decode_status=""):
    clauses, params = ["e.received_at >= ?", "e.received_at < ?"], [since, until]
    if kind in EVENT_TYPES:
        clauses.append("e.packet_type = ?")
        params.append(kind)
    if node:
        clauses.append("e.node = ?")
        params.append(node.upper())
    for field, value in (("e.boot_id", boot.upper()), ("d.source", source.upper()),
                         ("d.destination", destination.upper()), ("d.frame_type", frame_type),
                         ("d.decode_status", decode_status)):
        if value:
            clauses.append(field + " = ?")
            params.append(value)
    return " AND ".join(clauses), params


def get_events(since, until, kind="", node="", before=None, limit=100, **filters):
    where, params = _where(since, until, kind, node, **filters)
    if before is not None:
        where += " AND e.id < ?"
        params.append(before)
    params.append(max(1, min(int(limit), 200)))
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT e.*, d.details_json FROM rf_events e LEFT JOIN rf_frame_details d ON d.event_id=e.id "
            f"WHERE {where} ORDER BY e.id DESC LIMIT ?", params).fetchall()
        result = []
        for row in rows:
            event = dict(row)
            raw = event.pop("raw")
            event["raw_hex"] = raw.hex().upper() if raw is not None else ""
            details = event.pop("details_json")
            event["decoded"] = json.loads(details) if details else None
            result.append(event)
        return result
    finally:
        conn.close()


def count_events(since, until, kind="", node="", **filters):
    where, params = _where(since, until, kind, node, **filters)
    conn = _connect()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM rf_events e LEFT JOIN rf_frame_details d ON d.event_id=e.id WHERE {where}", params).fetchone()[0]
    finally:
        conn.close()


def latest_statuses(node="", now=None):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT e.* FROM rf_events e JOIN (SELECT MAX(id) id FROM rf_events "
            "WHERE packet_type = 'node_status' AND (?='' OR node=?) GROUP BY node) last ON e.id = last.id "
            "ORDER BY e.id DESC LIMIT 50", (node.upper(), node.upper()),
        ).fetchall()
        now = time.time() if now is None else now
        result = []
        for row in rows:
            s = json.loads(row["payload_json"])
            s.update(received_at=row["received_at"], peer=row["peer"])
            s["age_s"] = max(0, int(now - row["received_at"]))
            s["online"] = s["age_s"] <= 90
            s["uptime_label"] = format_uptime(s["uptime_ms"])
            s["reset_label"] = reset_reason_label(s["reset_reason"])
            s["config_label"] = {"keyed": "uložená konfigurace", "legacy": "starší formát konfigurace",
                                 "profile": "obnovovací profil", "defaults": "výchozí hodnoty"}.get(s["config_source"], s["config_source"])
            result.append(s)
        return result
    finally:
        conn.close()


def export_events(since, until, kind="", node="", **filters):
    """Read an immutable upper ID boundary so a busy node cannot extend an export forever."""
    where, params = _where(since, until, kind, node, **filters)
    conn = _connect()
    try:
        joined = "rf_events e LEFT JOIN rf_frame_details d ON d.event_id=e.id"
        last = conn.execute(f"SELECT MAX(e.id) FROM {joined} WHERE {where}", params).fetchone()[0]
        if last is None:
            return
        after = 0
        while True:
            rows = conn.execute(
                f"SELECT e.id, e.received_at, e.peer, e.payload_json, d.details_json FROM {joined} WHERE {where} "
                "AND e.id > ? AND e.id <= ? ORDER BY e.id LIMIT 200", (*params, after, last),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                yield json.dumps({"id": row["id"], "received_at": row["received_at"],
                                  "peer": row["peer"], "packet": json.loads(row["payload_json"]),
                                  "decoded": json.loads(row["details_json"]) if row["details_json"] else None},
                                 ensure_ascii=False, allow_nan=False) + "\n"
            after = rows[-1]["id"]
    finally:
        conn.close()


def format_uptime(ms):
    seconds = ms // 1000
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return (f"{days} d " if days else "") + f"{hours:02}:{minutes:02}:{seconds:02}"


def reset_reason_label(reason):
    bits = {1: "reset tlačítkem", 2: "watchdog", 4: "softwarový reset", 8: "zamrznutí CPU",
            0x10000: "probuzení ze System OFF", 0x20000: "probuzení LPCOMP",
            0x40000: "probuzení debug", 0x80000: "probuzení NFC", 0x100000: "probuzení VBUS"}
    if reason == 0:
        return "zapnutí nebo reset napájením (bez určení příčiny)"
    labels = [label for bit, label in bits.items() if reason & bit]
    known = sum(bits)
    if reason & ~known:
        labels.append(f"další bity 0x{reason & ~known:X}")
    return ", ".join(labels)


def get_sessions(since, until, node="", boot=""):
    where, params = _where(since, until, "", node, boot=boot)
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT e.node,e.boot_id,MIN(e.received_at) first_at,MAX(e.received_at) last_at,"
            "COUNT(CASE WHEN e.packet_type='rf_rx' THEN 1 END) rx_packets,"
            "COUNT(DISTINCT e.event_seq) unique_seq,MIN(e.event_seq) first_seq,MAX(e.event_seq) last_seq "
            f"FROM rf_events e WHERE {where} GROUP BY e.node,e.boot_id ORDER BY last_at DESC LIMIT 20", params).fetchall()
        result = []
        for row in rows:
            s = dict(row)
            s["udp_duplicates"] = s["rx_packets"] - s["unique_seq"]
            span = s["last_seq"] - s["first_seq"] if s["first_seq"] is not None else None
            s["sequence_gaps"] = span + 1 - s["unique_seq"] if span is not None and span < 2**31 else None
            result.append(s)
        return result
    finally:
        conn.close()
