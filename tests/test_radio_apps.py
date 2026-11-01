import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import common
import radio_apps
import webapp


def completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class RadioAppsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.exe = os.path.join(self.tmp.name, "VARA.exe")
        open(self.exe, "w").close()
        self.cfg = {**common.DEFAULT_CONFIG, "radio_vara_path": self.exe}

    def test_wine_app_starts_shared_wineserver_and_transient_unit(self):
        calls = []

        def fake_run(arguments, **kwargs):
            calls.append(arguments)
            if "is-active" in arguments:
                return completed(returncode=3)
            return completed()

        with patch.object(radio_apps.subprocess, "run", side_effect=fake_run), \
             patch.object(radio_apps.shutil, "which", return_value="/usr/local/bin/wineserver"), \
             patch.object(radio_apps.time, "sleep"):
            self.assertTrue(radio_apps.start("vara", self.cfg))

        launches = [call for call in calls if call[0] == "systemd-run"]
        self.assertEqual(len(launches), 2)
        self.assertIn(f"--unit={radio_apps.WINESERVER_UNIT}", launches[0])
        self.assertEqual(launches[0][-3:], ["wineserver", "--foreground", "--persistent"])
        self.assertIn("--unit=meshcom-radio-vara.service", launches[1])
        self.assertIn("--setenv=WINEPREFIX=/home/pi/.wine", launches[1])
        self.assertEqual(launches[1][-2:], ["wine", self.exe])

    def test_running_app_is_not_started_twice(self):
        with patch.object(radio_apps.subprocess, "run", return_value=completed()) as run:
            self.assertFalse(radio_apps.start("vara", self.cfg))
        self.assertEqual(run.call_count, 1)

    def test_mercury_command_is_restricted_and_port_is_parsed(self):
        with self.assertRaises(radio_apps.RadioError):
            radio_apps.app_command("mercury", {**self.cfg, "radio_mercury_command": "/bin/sh"})
        with patch.object(radio_apps.shutil, "which", return_value="/usr/bin/mercury"):
            command = radio_apps.app_command("mercury", {**self.cfg, "radio_mercury_args": "-x alsa -p 8400"})
        self.assertEqual(command, ["mercury", "-x", "alsa", "-p", "8400"])
        self.assertEqual(radio_apps.mercury_port({"radio_mercury_args": "-x alsa -p8500"}), 8500)
        self.assertEqual(radio_apps.mercury_port({"radio_mercury_args": "-x alsa"}), 8300)

    def test_wine_path_must_exist_and_be_exe(self):
        for path in ("/tmp/script.sh", os.path.join(self.tmp.name, "missing.exe")):
            with self.assertRaises(radio_apps.RadioError):
                radio_apps.app_command("varac", {**self.cfg, "radio_varac_path": path})

    def test_autostart_starts_enabled_apps_in_delay_order(self):
        cfg = {
            **self.cfg,
            "radio_vara_autostart": True, "radio_vara_delay": 10,
            "radio_mercury_autostart": False,
            "radio_varac_autostart": True, "radio_varac_delay": 40,
        }
        now = [100.0]
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with patch.object(radio_apps, "start", return_value=True) as start, \
             patch.object(radio_apps, "_systemctl"):
            radio_apps.run_autostart(cfg, sleep=fake_sleep, clock=lambda: now[0])

        self.assertEqual([call.args[0] for call in start.call_args_list], ["vara", "varac"])
        self.assertEqual(sleeps, [10, 30])

    def test_autostart_entry_follows_configuration(self):
        entry = os.path.join(self.tmp.name, "autostart", "meshcom-radio.desktop")
        with patch.object(radio_apps, "AUTOSTART_FILE", entry):
            self.assertTrue(radio_apps.sync_autostart_entry({**self.cfg, "radio_varac_autostart": True}))
            with open(entry, encoding="utf-8") as handle:
                self.assertRegex(handle.read(), r'Exec="[^"]+" "[^"]*radio_apps\.py" autostart\n')
            self.assertFalse(radio_apps.sync_autostart_entry(self.cfg))
        self.assertFalse(os.path.exists(entry))

    def test_web_start_requires_csrf_and_calls_radio_apps(self):
        with patch.object(webapp.common, "load_config", return_value={**self.cfg, "web_password": ""}), \
             patch.object(webapp.radio_apps, "start", return_value=True) as start:
            client = webapp.app.test_client()
            self.assertEqual(client.post("/radio", data={"action": "start", "app": "vara"}).status_code, 400)
            with client.session_transaction() as browser_session:
                browser_session["_csrf_token"] = "test-token"
            response = client.post(
                "/radio", data={"action": "start", "app": "vara", "csrf_token": "test-token"}
            )
        self.assertEqual(response.status_code, 302)
        start.assert_called_once()

    def test_web_rejects_invalid_delay(self):
        new_cfg, errors = webapp.save_radio_settings(
            {**{key: self.cfg[key] for key in webapp.RADIO_TEXT_FIELDS},
             "radio_vara_delay": "900", "radio_mercury_delay": "5", "radio_varac_delay": "x"},
            self.cfg,
        )
        self.assertEqual(len(errors), 2)

    def test_radio_page_renders_status(self):
        with patch.object(webapp.common, "load_config", return_value={**self.cfg, "web_password": ""}), \
             patch.object(webapp.radio_apps, "status",
                          return_value={"state": "active", "sub_state": "running", "since": "dnes", "error": None}), \
             patch.object(webapp.radio_apps, "recent_log", return_value="log line"):
            response = webapp.app.test_client().get("/radio")
        self.assertEqual(response.status_code, 200)
        self.assertIn("VarAC".encode(), response.data)
        self.assertIn("log line".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
