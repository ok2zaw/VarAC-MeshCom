import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import common
import gateway
import rf_monitor
import webapp

FRAME = bytes.fromhex("3A04030201944F4B314141412D312C4F4B325250542D393E3236323A48656C6C6F7B30303100091309462809617E")


def rx(**changes):
    return {"monitor_version": 1, "type": "rf_rx", "node": "OK2ZAW-30",
            "boot_id": "0123456789ABCDEF", "event_seq": 0, "uptime_ms": 123,
            "len": 4, "raw_hex": "0041FF00", "rssi": -100, "snr": -7, **changes}


def status(**changes):
    return {"monitor_version": 1, "type": "node_status", "node": "OK2ZAW-30",
            "boot_id": "0123456789ABCDEF", "uptime_ms": 123, "heap_bytes": 90000,
            "reset_reason": 4, "rx_seen": 0, "rx_dropped": 0, "rx_suppressed": 0,
            "queue_depth": 0, "queue_high_water": 0, "udp_failures": 0,
            "format_failures": 0, "rx_enabled": True, "firmware": "4.40a",
            "config_source": "profile", "profile": "OK2ZAW-30-v1", **changes}


class RfMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(common, "DB_PATH", os.path.join(self.tmp.name, "gateway.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        common.init_db()

    def events(self):
        return rf_monitor.get_events(0, time.time() + 1)

    def test_byte_exact_repeats_and_restarts_are_preserved(self):
        for packet in (rx(), rx(), rx(event_seq=1), rx(boot_id="1111111111111111")):
            rf_monitor.record_event(packet, "192.0.2.1:1799")
        self.assertEqual(len(self.events()), 4)
        self.assertEqual([e["event_seq"] for e in self.events()], [0, 1, 0, 0])
        self.assertEqual({e["raw_hex"] for e in self.events()}, {"0041FF00"})
        conn = common.get_db()
        try:
            self.assertEqual(conn.execute("SELECT raw FROM rf_events LIMIT 1").fetchone()[0], b"\x00A\xff\x00")
        finally:
            conn.close()

    def test_archive_survives_udp_monitor_retention_and_database_reopen(self):
        rf_monitor.record_event(rx(), "peer")
        for _ in range(common.UDP_PACKET_LOG_LIMIT + 1):
            common.record_udp_packet("rx", "peer", b'{}')
        common.init_db()  # The migration is idempotent and retains existing captures.
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(common.get_udp_packets(limit=999)), common.UDP_PACKET_LOG_LIMIT)

    def test_invalid_capture_is_rejected_instead_of_truncated_or_coerced(self):
        cases = [rx(monitor_version=True), rx(monitor_version=2), rx(event_seq=True),
                 rx(event_seq=-1), rx(uptime_ms=2**32), rx(len=255), rx(len=0, raw_hex=""),
                 rx(raw_hex="00 1FF00"), rx(raw_hex="GG41FF00"), rx(snr=1.5),
                 rx(rssi=None), rx(boot_id="boot"), rx(node="<script>"), status(rx_seen=-1),
                 status(rx_enabled=1), status(profile=None), rx(unknown=float("nan"))]
        for packet in cases:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                rf_monitor.record_event(packet, "peer")
        self.assertEqual(self.events(), [])

    def test_listener_monitor_events_never_reach_email_or_message_tracking(self):
        packets = [rx(), status(), rx(len=9)]

        class Socket:
            def recvfrom(self, size):
                if not packets:
                    raise KeyboardInterrupt
                return json.dumps(packets.pop(0)).encode(), ("192.0.2.1", 1799)

        with patch.object(gateway, "bind_udp_listener", return_value=Socket()), \
             patch.object(common, "load_config", return_value={"callsign": "OK2ZAW-30"}), \
             patch.object(gateway, "send_email") as email, \
             patch.object(common, "track_delivery") as delivery, \
             patch.object(common, "record_rf_message") as message:
            with self.assertRaises(KeyboardInterrupt):
                gateway.udp_listener()
        email.assert_not_called()
        delivery.assert_not_called()
        message.assert_not_called()
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(common.get_udp_packets()[0]["status"], "neplatný RF monitor")

    def test_filter_cursor_and_loss_counters(self):
        rf_monitor.record_event(rx(), "peer")
        rf_monitor.record_event(rx(event_seq=4), "peer")
        rf_monitor.record_event(status(rx_seen=5, rx_dropped=3), "peer")
        rf_monitor.record_event(status(node="OK1AAA-1"), "another")
        latest = rf_monitor.get_events(0, time.time() + 1, kind="rf_rx", node="ok2zaw-30", limit=1)
        self.assertEqual(latest[0]["event_seq"], 4)
        older = rf_monitor.get_events(0, time.time() + 1, kind="rf_rx", before=latest[0]["id"])
        self.assertEqual([e["event_seq"] for e in older], [0])
        statuses = {s["node"]: s for s in rf_monitor.latest_statuses()}
        self.assertEqual(statuses["OK2ZAW-30"]["rx_dropped"], 3)

    def test_web_filters_export_and_escape_device_text(self):
        with patch.object(rf_monitor.time, "time", return_value=1805756400):  # 2027-03-22 UTC
            rf_monitor.record_event(rx(), "peer")
            rf_monitor.record_event(status(profile="<script>alert(1)</script>"), "peer")
        cfg = {**common.DEFAULT_CONFIG, "web_password": "", "display_utc": True}
        with patch.object(common, "load_config", return_value=cfg), webapp.app.test_client() as client:
            response = client.get("/rf?start=2027-03-22&end=2027-03-22")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"0041FF00", response.data)
            self.assertIn(b"&lt;script&gt;", response.data)
            self.assertNotIn(b"<script>alert(1)</script>", response.data)
            result = client.get("/rf/export?start=2027-03-22&end=2027-03-22&kind=rf_rx")
            rows = [json.loads(line) for line in result.data.splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["packet"]["raw_hex"], "0041FF00")
            empty = client.get("/rf/export?start=2027-03-20&end=2027-03-20")
            self.assertEqual(empty.data, b"")

    def test_rf_archive_and_download_require_existing_web_auth(self):
        with patch.object(common, "load_config", return_value=common.DEFAULT_CONFIG), webapp.app.test_client() as client:
            self.assertEqual(client.get("/rf").status_code, 401)
            self.assertEqual(client.get("/rf/export").status_code, 401)

    def test_date_export_excludes_the_next_midnight(self):
        midnight = datetime(2027, 3, 23, tzinfo=timezone.utc).timestamp()
        with patch.object(rf_monitor.time, "time", return_value=midnight - 1):
            rf_monitor.record_event(rx(), "peer")
        with patch.object(rf_monitor.time, "time", return_value=midnight):
            rf_monitor.record_event(rx(event_seq=1), "peer")
        cfg = {**common.DEFAULT_CONFIG, "web_password": "", "display_utc": True}
        with patch.object(common, "load_config", return_value=cfg), webapp.app.test_client() as client:
            result = client.get("/rf/export?start=2027-03-22&end=2027-03-22")
        rows = [json.loads(line) for line in result.data.splitlines()]
        self.assertEqual([row["packet"]["event_seq"] for row in rows], [0])

    def test_cpp_capture_and_json_feed_the_python_archive(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        self.assertIsNotNone(compiler, "A C++ compiler is required for firmware monitor tests")
        root = Path(__file__).resolve().parents[1]
        exe = Path(self.tmp.name) / "rf-monitor-test"
        subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pthread",
                        "-I", str(root / "firmware/overlay/src"),
                        str(root / "firmware/tests/rf_monitor_core_test.cpp"), "-o", str(exe)], check=True)
        result = subprocess.run([str(exe)], check=True, text=True, capture_output=True)
        for line in result.stdout.splitlines():
            rf_monitor.record_event(json.loads(line), "cpp-test")
        events = self.events()
        self.assertEqual(len(events), 2)
        raw = next(e for e in events if e["packet_type"] == "rf_rx")
        self.assertEqual(bytes.fromhex(raw["raw_hex"]), bytes(range(255)))
        self.assertEqual(raw["uptime_ms"], 2**32 - 1)

    def test_decoded_filters_and_export_match_without_losing_repeats(self):
        for seq in (0, 1):
            rf_monitor.record_event(rx(event_seq=seq, len=len(FRAME), raw_hex=FRAME.hex()), "peer")
        rf_monitor.record_event(rx(event_seq=2), "peer")
        args = dict(source="ok1aaa-1", destination="262", frame_type="msg", decode_status="ok")
        rows = rf_monitor.get_events(0, time.time() + 1, **args)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["decoded"]["msg_id"], "01020304")
        self.assertEqual(rf_monitor.count_events(0, time.time() + 1, **args), 2)
        exported = [json.loads(s) for s in rf_monitor.export_events(0, time.time() + 1, **args)]
        self.assertEqual(exported[0]["decoded"]["payload_text"], "Hello{001")
        self.assertEqual(exported[0]["packet"]["raw_hex"], FRAME.hex())

    def test_old_archive_migration_is_idempotent_and_preserves_originals(self):
        rf_monitor.record_event(rx(len=len(FRAME), raw_hex=FRAME.hex()), "peer")
        conn = common.get_db()
        try:
            before = tuple(conn.execute("SELECT * FROM rf_events").fetchone())
            conn.execute("DELETE FROM rf_frame_details")
            conn.commit()
        finally:
            conn.close()
        common.init_db()
        common.init_db()
        conn = common.get_db()
        try:
            self.assertEqual(tuple(conn.execute("SELECT * FROM rf_events").fetchone()), before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rf_frame_details").fetchone()[0], 1)
        finally:
            conn.close()
        self.assertEqual(self.events()[0]["decoded"]["source"], "OK1AAA-1")

    def test_session_gaps_late_delivery_udp_duplicates_and_sequence_wrap(self):
        for seq in (0, 4, 4):
            rf_monitor.record_event(rx(event_seq=seq), "peer")
        sessions = rf_monitor.get_sessions(0, time.time() + 1)
        self.assertEqual((sessions[0]["sequence_gaps"], sessions[0]["udp_duplicates"]), (3, 1))
        rf_monitor.record_event(rx(event_seq=1), "peer")
        self.assertEqual(rf_monitor.get_sessions(0, time.time() + 1)[0]["sequence_gaps"], 2)
        for seq in (2**32 - 1, 0):
            rf_monitor.record_event(rx(event_seq=seq, boot_id="2222222222222222"), "peer")
        sessions = rf_monitor.get_sessions(0, time.time() + 1)
        self.assertEqual(len(sessions), 2)
        self.assertIsNone(sessions[0]["sequence_gaps"])

    def test_status_age_labels_and_sender_metadata_are_trusted_locally(self):
        with patch.object(rf_monitor.time, "time", return_value=100):
            rf_monitor.record_event(status(received_at=10000000, peer="spoofed"), "real-peer")
        s = rf_monitor.latest_statuses(node="ok2zaw-30", now=180)[0]
        self.assertEqual((s["received_at"], s["peer"], s["age_s"], s["online"]), (100, "real-peer", 80, True))
        self.assertIn("softwarový reset", s["reset_label"])
        self.assertFalse(rf_monitor.latest_statuses(now=191)[0]["online"])
        self.assertEqual(rf_monitor.format_uptime(90061000), "1 d 01:01:01")

    def test_live_page_filters_paging_links_and_short_udp_log(self):
        rf_monitor.record_event(rx(len=len(FRAME), raw_hex=FRAME.hex()), "peer")
        common.record_udp_packet("rx", "peer", json.dumps(rx()).encode())
        common.record_udp_packet("rx", "peer", json.dumps(status()).encode())
        common.record_udp_packet("rx", "peer", b"not JSON")
        cfg = {**common.DEFAULT_CONFIG, "web_password": "", "callsign": "OK2ZAW-30"}
        with patch.object(common, "load_config", return_value=cfg), \
             patch.object(webapp, "local_ip_towards", return_value="127.0.0.1"), webapp.app.test_client() as client:
            result = client.get("/rf?source=OK1AAA-1&destination=262&frame_type=msg&refresh=1")
            self.assertEqual(result.status_code, 200)
            self.assertIn(b"Hello{001", result.data)
            self.assertIn(b'http-equiv="refresh"', result.data)
            self.assertIn(b"source=OK1AAA-1", result.data)
            download = client.get("/rf/export?source=OK1AAA-1&destination=262")
            self.assertEqual(len(download.data.splitlines()), 1)
            udp = client.get("/udp?type=node_status")
            self.assertIn(b'&#34;type&#34;: &#34;node_status&#34;', udp.data)
            self.assertNotIn(b'&#34;type&#34;: &#34;rf_rx&#34;', udp.data)
            self.assertEqual(client.get("/rf?boot=invalid").status_code, 400)


if __name__ == "__main__":
    unittest.main()
