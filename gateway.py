#!/usr/bin/env python3
"""
MeshCom <-> Email Gateway daemon.

Bidirectional bridge between a MeshCom node's external UDP interface
(port 1799, JSON protocol) and email. Configuration is read from
config.json (editable via webapp.py) and every message is logged to
gateway.db so the web UI can show status and history.

  * mesh -> email : listens on UDP, relays group text messages to email.
  * email -> mesh : watches a Maildir 'new' folder, forwards new mail
                     (subject/body, truncated) as a group message.

Run alongside webapp.py (see systemd/ for service files).
"""

import imaplib
import json
import logging
import os
import re
import smtplib
import socket
import sqlite3
import threading
import time
import uuid
from email import policy
from email.parser import BytesParser
from email.mime.text import MIMEText

import common

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
)
log = logging.getLogger("meshcom-gateway")

_msg_counter = 0
_msg_counter_lock = threading.Lock()


def next_msg_id(callsign: str) -> str:
    global _msg_counter
    with _msg_counter_lock:
        _msg_counter += 1
        n = _msg_counter
    return f"{callsign.replace('-', '')[:6]}{n:03d}"


def update_udp_monitor(direction: str, peer: str = None, status: str = "ok",
                       error: str = None, count_datagram: bool = True) -> None:
    try:
        common.record_udp_activity(direction, peer, status, error, count_datagram)
    except sqlite3.Error as exc:
        log.warning("Could not update UDP monitor: %s", exc)


def log_udp_packet(direction: str, peer: str, data: bytes, status: str) -> None:
    try:
        common.record_udp_packet(direction, peer, data, status)
    except sqlite3.Error as exc:
        log.warning("Could not store UDP packet: %s", exc)


def send_udp_message(cfg: dict, dst: str, text: str, source: str = None) -> None:
    """Send one text to the node; with a source ('web', 'email') its delivery is tracked."""
    text = text[: cfg["max_mesh_payload"]]
    payload = {"type": "msg", "dst": dst, "msg": text}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    peer = f"{cfg['mesh_node_ip']}:{cfg['mesh_udp_port']}"
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(data, (cfg["mesh_node_ip"], cfg["mesh_udp_port"]))
        update_udp_monitor("tx", peer, "odesláno")
        log_udp_packet("tx", peer, data, "odesláno")
        if source:
            try:
                common.record_outgoing_message(source, dst, text)
            except sqlite3.Error as exc:
                log.warning("Could not record outgoing message: %s", exc)
        log.info("Sent to mesh (dst=%s): %s", dst, text)
    except OSError as exc:
        update_udp_monitor("tx", peer, "chyba", str(exc), count_datagram=False)
        log_udp_packet("tx", peer, data, f"chyba: {exc}")
        raise
    finally:
        if sock is not None:
            sock.close()


def send_email(cfg: dict, subject: str, body: str) -> None:
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["mail_from"]
    msg["To"] = cfg["mail_to"]

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as server:
        if cfg["smtp_use_tls"]:
            server.starttls()
        if cfg["smtp_user"]:
            server.login(cfg["smtp_user"], cfg["smtp_password"])
        server.send_message(msg)
    log.info("Sent email: %s", subject)


# ---------------------------------------------------------------------
# mesh -> email
# ---------------------------------------------------------------------

def decode_udp_packet(data: bytes):
    # Firmware buffers may carry a trailing NUL terminator, CR/LF or a BOM.
    text = data.decode("utf-8", errors="replace").strip("\x00﻿ \t\r\n")
    return json.loads(text)


UDP_LISTENER_HEARTBEAT = "udp-listener"
UDP_BIND_RETRY_SECONDS = 10


