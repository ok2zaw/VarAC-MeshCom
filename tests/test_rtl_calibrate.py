import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aprs_rx  # noqa: E402
import common  # noqa: E402
import radio_apps  # noqa: E402
import rtl_calibrate  # noqa: E402
import webapp  # noqa: E402

SCAN_OUTPUT = """kal: Scanning for GSM-900 base stations.
GSM-900:
    chan:    7 (936.4MHz + 8.123kHz)    power:  186245.32
    chan:   37 (942.4MHz - 312Hz)    power:  421337.10
    chan:   62 (947.4MHz + 7.988kHz)    power:   98765.43
"""

MEASURE_OUTPUT = """kal: Calculating clock frequency offset.
Using GSM-900 channel 37 (942.4MHz)
Tuned to 942.400000MHz (reported tuner error: 0Hz)
average\t\t[min, max]\t(range, stddev)
- 7.951kHz
\t\t[-8012, -7890]\t(122, 31.420000)
overruns: 0
not found: 0
average absolute error: 8.437 ppm
"""


class CompletedKal:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


class RtlCalibrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state_file = os.path.join(self.tmp.name, "cal.json")
        patcher = patch.object(rtl_calibrate, "STATE_FILE", state_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = {**common.DEFAULT_CONFIG, "aprs_ppm": 3}

    def test_parse_scan_and_ppm(self):
        self.assertEqual(rtl_calibrate.parse_scan(SCAN_OUTPUT), [
            {"channel": 7, "freq_mhz": 936.4, "offset_hz": 8123, "power": 186245.32},
            {"channel": 37, "freq_mhz": 942.4, "offset_hz": -312, "power": 421337.10},
            {"channel": 62, "freq_mhz": 947.4, "offset_hz": 7988, "power": 98765.43}])
        self.assertEqual(rtl_calibrate.parse_ppm(MEASURE_OUTPUT), 8.437)
        self.assertEqual(rtl_calibrate.parse_ppm("average absolute error: -12.500 ppm"), -12.5)
        self.assertIsNone(rtl_calibrate.parse_ppm("not found: 10"))

    def test_kal_options_and_validation(self):
        self.assertEqual(rtl_calibrate.kal_options(self.cfg), ["-d", "0", "-g", "40", "-e", "3"])
        self.assertEqual(rtl_calibrate.kal_options({**self.cfg, "aprs_ppm": 0, "rtl_cal_gain": ""}), ["-d", "0"])
        with self.assertRaises(radio_apps.RadioError):
            rtl_calibrate.kal_options({**self.cfg, "aprs_device": "00000001x"})  # serial number
        for bad in (("LTE", "", ""), ("GSM900", "abc", ""), ("GSM900", "", "70")):
            with self.assertRaises(radio_apps.RadioError):
                rtl_calibrate.validate(*bad)
        rtl_calibrate.validate("EGSM", "37", "38.6")

    def test_run_scans_measures_and_restores_aprs(self):
        calls = []

        def fake_kal(arguments, **kwargs):
            calls.append(arguments)
            return CompletedKal(SCAN_OUTPUT if "-s" in arguments else MEASURE_OUTPUT)

        with patch.object(rtl_calibrate.common, "load_config", return_value=self.cfg), \
             patch.object(rtl_calibrate.subprocess, "run", side_effect=fake_kal), \
             patch.object(rtl_calibrate, "is_active", return_value=True), \
             patch.object(rtl_calibrate, "systemctl") as systemctl, \
             patch.object(rtl_calibrate.time, "sleep"):
            state = rtl_calibrate.run("GSM900")

        self.assertEqual(calls, [["kal", "-s", "GSM900", "-d", "0", "-g", "40", "-e", "3"],
                                 ["kal", "-c", "37", "-b", "GSM900", "-d", "0", "-g", "40", "-e", "3"]])
        self.assertEqual((state["state"], state["channel"], state["ppm"]), ("done", 37, 8.437))
        self.assertEqual([item["channel"] for item in state["channels"]], [37, 7, 62])
        self.assertEqual([call.args for call in systemctl.call_args_list],
                         [("stop", aprs_rx.UNIT), ("start", "--no-block", aprs_rx.UNIT)])
        self.assertEqual(rtl_calibrate.read_state()["ppm"], 8.437)

    def test_run_reports_errors_and_keeps_aprs_off_when_it_was_off(self):
        with patch.object(rtl_calibrate.common, "load_config", return_value=self.cfg), \
             patch.object(rtl_calibrate.subprocess, "run", return_value=CompletedKal("GSM-900:\n")), \
             patch.object(rtl_calibrate, "is_active", return_value=False), \
             patch.object(rtl_calibrate, "systemctl") as systemctl:
            state = rtl_calibrate.run("GSM900")
            self.assertEqual(state["state"], "error")
            self.assertIn("žádná základnová stanice", state["error"])
            state = rtl_calibrate.run("GSM900", channel=37)  # measurement without a result line
        self.assertIn("nevypsal výsledek", state["error"])
        systemctl.assert_not_called()

    def test_status_marks_vanished_run_as_interrupted(self):
        rtl_calibrate.write_state({"state": "scanning", "updated_at": 0})
        with patch.object(rtl_calibrate, "is_active", return_value=False):
            self.assertEqual(rtl_calibrate.status()["state"], "error")

    def test_start_launches_transient_unit(self):
        with patch.object(rtl_calibrate.shutil, "which", return_value="/usr/local/bin/kal"), \
             patch.object(rtl_calibrate, "is_active", return_value=False), \
             patch.object(rtl_calibrate, "systemctl"), \
             patch.object(rtl_calibrate, "_run") as run:
            rtl_calibrate.start(self.cfg, "EGSM", "37")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["systemd-run", "--user", "--collect", f"--unit={rtl_calibrate.UNIT}"])
        self.assertEqual(command[-5:], ["run", "--band", "EGSM", "--channel", "37"])
        self.assertEqual(rtl_calibrate.read_state()["state"], "starting")
        with patch.object(rtl_calibrate.shutil, "which", return_value=None):
            with self.assertRaises(radio_apps.RadioError):
                rtl_calibrate.start(self.cfg, "GSM900")

    def test_aprs_page_shows_result_and_calibrate_posts(self):
        rtl_calibrate.write_state({"state": "done", "band": "GSM900", "channel": 37, "ppm": 8.437,
                                   "finished_at": 1, "channels": [{"channel": 37, "freq_mhz": 942.4,
                                                                   "offset_hz": -312, "power": 4.2e5}]})
        cfg = {**self.cfg, "web_password": ""}
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")), \
             patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.common, "save_config") as save_config, \
             patch.object(webapp.aprs_rx, "status", return_value={"state": "inactive", "sub_state": "dead",
                                                                  "since": "", "error": None, "enabled": ""}), \
             patch.object(webapp.aprs_rx, "recent_log", return_value=""), \
             patch.object(rtl_calibrate, "is_active", return_value=False), \
             patch.object(webapp.rtl_calibrate, "start") as start:
            common.init_db()
            client = webapp.app.test_client()
            page = client.get("/aprs").get_data(as_text=True)
            self.assertEqual(client.get("/aprs/calibration").get_json()["ppm"], 8.437)
            with client.session_transaction() as browser_session:
                browser_session["_csrf_token"] = "t"
            response = client.post("/aprs", data={"csrf_token": "t", "action": "calibrate",
                                                  "rtl_cal_band": "EGSM", "rtl_cal_channel": "", "rtl_cal_gain": "30"})
        self.assertIn("+8.437 ppm", page)
        self.assertIn('id="cal-apply" data-ppm="8"', page)
        self.assertIn('id="aprs-ppm"', page)
        self.assertEqual(response.status_code, 302)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[1:], ("EGSM", ""))
        self.assertEqual(save_config.call_args.args[0]["rtl_cal_gain"], "30")


if __name__ == "__main__":
    unittest.main()
