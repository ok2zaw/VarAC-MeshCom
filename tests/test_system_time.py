import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common  # noqa: E402
import system_time  # noqa: E402
import webapp  # noqa: E402

SHOW = "Timezone=Europe/Prague\nLocalRTC=no\nCanNTP=yes\nNTP=yes\nNTPSynchronized=yes\n"
SHOW_TIMESYNC = ("SystemNTPServers=tik.cesnet.cz\nFallbackNTPServers=0.debian.pool.ntp.org 1.debian.pool.ntp.org\n"
                 "ServerName=tik.cesnet.cz\n")
ZONES = "Europe/London\nEurope/Prague\nUTC\n"


def fake_timedatectl(calls, fail=None):
    def run(arguments, check=False, capture_output=True, text=True, timeout=10):
        calls.append(arguments)
        command = arguments[2] if arguments[0] == "timedatectl" else arguments[0]
        if fail and command == fail:
            return subprocess.CompletedProcess(arguments, 1, "", "Interactive authentication required.")
        output = {"show": SHOW, "show-timesync": SHOW_TIMESYNC, "list-timezones": ZONES}.get(command, "")
        return subprocess.CompletedProcess(arguments, 0, output, "")
    return run


class SystemTimeTests(unittest.TestCase):
    def test_status_reads_timedatectl(self):
        calls = []
        with patch.object(system_time.subprocess, "run", side_effect=fake_timedatectl(calls)), \
             patch.object(system_time, "ntp_helper_ready", return_value=True):
            state = system_time.status()
        self.assertEqual((state["timezone"], state["ntp"], state["synchronized"]), ("Europe/Prague", True, True))
        self.assertEqual((state["server"], state["servers"]), ("tik.cesnet.cz", "tik.cesnet.cz"))
        self.assertIsNone(state["error"])
        self.assertIn(["timedatectl", "--no-ask-password", "show"], calls)

    def test_status_reports_missing_timedatectl(self):
        with patch.object(system_time.subprocess, "run", side_effect=FileNotFoundError("timedatectl")), \
             patch.object(system_time, "ntp_helper_ready", return_value=False):
            state = system_time.status()
        self.assertIn("timedatectl", state["error"])
        self.assertIsNone(state["ntp"])

    def test_set_timezone_validates_and_explains_polkit(self):
        calls = []
        with patch.object(system_time.subprocess, "run", side_effect=fake_timedatectl(calls)):
            system_time.set_timezone("Europe/London")
            with self.assertRaises(system_time.TimeError):
                system_time.set_timezone("Mars/Base")
        self.assertIn(["timedatectl", "--no-ask-password", "set-timezone", "Europe/London"], calls)
        with patch.object(system_time.subprocess, "run", side_effect=fake_timedatectl([], fail="set-ntp")):
            with self.assertRaisesRegex(system_time.TimeError, "polkit"):
                system_time.set_ntp(False)

    def test_set_time_accepts_browser_format(self):
        calls = []
        with patch.object(system_time.subprocess, "run", side_effect=fake_timedatectl(calls)):
            system_time.set_time("2026-11-04T13:45")
            system_time.set_time("2026-11-04 13:45:07")
            with self.assertRaises(system_time.TimeError):
                system_time.set_time("zítra")
        self.assertEqual([c[3] for c in calls], ["2026-11-04 13:45:00", "2026-11-04 13:45:07"])

    def test_ntp_servers_are_validated_and_requested(self):
        self.assertEqual(system_time.parse_ntp_servers("tik.cesnet.cz, ntp.nic.cz\n2001:718::1"),
                         ["tik.cesnet.cz", "ntp.nic.cz", "2001:718::1"])
        for bad in ("evil;rm", "-x", "a b c d e f g h i"):
            with self.assertRaises(system_time.TimeError):
                system_time.parse_ntp_servers(bad)
        with tempfile.TemporaryDirectory() as tmpdir:
            request = Path(tmpdir) / ".meshcom-ntp-request"
            with patch.object(system_time, "NTP_REQUEST", request), \
                 patch.object(system_time, "ntp_helper_ready", return_value=True):
                system_time.request_ntp_servers("tik.cesnet.cz ntp.nic.cz")
                self.assertEqual(request.read_text(), "tik.cesnet.cz ntp.nic.cz\n")
            with patch.object(system_time, "ntp_helper_ready", return_value=False):
                with self.assertRaisesRegex(system_time.TimeError, "meshcom-ntp"):
                    system_time.request_ntp_servers("ntp.nic.cz")


class SystemTimeWebTests(unittest.TestCase):
    def setUp(self):
        self.client = webapp.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "token"
        self.cfg = patch.object(webapp.common, "load_config",
                                return_value={**common.DEFAULT_CONFIG, "web_password": ""})
        self.cfg.start()
        self.addCleanup(self.cfg.stop)

    def _status(self, **over):
        return {"now": "2026-11-04 13:45:00 CET", "error": None, "timezone": "Europe/Prague", "ntp": True,
                "synchronized": True, "can_ntp": True, "server": None, "servers": "", "fallback_servers": "",
                "helper_ready": True, **over}

    def test_apply_changes_only_what_differs(self):
        with patch.object(webapp.system_time, "status", return_value=self._status()), \
             patch.object(webapp.system_time, "set_timezone") as set_timezone, \
             patch.object(webapp.system_time, "set_ntp") as set_ntp, \
             patch.object(webapp.system_time, "request_ntp_servers") as request_servers:
            response = self.client.post("/config/time", data={
                "csrf_token": "token", "action": "apply", "timezone": "UTC", "ntp": "on", "ntp_servers": ""})
            self.client.post("/config/time", data={
                "csrf_token": "token", "action": "apply", "timezone": "Europe/Prague",
                "ntp_servers": "ntp.nic.cz"})
        self.assertTrue(response.headers["Location"].endswith("/config#system-time"))
        set_timezone.assert_called_once_with("UTC")
        set_ntp.assert_called_once_with(False)
        request_servers.assert_called_once_with("ntp.nic.cz")

    def test_display_utc_is_saved_to_config(self):
        with patch.object(webapp.system_time, "status", return_value=self._status()), \
             patch.object(webapp.common, "save_config") as save_config:
            self.client.post("/config/time", data={"csrf_token": "token", "action": "apply",
                                                   "timezone": "Europe/Prague", "ntp": "on", "display_utc": "on"})
            self.client.post("/config/time", data={"csrf_token": "token", "action": "apply",
                                                   "timezone": "Europe/Prague", "ntp": "on"})
        save_config.assert_called_once()
        self.assertTrue(save_config.call_args.args[0]["display_utc"])

    def test_errors_are_flashed_and_page_shows_time(self):
        with patch.object(webapp.system_time, "status", return_value=self._status(ntp=False)), \
             patch.object(webapp.system_time, "timezones", return_value=["Europe/Prague", "UTC"]), \
             patch.object(webapp.system_time, "set_time",
                          side_effect=webapp.system_time.TimeError("chybí polkit")), \
             patch.object(webapp.common, "get_email_aliases", return_value={}):
            self.client.post("/config/time", data={"csrf_token": "token", "action": "set_time",
                                                   "manual_time": "2026-11-04T13:45"})
            page = self.client.get("/config").get_data(as_text=True)
        self.assertIn("Čas: chybí polkit", page)
        self.assertIn("<option selected>Europe/Prague</option>", page)
        self.assertIn("2026-11-04 13:45:00 CET", page)
        self.assertIn('value="set_time" class="fieldset-button"\n              >', page)


if __name__ == "__main__":
    unittest.main()