def bind_udp_listener():
    """Bind the receive socket, retrying until it succeeds.

    A failed bind (wrong listen_ip, port taken) used to kill the listener
    thread silently while sending kept working; now it is logged, shown in
    the web UI and retried with the current configuration.
    """
    failed = False
    while True:
        cfg = common.load_config()
        address = (cfg["listen_ip"], int(cfg["mesh_udp_port"]))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(address)
        except OSError as exc:
            sock.close()
            error = f"Nelze naslouchat na {address[0]}:{address[1]}: {exc}"
            log.error("%s", error)
            update_udp_monitor("rx", status="chyba", error=error, count_datagram=False)
            failed = True
            time.sleep(UDP_BIND_RETRY_SECONDS)
            continue
        sock.settimeout(2.0)
        log.info("Listening for MeshCom UDP packets on %s:%d", *address)
        if failed:
            # Replace the bind error shown in the web UI.
            update_udp_monitor("rx", status=f"naslouchá na {address[0]}:{address[1]}",
                               count_datagram=False)
        return sock


def udp_listener():
    sock = bind_udp_listener()
    last_heartbeat = 0.0

    while True:
        if time.monotonic() - last_heartbeat >= 10:
            try:
                common.set_heartbeat(UDP_LISTENER_HEARTBEAT)
            except sqlite3.Error as exc:
                log.warning("Could not update UDP listener heartbeat: %s", exc)
            last_heartbeat = time.monotonic()
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError as e:
            log.error("UDP recv error: %s", e)
            update_udp_monitor("rx", status="chyba", error=str(e), count_datagram=False)
            continue

        peer = f"{addr[0]}:{addr[1]}"
        log.debug("UDP rx from %s: %r", peer, data)
        update_udp_monitor("rx", peer, "přijato")
        cfg = common.load_config()  # pick up live config changes (group, smtp, etc.)

        def finish(status: str, error: str = None) -> None:
            update_udp_monitor("rx", peer, status, error, count_datagram=False)
            log_udp_packet("rx", peer, data, status)

        try:
            packet = decode_udp_packet(data)
        except json.JSONDecodeError:
            finish("neplatný JSON", "Datagram neobsahuje platný JSON.")
            continue

        if not isinstance(packet, dict):
            finish("neplatná struktura", "JSON paket není objekt.")
            continue

        if packet.get("type") in {"ack", "msg"}:
            try:
                delivery = common.track_delivery(packet, cfg.get("callsign", ""))
            except sqlite3.Error as exc:
                log.warning("Could not update delivery status: %s", exc)
                delivery = None
            if delivery:
                log.info("Delivery status of msg_id %s: %s", packet.get("msg_id"), delivery)
            if packet.get("type") == "ack":
                finish(f"potvrzení: {common.delivery_label(delivery)}" if delivery else "potvrzení cizí zprávy")
                continue

        recorded_station = False
        try:
            recorded_station = common.record_station_observation(packet, cfg.get("callsign", ""))
        except sqlite3.Error as exc:
            log.warning("Could not record RF station observation: %s", exc)

        if packet.get("type") != "msg":
            own_position = False
            if not recorded_station:
                try:
                    own_position = common.record_own_position(packet, cfg.get("callsign", ""))
                except sqlite3.Error as exc:
                    log.warning("Could not record own node position: %s", exc)
            finish("RF pozorování" if recorded_station else
                   "poloha vlastního uzlu" if own_position else "ignorován jiný typ")
            continue

        try:
            new_message = common.record_rf_message(packet, cfg.get("callsign", ""))
        except sqlite3.Error as exc:
            log.warning("Could not record mesh message: %s", exc)
            new_message = True  # still forward; duplicates are better than losses
        if not new_message:
            finish("duplikát, potvrzení nebo echo")
            continue

        src = str(packet.get("src", "unknown"))
        dst = str(packet.get("dst", "")).split(",")[-1].strip().upper()
        if src.split(",")[0].strip().upper() == str(cfg.get("callsign", "")).strip().upper():
            finish("vlastní zpráva (echo uzlu)")
            continue
        if dst != str(cfg["mesh_group"]).strip().upper():
            finish(f"zpráva pro {dst}")
            continue

        finish("MeshCom zpráva → e-mail")
        text = common.clean_message_text(str(packet.get("msg", "")))
        log.info("Mesh message from %s in group %s: %s", src, dst, text)

        subject = f"[MeshCom {dst}] {src}"
        body = f"From: {src}\nGroup: {dst}\n\n{text}"
        try:
            send_email(cfg, subject, body)
            common.log_message("mesh_to_email", src, subject, text, status="ok")
        except Exception as e:
            log.error("Failed to send email for mesh message: %s", e)
            common.log_message("mesh_to_email", src, subject, text, status="error", error=str(e))


