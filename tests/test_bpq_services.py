import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import bpq_services
import common
import radio_apps
import webapp


class BpqServicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bpq_dir = os.path.join(self.tmp.name, "linbpq")
        os.makedirs(bpq_dir)
        self.binary = os.path.join(bpq_dir, "linbpq")
        open(self.binary, "w").close()
        os.chmod(self.binary, os.stat(self.binary).st_mode | stat.S_IXUSR)
        self.unit_dir = os.path.join(self.tmp.name, "units")
        self.tnc_dir = os.path.join(self.tmp.name, "meshcom")
        self.cfg = {
            **common.DEFAULT_CONFIG,
            "tnc1_enabled": True,
            "tnc1_autostart": True,
            "tnc1_frequency_mhz": "433.900",
            "tnc2_enabled": True,
            "tnc2_frequency_mhz": "434,1",
            "linbpq_path": self.binary,
            "linbpq_delay": 15,
        }
        self.paths = [
            patch.object(bpq_services, "UNIT_DIR", self.unit_dir),
            patch.object(bpq_services, "TNC_CONFIG_DIR", self.tnc_dir),
            patch.object(bpq_services, "TNC_SCRIPT", "/home/ok2zaw/meshcom-gateway/lora_tnc/Start_lora-tnc.py"),
            patch.object(bpq_services.os.path, "isabs", return_value=True),
            patch.object(bpq_services.os, "access", return_value=True),
        ]
        for item in self.paths:
            item.start()
            self.addCleanup(item.stop)

    def test_defaults_match_dual_tnc_board(self):
        first = bpq_services.tnc_settings("tnc1", self.cfg)
        second = bpq_services.tnc_settings("tnc2", self.cfg)
        self.assertEqual((first["irqPin"], first["TCP_PORT"], first["csId"]), (5, 10001, 0))
        self.assertEqual((second["irqPin"], second["TCP_PORT"], second["csId"]), (19, 10002, 1))
        self.assertEqual(first["frequency"], 433_900_000)
        self.assertEqual(second["frequency"], 434_100_000)
        self.assertEqual(first["sync_word"], 0x12)
        self.assertFalse(first["appendSignalReport"])
        self.assertEqual(bpq_services.validate(self.cfg), [])

    def test_missing_frequency_and_conflicts_are_reported(self):
        cfg = {**self.cfg, "tnc1_frequency_mhz": "", "tnc2_tcp_port": 10001}
        self.assertTrue(any("kmitočet" in error for error in bpq_services.validate(cfg)))
        cfg = {**self.cfg, "tnc2_tcp_port": 10001, "tnc2_irq_pin": 5, "tnc2_cs_id": 0}
        errors = bpq_services.validate(cfg)
        self.assertEqual(len(errors), 3)
        cfg = {**self.cfg, "tnc2_enabled": False, "tnc2_frequency_mhz": ""}
        self.assertEqual(bpq_services.validate(cfg), [])

    def test_invalid_lora_values_are_rejected(self):
        for key, value in (("tnc1_bandwidth", 100000), ("tnc1_spreading_factor", 13),
                           ("tnc1_output_power", 20), ("tnc1_tcp_host", "localhost")):
            with self.assertRaises(radio_apps.RadioError):
                bpq_services.tnc_settings("tnc1", {**self.cfg, key: value})

    def test_units_pass_instance_config_and_order_linbpq(self):
        tnc_unit = bpq_services.unit_content("tnc2", self.cfg)
        self.assertIn(f"Environment=LORA_TNC_CONFIG={os.path.join(self.tnc_dir, 'tnc2.json')}", tnc_unit)
        self.assertIn("ExecStart=/usr/bin/python3 -u /home/ok2zaw/meshcom-gateway/lora_tnc/Start_lora-tnc.py", tnc_unit)
        bpq_unit = bpq_services.unit_content("linbpq", self.cfg)
        self.assertIn("After=meshcom-tnc1.service meshcom-tnc2.service", bpq_unit)
        self.assertIn("ExecStartPre=/bin/sleep 15", bpq_unit)
        with self.assertRaises(radio_apps.RadioError):
            bpq_services.unit_content("linbpq", {**self.cfg, "linbpq_path": "/bin/sh"})

    def test_write_units_writes_json_and_removes_disabled_tnc(self):
        calls = []
        with patch.object(bpq_services, "systemctl", side_effect=lambda *a, **k: calls.append(a)):
            self.assertEqual(bpq_services.write_units(self.cfg), [])
            with open(os.path.join(self.tnc_dir, "tnc1.json"), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["irqPin"], 5)
            self.assertIn(("enable", "meshcom-tnc1.service"), calls)
            self.assertIn(("disable", "meshcom-tnc2.service"), calls)

            bpq_services.write_units({**self.cfg, "tnc2_enabled": False})
        self.assertIn(("disable", "--now", "meshcom-tnc2.service"), calls)
        self.assertEqual(sorted(os.listdir(self.unit_dir)),
                         ["meshcom-linbpq.service", "meshcom-tnc1.service"])

    def test_start_requires_unit_file(self):
        with self.assertRaises(radio_apps.RadioError):
            bpq_services.start("tnc1")

    def test_web_page_renders_and_start_needs_csrf(self):
        state = {"state": "inactive", "sub_state": "dead", "since": "", "enabled": "disabled", "error": None}
        with patch.object(webapp.common, "load_config", return_value={**self.cfg, "web_password": ""}), \
             patch.object(webapp.bpq_services, "status", return_value=state), \
             patch.object(webapp.bpq_services, "recent_log", return_value=""), \
             patch.object(webapp.bpq_services, "start", return_value=True) as start:
            client = webapp.app.test_client()
            page = client.get("/bpq")
            rejected = client.post("/bpq", data={"action": "start", "service": "tnc2"})
            with client.session_transaction() as browser_session:
                browser_session["_csrf_token"] = "test-token"
            accepted = client.post(
                "/bpq", data={"action": "start", "service": "tnc2", "csrf_token": "test-token"}
            )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"TNC2", page.data)
        self.assertIn(":8008/".encode(), page.data)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 302)
        start.assert_called_once_with("tnc2")

    def test_web_save_reports_validation_errors(self):
        form = {f"{tnc}_{field}": str(self.cfg[f"{tnc}_{field}"])
                for tnc in bpq_services.TNCS for field in webapp.BPQ_TNC_FIELDS}
        form.update({"tnc1_enabled": "on", "tnc2_enabled": "on", "tnc2_tcp_port": "10001",
                     "tnc1_delay": "0", "tnc2_delay": "0", "linbpq_delay": "15",
                     "linbpq_path": self.binary, "linbpq_web_port": "8008"})
        _, errors = webapp.save_bpq_settings(form, self.cfg)
        self.assertEqual(errors, ["TNC1 a TNC2 musí mít rozdílný TCP port."])


if __name__ == "__main__":
    unittest.main()
