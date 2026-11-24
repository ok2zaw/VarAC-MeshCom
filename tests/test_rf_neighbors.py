import json
import os
import tempfile
import unittest
from unittest.mock import patch

import common
import rf_monitor
import rf_neighbors
import webapp


def packet(path="OK1AAA-1,OK2RPT-9", seq=0, rssi=-100, snr=-8, node="OK2ZAW-30", boot="0123456789ABCDEF"):
    raw = b":\x04\x03\x02\x01\x94" + path.encode() + b">262:Hello\0\x09\x13"
    raw += sum(raw).to_bytes(2, "big")
    return {"monitor_version": 1, "type": "rf_rx", "node": node, "boot_id": boot,
            "event_seq": seq, "uptime_ms": 100, "rssi": rssi, "snr": snr,
            "len": len(raw), "raw_hex": raw.hex()}


class NeighborsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(common, "DB_PATH", os.path.join(self.tmp.name, "gateway.db"))
        p.start()
        self.addCleanup(p.stop)
        common.init_db()

    def record(self, at, **args):
        with patch.object(rf_monitor.time, "time", return_value=at):
            rf_monitor.record_event(packet(**args), "peer")

    def test_last_hop_gets_signal_and_rf_repeats_survive_udp_deduplication(self):
        self.record(100, seq=0, rssi=-110, snr=-10)
        self.record(105, seq=0, rssi=-110, snr=-10)  # UDP duplicate
        self.record(110, seq=1, rssi=-90, snr=-2)  # New RF capture of the SAME message ID
        result = rf_neighbors.signal_history("OK2ZAW-30", 0, 200)
        self.assertEqual([n["station"] for n in result["neighbors"]], ["OK2RPT-9"])
        n = result["neighbors"][0]
        self.assertEqual((n["count"], n["relay_count"], n["rssi_median"], n["snr_median"]), (2, 2, -100, -6))
        self.assertEqual(n["last_rssi"], -90)
        self.assertEqual(len(result["series"][0]["points"]), 2)
        self.assertEqual(rf_monitor.count_events(0, 200), 3)  # Original archive unchanged.

    def test_late_udp_duplicates_are_not_new_measurements_in_a_later_window(self):
        self.record(100)
        self.record(500)
        self.assertEqual(rf_neighbors.signal_history("OK2ZAW-30", 400, 600)["sample_count"], 0)

    def test_receiver_boot_and_neighbor_own_frames_are_separate(self):
        self.record(100)
        self.record(101, path="OK2RPT-9", seq=1)
        self.record(102, path="OK2RPT-9", seq=0, boot="2222222222222222")
        self.record(103, path="OK2RPT-9", seq=0, node="OK1NODE-1", rssi=-40)
        self.record(104, path="OK2ZAW-30", seq=2)  # Not a neighbor.
        own = rf_neighbors.signal_history("OK2ZAW-30", 0, 200, own_only=True)
        self.assertEqual(own["sample_count"], 2)
        self.assertEqual(own["neighbors"][0]["relay_count"], 0)
        self.assertEqual(len({p["boot"] for p in own["series"][0]["points"]}), 2)
        other = rf_neighbors.signal_history("OK1NODE-1", 0, 200)
        self.assertEqual(other["neighbors"][0]["rssi_median"], -40)

    def test_bad_fcs_and_ack_without_sender_are_excluded(self):
        p = packet()
        raw = bytes.fromhex(p["raw_hex"]).replace(b"Hello", b"Jello")
        rf_monitor.record_event({**p, "raw_hex": raw.hex()}, "peer")
        ack = bytes.fromhex("414433221184040302010100")
        rf_monitor.record_event({**p, "event_seq": 1, "len": len(ack), "raw_hex": ack.hex()}, "peer")
        self.assertEqual(rf_neighbors.signal_history("OK2ZAW-30", 0, 2**40)["neighbors"], [])

    def test_graph_downsampling_keeps_extremes_and_statistics_use_every_frame(self):
        for i in range(80):
            self.record(100 + i * 0.1, seq=i, rssi=-130 if i == 40 else -80 if i == 41 else -100,
                        snr=-20 if i == 30 else 5 if i == 31 else -5)
        result = rf_neighbors.signal_history("OK2ZAW-30", 0, 3600)
        n, s = result["neighbors"][0], result["series"][0]
        self.assertEqual((n["count"], n["rssi_median"], n["snr_median"]), (80, -100, -5))
        self.assertTrue(s["sampled"])
        self.assertLessEqual(len(s["points"]), 6)
        self.assertEqual((min(p["rssi"] for p in s["points"]), max(p["rssi"] for p in s["points"])), (-130, -80))
        self.assertEqual((min(p["snr"] for p in s["points"]), max(p["snr"] for p in s["points"])), (-20, 5))
        self.assertEqual(s["trend"][0]["rssi"], -100)

    def test_selection_empty_state_web_auth_and_safe_chart_json(self):
        self.record(100, path="OK2RPT-9")
        cfg = {**common.DEFAULT_CONFIG, "callsign": "OK2ZAW-30", "web_password": ""}
        with patch.object(common, "load_config", return_value=cfg), patch.object(webapp.time, "time", return_value=200), webapp.app.test_client() as client:
            page = client.get("/neighbors?hours=1")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"neighbor-rssi", page.data)
            self.assertIn(b"OK2RPT-9", page.data)
            chart_json = page.data.split(b'<script id="neighbor-data" type="application/json">')[1].split(b"</script>")[0]
            self.assertEqual(json.loads(chart_json)["history"]["sample_count"], 1)
            self.assertEqual(client.get("/neighbors?node=%3Cscript%3E").status_code, 400)
            self.assertEqual(client.get("/neighbors?selection=1&station=../bad").status_code, 400)
            empty = client.get("/neighbors?selection=1")
            data = json.loads(empty.data.split(b'<script id="neighbor-data" type="application/json">')[1].split(b"</script>")[0])
            self.assertEqual(data["history"]["series"], [])
            self.assertEqual(data["history"]["sample_count"], 1)
        with patch.object(common, "load_config", return_value=common.DEFAULT_CONFIG), webapp.app.test_client() as client:
            self.assertEqual(client.get("/neighbors").status_code, 401)

    def test_a_long_gap_inside_one_aggregate_bucket_stays_a_gap(self):
        self.record(10, seq=0)
        self.record(1700, seq=1)
        data = rf_neighbors.signal_history("OK2ZAW-30", 0, 7 * 86400, gap_seconds=900)
        self.assertEqual(data["bucket_seconds"], 1800)
        trend = data["series"][0]["trend"]
        self.assertEqual(len(trend), 2)
        self.assertEqual([p["t"] for p in trend], [10, 1700])
