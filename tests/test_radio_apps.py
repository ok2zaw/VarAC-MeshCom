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
        self.assertIn(f"--setenv=WINEPREFIX={common.WINE_PREFIX}", launches[1])
        self.assertIn("--setenv=BOX86_DYNAREC_BIGBLOCK=0", launches[1])
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

    def test_mercury_web_gui_adds_flags_once(self):
        cfg = {**self.cfg, "radio_mercury_args": "-x alsa -p 8400", "radio_mercury_gui": True,
               "radio_mercury_gui_port": 10500, "radio_mercury_no_waterfall": True}
        with patch.object(radio_apps.shutil, "which", return_value="/usr/bin/mercury"):
            command = radio_apps.app_command("mercury", cfg)
            self.assertEqual(command, ["mercury", "-x", "alsa", "-p", "8400", "-G", "-U", "10500", "-W"])
            explicit = radio_apps.app_command("mercury", {**cfg, "radio_mercury_args": "-G -U 11000"})
        self.assertEqual(explicit, ["mercury", "-G", "-U", "11000", "-W"])
        self.assertEqual(radio_apps.mercury_gui_port({**cfg, "radio_mercury_args": "-U 11000"}), 11000)
        with patch.object(radio_apps.shutil, "which", return_value="/usr/bin/mercury"):
            plain = radio_apps.app_command("mercury", {**cfg, "radio_mercury_gui": False})
        self.assertNotIn("-G", plain)

    def test_mercury_gui_port_must_not_clash_with_modem_port(self):
        form = {**{key: self.cfg[key] for key in webapp.RADIO_TEXT_FIELDS},
                "radio_mercury_args": "-p 8400", "radio_vara_delay": "0", "radio_mercury_delay": "0",
                "radio_varac_delay": "0", "radio_mercury_gui": "on", "radio_mercury_gui_port": "8401"}
        _, errors = webapp.save_radio_settings(form, self.cfg)
        self.assertEqual(len(errors), 1)
        new_cfg, errors = webapp.save_radio_settings({**form, "radio_mercury_gui_port": "10088"}, self.cfg)
        self.assertEqual(errors, [])
        self.assertTrue(new_cfg["radio_mercury_gui"])
        _, errors = webapp.save_radio_settings({**form, "radio_mercury_gui_port": "10080"}, self.cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("ERR_UNSAFE_PORT", errors[0])

    def test_browser_blocked_gui_port_falls_back_to_default(self):
        self.assertEqual(radio_apps.mercury_gui_port({"radio_mercury_gui_port": 10080}), 10088)
        with patch.object(radio_apps.shutil, "which", return_value="/usr/bin/mercury"):
            command = radio_apps.app_command("mercury", {**self.cfg, "radio_mercury_args": "-x alsa",
                                                         "radio_mercury_gui": True, "radio_mercury_gui_port": 10080})
        self.assertEqual(command, ["mercury", "-x", "alsa", "-G", "-U", "10088"])

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
             patch.object(radio_apps, "systemctl"):
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
             "radio_vara_delay": "900", "radio_mercury_delay": "5", "radio_varac_delay": "x",
             "radio_mercury_gui_port": "10088"},
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

    def _radio_page(self, cfg, listening):
        with patch.object(webapp.common, "load_config", return_value={**self.cfg, "web_password": "", **cfg}), \
             patch.object(webapp.radio_apps, "status",
                          return_value={"state": "active", "sub_state": "running", "since": "dnes", "error": None}), \
             patch.object(webapp.radio_apps, "recent_log", return_value=""), \
             patch.object(webapp, "port_listening", return_value=listening) as port_listening:
            page = webapp.app.test_client().get("/radio", base_url="http://192.168.1.20:8899").get_data(as_text=True)
        return page, port_listening

    def test_radio_page_links_running_mercury_gui(self):
        page, port_listening = self._radio_page({"radio_mercury_gui": True, "radio_mercury_gui_port": 10080}, True)
        port_listening.assert_called_once_with(10088)
        self.assertIn('class="button-link" href="http://192.168.1.20:10088/mercury.html"', page)
        self.assertIn("waterfall.html", page)

    def test_radio_page_explains_missing_mercury_gui(self):
        page, _ = self._radio_page({"radio_mercury_gui": True, "radio_mercury_gui_port": 10080}, False)
        self.assertNotIn("button-link", page)
        self.assertIn("neodpovídá", page)

        page, port_listening = self._radio_page({"radio_mercury_gui": False}, True)
        port_listening.assert_not_called()
        self.assertNotIn("button-link", page)
        self.assertIn('href="#mercury-gui"', page)

    def xara_cfg(self, **extra):
        return {**self.cfg, "callsign": "OK2ZAW-10", "xara_capture_device": "plughw:CARD=Device,DEV=0",
                "xara_playback_device": "plughw:CARD=Device,DEV=0", **extra}

    def test_xara_config_follows_settings(self):
        text = radio_apps.xara_config_text(self.xara_cfg())
        self.assertEqual(text.splitlines(), [
            radio_apps.XARA_CONFIG_HEADER, "call = OK2ZAW", "hf = 2300", "audio = alsa",
            "capture = plughw:CARD=Device,DEV=0", "playback = plughw:CARD=Device,DEV=0", "radio",
            "drive-db = -5", "ptt = rigctld:127.0.0.1:4532", "addr = 127.0.0.1", "port = 8300",
            "kiss-port = 8100"])
        text = radio_apps.xara_config_text(self.xara_cfg(
            xara_call="ok1abc", xara_mode="fmnarrow", xara_ptt="vox", xara_drive_db=-12.5,
            xara_port=8400, xara_ui_port=8090, rigctld_port=4600))
        for line in ("call = OK1ABC", "fm = narrow", "ptt = vox", "drive-db = -12.5", "port = 8400",
                     "ui-port = 8090", "ui-addr = 0.0.0.0"):
            self.assertIn(line + "\n", text)
        self.assertNotIn("ui-control", text)
        text = radio_apps.xara_config_text(self.xara_cfg(xara_playback_device="plughw:CARD=Other,DEV=1"))
        self.assertIn("capture = plughw:CARD=Device,DEV=0\nplayback = plughw:CARD=Other,DEV=1\n", text)
        # Configs saved before the split still work: one device for both directions.
        legacy = {**self.cfg, "callsign": "OK2ZAW-10", "xara_audio_device": "plughw:CARD=Old,DEV=0"}
        self.assertIn("capture = plughw:CARD=Old,DEV=0\nplayback = plughw:CARD=Old,DEV=0\n",
                      radio_apps.xara_config_text(legacy))
        for bad in ({"xara_capture_device": ""}, {"xara_playback_device": ""},
                    {"xara_capture_device": "hw:0\nui-control"},
                    {"xara_mode": "hf9999"}, {"xara_call": "X"}, {"xara_ptt": "dtr"}):
            with self.assertRaises(radio_apps.RadioError):
                radio_apps.xara_config_text(self.xara_cfg(**bad))

    def test_xara_start_writes_config_and_keeps_hand_written_file(self):
        path = os.path.join(self.tmp.name, "xara", "xara.conf")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("call = MINE\n")
        with patch.object(radio_apps, "XARA_CONFIG_PATH", path), \
             patch.object(radio_apps.shutil, "which", return_value="/usr/local/bin/xara"):
            command = radio_apps.app_command("xara", self.xara_cfg())
            radio_apps.app_command("xara", self.xara_cfg(xara_mode="hf500"))
        self.assertEqual(command, ["xara", "--config", path])
        with open(path + ".bak", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "call = MINE\n")
        with open(path, encoding="utf-8") as handle:
            self.assertIn("hf = 500\n", handle.read())
        with self.assertRaises(radio_apps.RadioError):
            radio_apps.app_command("xara", self.xara_cfg(radio_xara_command="/bin/sh"))

    def test_rigctld_command(self):
        cfg = {**self.cfg, "rigctld_model": 3073, "rigctld_device": "/dev/serial/by-id/usb-Icom-if00",
               "rigctld_speed": 19200}
        with patch.object(radio_apps.shutil, "which", return_value="/usr/bin/rigctld"):
            self.assertEqual(radio_apps.app_command("rigctld", cfg), [
                "rigctld", "-m", "3073", "-r", "/dev/serial/by-id/usb-Icom-if00", "-s", "19200",
                "-T", "127.0.0.1", "-t", "4532"])
            self.assertNotIn("-s", radio_apps.app_command("rigctld", {**cfg, "rigctld_speed": 0}))
            for bad in ({"rigctld_model": ""}, {"rigctld_device": ""}, {"rigctld_device": "/dev/tty USB0"}):
                with self.assertRaises(radio_apps.RadioError):
                    radio_apps.app_command("rigctld", {**cfg, **bad})

    def test_modem_port_conflicts(self):
        cfg = {**self.cfg, "radio_vara_autostart": True, "radio_xara_autostart": True}
        self.assertEqual(radio_apps.port_conflicts(cfg, set()), [("vara", "xara", 8300)])
        self.assertEqual(radio_apps.port_conflicts({**cfg, "xara_port": 8301}, set()), [("vara", "xara", 8300)])
        self.assertEqual(radio_apps.port_conflicts({**cfg, "xara_port": 8400}, set()), [])
        self.assertEqual(radio_apps.port_conflicts(self.cfg, {"xara", "mercury"}), [])  # Mercury default -p 8400
        self.assertEqual(radio_apps.port_conflicts({**self.cfg, "radio_mercury_args": "-x alsa"}, {"xara", "mercury"}),
                         [("xara", "mercury", 8300)])

    def test_sound_devices_split_capture_and_playback(self):
        cards = os.path.join(self.tmp.name, "cards")
        pcm = os.path.join(self.tmp.name, "pcm")
        with open(cards, "w", encoding="utf-8") as handle:
            handle.write(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n                      vc4-hdmi-0\n"
                         " 2 [Device         ]: USB-Audio - USB Audio Device\n"
                         "                      C-Media Electronics Inc. USB Audio Device at usb-xhci\n")
        with open(pcm, "w", encoding="utf-8") as handle:
            handle.write("00-00: MAI PCM i2s-hifi-0 : MAI PCM i2s-hifi-0 : playback 1\n"
                         "02-00: USB Audio : USB Audio : playback 1 : capture 1\n")
        devices = radio_apps.sound_devices(cards, pcm)
        usb = {"value": "plughw:CARD=Device,DEV=0", "usb": True, "label": "USB Audio Device (USB)"}
        self.assertEqual(devices["capture"], [usb])
        self.assertEqual(devices["playback"], [
            usb, {"value": "plughw:CARD=vc4hdmi0,DEV=0", "usb": False, "label": "vc4-hdmi-0 (vc4-hdmi)"}])
        # Without /proc/asound/pcm every card is offered both ways.
        fallback = radio_apps.sound_devices(cards, os.path.join(self.tmp.name, "missing"))
        self.assertEqual(len(fallback["capture"]), 2)
        self.assertEqual(radio_apps.sound_devices(os.path.join(self.tmp.name, "missing")),
                         {"capture": [], "playback": []})

    def test_rig_models_parse_rigctl_list(self):
        output = (" Rig #  Mfg                    Model                   Version         Status      Macro\n"
                  "     1  Hamlib                 Dummy                   20221128.0      Stable      RIG_MODEL_DUMMY\n"
                  "  3073  Icom                   IC-7300                 20230109.15     Stable      RIG_MODEL_IC7300\n")
        radio_apps.rig_models.cache_clear()
        self.addCleanup(radio_apps.rig_models.cache_clear)
        with patch.object(radio_apps.shutil, "which", return_value="/usr/bin/rigctl"), \
             patch.object(radio_apps.subprocess, "run", return_value=completed(stdout=output)):
            self.assertEqual(radio_apps.rig_models(), ((1, "Hamlib Dummy"), (3073, "Icom IC-7300")))

    def test_web_saves_xara_and_rigctld_settings(self):
        form = {key: self.cfg[key] for key in webapp.RADIO_TEXT_FIELDS} | {
            "radio_mercury_gui_port": "10088", "xara_call": "ok1abc", "xara_mode": "hf500",
            "xara_capture_device": "plughw:CARD=Device,DEV=0", "xara_playback_device": "plughw:CARD=Device,DEV=0",
            "xara_ptt": "rigctld", "xara_drive_db": "-8",
            "xara_port": "8400", "xara_ui_port": "8090", "rigctld_model": "3073",
            "rigctld_device": "/dev/ttyUSB0", "rigctld_speed": "19200", "rigctld_port": "4532"}
        new_cfg, errors = webapp.save_radio_settings(form, self.cfg)
        self.assertEqual(errors, [])
        self.assertEqual((new_cfg["xara_call"], new_cfg["xara_mode"], new_cfg["xara_drive_db"],
                          new_cfg["xara_port"], new_cfg["xara_ui_port"], new_cfg["rigctld_model"],
                          new_cfg["rigctld_speed"]), ("OK1ABC", "hf500", -8.0, 8400, 8090, 3073, 19200))
        _, errors = webapp.save_radio_settings({**form, "xara_ui_port": "8401", "xara_mode": "x",
                                                "rigctld_speed": "1234", "xara_drive_db": "3"}, self.cfg)
        self.assertEqual(len(errors), 4)
        new_cfg, errors = webapp.save_radio_settings({**form, "xara_ui_port": "", "rigctld_model": ""}, self.cfg)
        self.assertEqual((errors, new_cfg["xara_ui_port"], new_cfg["rigctld_model"]), ([], 0, ""))

    def test_radio_page_shows_xara_and_port_conflict(self):
        cfg = {"xara_ui_port": 8090, "radio_vara_autostart": True, "radio_xara_autostart": True}
        usb = [{"value": "plughw:CARD=Device,DEV=0", "usb": True, "label": "USB"}]
        with patch.object(webapp.radio_apps, "sound_devices", return_value={"capture": usb, "playback": usb}), \
             patch.object(webapp.radio_apps, "rig_models", return_value=((3073, "Icom IC-7300"),)):
            page, _ = self._radio_page(cfg, True)
        self.assertIn('href="http://192.168.1.20:8090/"', page)
        self.assertIn("VARA a XARA používají TCP port 8300/8301", page)
        self.assertIn('<option value="plughw:CARD=Device,DEV=0" >', page)
        self.assertIn('<option value="3073">Icom IC-7300</option>', page)

if __name__ == "__main__":
    unittest.main()