# ---------------------------------------------------------------------
# email -> mesh
# ---------------------------------------------------------------------

def write_email_to_maildir(maildir_path: str, payload: bytes, prefix: str = "imap") -> str:
    os.makedirs(maildir_path, exist_ok=True)
    unique_name = f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.eml"
    file_path = os.path.join(maildir_path, unique_name)
    with open(file_path, "wb") as f:
        f.write(payload)
    return file_path


def parse_email_file(path: str):
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    subject = msg.get("subject", "(no subject)")
    sender = msg.get("from", "unknown")

    body = ""
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            filename = part.get_filename()
            disposition = part.get_content_disposition()

            if content_type == "text/plain" and disposition != "attachment":
                body = part.get_content()
                continue

            if filename:
                attachments.append(filename)
            elif disposition == "attachment":
                attachments.append(content_type)
    else:
        body = msg.get_content()

    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    return sender, subject, (body or "").strip(), attachments


# "GROUP: 262" anywhere in the subject picks the target group, e.g. "Re: GROUP: 262 Test".
_SUBJECT_GROUP = re.compile(r"\bGROUP\s*:\s*#?(\d{1,5})\b\s*[-:]?\s*", re.IGNORECASE)


def email_destination(subject: str, default_group: str) -> tuple[str, str]:
    """Target group from the subject and the subject without the GROUP tag."""
    match = _SUBJECT_GROUP.search(subject or "")
    if not match:
        return str(default_group), subject
    rest = (subject[: match.start()] + subject[match.end():]).strip()
    return match.group(1), re.sub(r"^(?:(?:re|fwd?|aw|wg)\s*:\s*)+$", "", rest, flags=re.IGNORECASE).strip()


def build_mesh_messages(sender: str, subject: str, body: str, attachments: list[str], max_len: int) -> list[str]:
    short_sender = re.sub(r"<.*?>", "", sender).strip() or "unknown"
    body = re.sub(r"\s+", " ", (body or "").strip())

    attachment_summary = ""
    if attachments:
        unique_attachments = []
        seen = set()
        for item in attachments:
            if item not in seen:
                unique_attachments.append(item)
                seen.add(item)
        attachment_summary = " | Příloha: " + ", ".join(unique_attachments[:3])
        if len(unique_attachments) > 3:
            attachment_summary += ", ..."

    if not body:
        body = "(bez textu)"

    if subject or attachment_summary:
        first_prefix = f"{short_sender}: {subject}{attachment_summary} - "
    else:
        first_prefix = f"{short_sender}: "
    if subject:
        continuation_prefix = f"{short_sender}: {subject} (pokračování) - "
    else:
        continuation_prefix = f"{short_sender} (pokračování): "

    def truncate_prefix(prefix: str) -> str:
        if len(prefix) <= max_len:
            return prefix
        return prefix[: max_len - 6].rstrip() + "... "

    first_prefix = truncate_prefix(first_prefix)
    continuation_prefix = truncate_prefix(continuation_prefix)
    parts = []
    remaining = body
    current_prefix = first_prefix

    while remaining:
        available = max_len - len(current_prefix)
        if available <= 0:
            parts.append(current_prefix[:max_len])
            break

        if len(remaining) <= available:
            parts.append(current_prefix + remaining)
            break

        piece = remaining[:available].rsplit(" ", 1)[0].strip() if " " in remaining[:available] else remaining[:available]
        if not piece:
            piece = remaining[:available]

        parts.append(current_prefix + piece)
        remaining = remaining[len(piece):].lstrip()
        current_prefix = continuation_prefix

    if not parts:
        parts.append(first_prefix + "(bez textu)")

    return parts


