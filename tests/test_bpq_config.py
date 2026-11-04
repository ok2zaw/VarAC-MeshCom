import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import bpq_config
import common
import radio_apps
import webapp


class BpqConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {
            **common.DEFAULT_CONFIG,
            "linbpq_path": os.path.join(self.tmp.name, "linbpq"),
            "tnc1_enabled": True,
            "tnc2_enabled": True,
            "bpq_nodecall": "OK2ZAW-7",
            "bpq_nodealias": "ZAWNOD",
            "bpq_locator": "JN89AA",
            "bpq_bbs_call": "OK2ZAW-1",
            "bpq_bbs_alias": "ZAWBBS",
        }
        self.path = os.path.join(self.tmp.name, "bpq32.cfg")

    def test_render_has_node_telnet_two_kiss_ports_and_bbs(self):
        text = bpq_config.render(self.cfg, "Tajne123")
        for line in ("SIMPLE", "NODECALL=OK2ZAW-7", "NODEALIAS=ZAWNOD", "LOCATOR=JN89AA", "NODE=1", "BBS=1",
                     "INFOMSG:", "LinBPQ uzel s BBS pres LoRa", "***", " DRIVER=TELNET", "  TCPPORT=8010",
                     "  HTTPPORT=8008", "  USER=sysop,Tajne123,OK2ZAW,,SYSOP", " ID=LoRa TNC1", " PORTNUM=2",
                     " TCPPORT=10001", " ID=LoRa TNC2", " PORTNUM=3", " TCPPORT=10002", " PROTOCOL=KISS",
                     " IPADDR=127.0.0.1", " PACLEN=128", " MAXFRAME=1", " FRACK=20000", " RESPTIME=1000",
                     " RETRIES=10", "APPLICATION 1,BBS,,OK2ZAW-1,ZAWBBS,255", "LINMAIL"):
            self.assertIn(line, text.splitlines(), line)
        self.assertNotIn("CTEXT:", text)
        self.assertNotIn("LOCALNET", text)

        one_tnc = bpq_config.render({**self.cfg, "tnc2_enabled": False, "bpq_bbs_enabled": False,
                                     "bpq_localnet": "192.168.99.7/24", "bpq_ctext": "Ahoj\nvitej"}, "Tajne123")
        self.assertNotIn("TNC2", one_tnc)
        self.assertNotIn("LINMAIL", one_tnc)
        self.assertIn("  LOCALNET=192.168.99.0/24", one_tnc.splitlines())
        self.assertIn("CTEXT:\nAhoj\nvitej\n***", one_tnc)

    def test_settings_are_validated(self):
        bad = [
            {"bpq_nodecall": "OK2ZAW-16"}, {"bpq_nodecall": "NOCALL"}, {"bpq_nodealias": "TOOLONG"},
            {"bpq_locator": "XX99"}, {"bpq_localnet": "lan"}, {"bpq_telnet_port": "10001"},
            {"bpq_bbs_call": "OK2ZAW-7"}, {"bpq_bbs_alias": "ZAWNOD"}, {"bpq_info": "a\n***\nb"},
            {"tnc1_frack": "100"}, {"bpq_sysop_user": "sys op"},
        ]
        for override in bad:
            with self.assertRaises(radio_apps.RadioError, msg=override):
                bpq_config.settings({**self.cfg, **override})
        for password in ("short", "with,comma", "semi;colon", "has space"):
            with self.assertRaises(radio_apps.RadioError):
                bpq_config.render(self.cfg, password)
        # Without the BBS its call and alias are not needed.
        bpq_config.settings({**self.cfg, "bpq_bbs_enabled": False, "bpq_bbs_call": "", "bpq_bbs_alias": ""})

    def test_write_keeps_password_backs_up_and_protects_file(self):
        with self.assertRaisesRegex(radio_apps.RadioError, "heslo"):
            bpq_config.write(self.cfg)
        with open(self.path, "w") as handle:
            handle.write("; hand made\nUSER=sysop,Puvodni9,OK2ZAW,,SYSOP\n")
        path, backup = bpq_config.write(self.cfg)
        self.assertEqual(path, self.path)
        self.assertTrue(os.path.basename(backup).startswith("bpq32.cfg.bak-"))
        with open(backup) as handle:
            self.assertIn("hand made", handle.read())
        with open(path) as handle:
            self.assertIn("  USER=sysop,Puvodni9,OK2ZAW,,SYSOP", handle.read().splitlines())
        for name in (path, backup):
            self.assertEqual(stat.S_IMODE(os.stat(name).st_mode), 0o600)
        self.assertEqual(bpq_config.write(self.cfg), (path, None))  # unchanged: no new backup
        bpq_config.write(self.cfg, "Nove1234")
        self.assertEqual(bpq_config.existing_password(path, "sysop"), "Nove1234")
        with self.assertRaisesRegex(radio_apps.RadioError, "neexistuje"):
            bpq_config.write({**self.cfg, "linbpq_path": "/nonexistent/dir/linbpq"}, "Nove1234")

    def test_web_form_saves_settings_and_writes_file(self):
        state = {"state": "inactive", "sub_state": "dead", "since": "", "enabled": "disabled", "error": None}
        stored = {**self.cfg, "web_password": "", "bpq_nodecall": "", "bpq_nodealias": ""}
        form = {"csrf_token": "token", "action": "bpqcfg", "bpq_nodecall": "ok2zaw-7", "bpq_nodealias": "zawnod",
                "bpq_locator": "jn89aa", "bpq_info": "Uzel OK2ZAW\r\nLoRa", "bpq_ctext": "",
                "bpq_telnet_port": "8010", "linbpq_web_port": "8008", "bpq_localnet": "",
                "bpq_sysop_user": "sysop", "bpq_sysop_password": "Tajne123", "bpq_sysop_call": "",
                "bpq_bbs_enabled": "on", "bpq_bbs_call": "ok2zaw-1", "bpq_bbs_alias": "zawbbs",
                **{f"{tnc}_{suffix}": str(self.cfg[f"{tnc}_{suffix}"])
                   for tnc in ("tnc1", "tnc2") for suffix, *_ in bpq_config.AX25_FIELDS}}
        with patch.object(webapp.common, "load_config", return_value=stored), \
             patch.object(webapp.common, "save_config") as save_config, \
             patch.object(webapp.bpq_services, "status", return_value=state), \
             patch.object(webapp.bpq_services, "recent_log", return_value=""):
            client = webapp.app.test_client()
            with client.session_transaction() as session:
                session["_csrf_token"] = "token"
            empty_page = client.get("/bpq").get_data(as_text=True)
            response = client.post("/bpq", data=form)
            rejected = client.post("/bpq", data={**form, "bpq_nodealias": "TOOLONG"})
            page = client.get("/bpq").get_data(as_text=True)
        self.assertIn("Náhled není k dispozici", empty_page)
        self.assertTrue(response.headers["Location"].endswith("/bpq#bpq-config"))
        saved = save_config.call_args_list[0].args[0]
        self.assertEqual((saved["bpq_nodecall"], saved["bpq_locator"], saved["bpq_info"]),
                         ("OK2ZAW-7", "JN89AA", "Uzel OK2ZAW\nLoRa"))
        self.assertNotIn("bpq_sysop_password", saved)
        self.assertEqual(save_config.call_count, 1)  # the invalid form is not saved
        with open(self.path) as handle:
            self.assertIn("  USER=sysop,Tajne123,OK2ZAW,,SYSOP", handle.read().splitlines())
        self.assertIn("Restartujte LinBPQ", page)
        self.assertIn("Alias uzlu: 1 až 6", page)
        self.assertNotIn("Tajne123", page)
        rejected.close()


if __name__ == "__main__":
    unittest.main()