def fetch_imap_messages(cfg: dict) -> list[str]:
    if not cfg.get("imap_host") or not cfg.get("imap_user"):
        return []

    server = None
    try:
        host = cfg["imap_host"]
        port = int(cfg.get("imap_port", 993))
        folder = cfg.get("imap_folder", "INBOX")
        maildir = cfg.get("maildir_new") or os.path.join(os.path.dirname(__file__), "Maildir", "new")
        os.makedirs(maildir, exist_ok=True)

        imap_class = imaplib.IMAP4_SSL if cfg.get("imap_use_ssl", True) else imaplib.IMAP4
        server = imap_class(host, port, timeout=30)
        server.login(cfg["imap_user"], cfg.get("imap_password", ""))
        server.select(folder)

        status, data = server.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []

        saved_paths = []
        for msg_id in data[0].split():
            status, payload = server.fetch(msg_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw_message = None
            for item in payload:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_message = item[1]
                    break
            if raw_message is None:
                continue

            saved = write_email_to_maildir(maildir, raw_message)
            saved_paths.append(saved)
            server.store(msg_id, "+FLAGS", "(\\Seen)")

        return saved_paths
    except Exception as exc:
        log.error("IMAP fetch failed: %s", exc)
        return []
    finally:
        if server is not None:
            try:
                server.logout()
            except Exception:
                pass


def poll_imap():
    while True:
        cfg = common.load_config()
        if cfg.get("imap_host"):
            try:
                saved = fetch_imap_messages(cfg)
                if saved:
                    log.info("Fetched %d message(s) from IMAP mailbox %s", len(saved), cfg.get("imap_folder", "INBOX"))
            except Exception as exc:
                log.error("IMAP polling error: %s", exc)
        time.sleep(cfg.get("poll_interval_seconds", 5))


def poll_maildir():
    cfg = common.load_config()
    log.info("Watching Maildir: %s", cfg["maildir_new"])
    seen = set(os.listdir(cfg["maildir_new"])) if os.path.isdir(cfg["maildir_new"]) else set()

    while True:
        time.sleep(cfg.get("poll_interval_seconds", 5))
        cfg = common.load_config()  # live config reload
        maildir = cfg["maildir_new"]
        if not os.path.isdir(maildir):
            continue

        current = set(os.listdir(maildir))
        new_files = current - seen
        seen = current

        for fname in new_files:
            fpath = os.path.join(maildir, fname)
            try:
                sender, subject, body, attachments = parse_email_file(fpath)
            except Exception as e:
                log.error("Failed to parse email %s: %s", fpath, e)
                continue

            group, mesh_subject = email_destination(subject, cfg["mesh_group"])
            messages = build_mesh_messages(sender, mesh_subject, body, attachments, cfg["max_mesh_payload"])
            all_ok = True
            for text in messages:
                try:
                    send_udp_message(cfg, group, text, source="email")
                    common.log_message("email_to_mesh", sender, subject, text, status="ok")
                except Exception as e:
                    all_ok = False
                    log.error("Failed to send mesh message for %s: %s", fpath, e)
                    common.log_message("email_to_mesh", sender, subject, text, status="error", error=str(e))

            if all_ok:
                archive_target = cfg.get("maildir_cur") or os.path.join(os.path.dirname(maildir), "cur")
                archived = common.archive_mail_file(fpath, archive_target)
                if archived:
                    log.info("Archived processed email to %s", archived)
                else:
                    log.warning("Could not archive processed email %s", fpath)


# ---------------------------------------------------------------------
# heartbeat so the web UI knows the daemon is alive
# ---------------------------------------------------------------------

def heartbeat_loop():
    while True:
        common.set_heartbeat("gateway")
        time.sleep(10)


def main():
    common.init_db()
    cfg = common.load_config()
    maildir_root = cfg.get("maildir_new")
    if maildir_root:
        maildir_root = os.path.dirname(maildir_root)
        common.ensure_maildir_layout(maildir_root)
    log.info("Starting MeshCom <-> Email gateway (group %s, node %s)",
              cfg["mesh_group"], cfg["mesh_node_ip"])

    threads = [
        threading.Thread(target=udp_listener, name="udp-listener", daemon=True),
        threading.Thread(target=poll_maildir, name="maildir-watcher", daemon=True),
    ]
    if cfg.get("imap_host"):
        threads.append(threading.Thread(target=poll_imap, name="imap-watcher", daemon=True))
    threads.append(threading.Thread(target=heartbeat_loop, name="heartbeat", daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
