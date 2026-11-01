import os
import tempfile
import unittest
from datetime import datetime
from email.message import EmailMessage
from unittest.mock import patch

import common
import gateway
import webapp


class GatewayEmailTests(unittest.TestCase):
    def test_station_page_applies_inclusive_date_filter(self):
        with patch.object(webapp.common, "load_config", return_value={"web_password": ""}), \
             patch.object(webapp.common, "get_station_summary", return_value=[]) as summary, \
             patch.object(webapp.common, "count_station_history", return_value=0) as count_history, \
             patch.object(webapp.common, "get_station_history", return_value=[]) as history:
            response = webapp.app.test_client().get(
                "/stations?start=2026-10-01&end=2026-10-30&station=OK1AAA-1"
            )

        self.assertEqual(response.status_code, 200)
        expected_start = datetime(2026, 10, 1).timestamp()
        expected_end = datetime(2026, 10, 31).timestamp()
        summary.assert_called_once_with(expected_start, expected_end)
        count_history.assert_called_once_with(expected_start, expected_end, "OK1AAA-1")
        history.assert_called_once_with(expected_start, expected_end, "OK1AAA-1", limit=500)

    def test_station_observations_store_rf_position_signal_and_telemetry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
                common.init_db()
                position = {
                    "src_type": "lora",
                    "type": "pos",
                    "src": "OK1AAA-1,OK2BBB-2",
                    "msg_id": "0123ABCD",
                    "lat": 50.1,
                    "lat_dir": "N",
                    "long": 14.4,
                    "long_dir": "E",
                    "alt": 240,
                    "batt": 85,
                    "rssi": -91,
                    "snr": 7,
                    "max_hop": 3,
                    "firmware": 40,
                }
                telemetry = {
                    "src_type": "lora",
                    "type": "tele",
                    "src": "OK1AAA-1,OK2BBB-2",
                    "temp1": 18.5,
                    "hum": 62,
                    "qnh": 1018.2,
                }
                no_fix = {
                    **position,
                    "src": "OK3CCC-1",
                    "msg_id": "0123ABCE",
                    "lat": 0,
                    "long": 0,
                }

                self.assertTrue(common.record_station_observation(position, "OK9NODE-1"))
                self.assertTrue(common.record_station_observation(telemetry, "OK9NODE-1"))
                self.assertTrue(common.record_station_observation(no_fix, "OK9NODE-1"))
                self.assertFalse(common.record_station_observation(
                    {**position, "src_type": "udp"}, "OK9NODE-1"
                ))
                self.assertFalse(common.record_station_observation(
                    {**position, "type": []}, "OK9NODE-1"
                ))
                self.assertFalse(common.record_station_observation(position, "OK1AAA-1"))

                summary = common.get_station_summary(0, 4_102_444_800)
                history = common.get_station_history(0, 4_102_444_800, "OK1AAA-1")

        self.assertEqual(len(summary), 2)
        stations = {row["station"]: row for row in summary}
        self.assertEqual(stations["OK1AAA-1"]["heard_from"], "OK2BBB-2")
        self.assertEqual(stations["OK1AAA-1"]["rssi"], -91)
        self.assertEqual(stations["OK1AAA-1"]["latitude"], 50.1)
        self.assertEqual(stations["OK1AAA-1"]["battery_percent"], 85)
        self.assertEqual(stations["OK1AAA-1"]["temp1"], 18.5)
        self.assertEqual(stations["OK1AAA-1"]["humidity"], 62)
        self.assertIsNone(stations["OK3CCC-1"]["latitude"])
        self.assertEqual(len(history), 2)

    def test_web_send_transmits_to_configured_group_and_logs_message(self):
        cfg = {
            "web_password": "",
            "mesh_group": "12345",
            "max_mesh_payload": 20,
            "mesh_node_ip": "192.168.1.50",
            "mesh_udp_port": 1799,
        }
        with patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.gateway, "send_udp_message") as send_udp, \
             patch.object(webapp.common, "log_message") as log_message:
            with webapp.app.test_client() as client:
                with client.session_transaction() as browser_session:
                    browser_session["_csrf_token"] = "test-token"
                response = client.post(
                    "/send",
                    data={"csrf_token": "test-token", "message": "Maintenance starts"},
                )

        self.assertEqual(response.status_code, 302)
        send_udp.assert_called_once_with(cfg, "12345", "Maintenance starts")
        log_message.assert_called_once_with(
            "web_to_mesh", "12345", "Web → MeshCom", "Maintenance starts"
        )

    def test_web_send_rejects_oversized_message(self):
        cfg = {"web_password": "", "mesh_group": "12345", "max_mesh_payload": 5}
        with patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.gateway, "send_udp_message") as send_udp:
            with webapp.app.test_client() as client:
                with client.session_transaction() as browser_session:
                    browser_session["_csrf_token"] = "test-token"
                response = client.post(
                    "/send",
                    data={"csrf_token": "test-token", "message": "Too long"},
                )

        self.assertEqual(response.status_code, 302)
        send_udp.assert_not_called()

    def test_web_send_rejects_missing_csrf_token(self):
        cfg = {"web_password": "", "mesh_group": "12345", "max_mesh_payload": 150}
        with patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.gateway, "send_udp_message") as send_udp:
            response = webapp.app.test_client().post(
                "/send", data={"message": "Hello"}
            )

        self.assertEqual(response.status_code, 400)
        send_udp.assert_not_called()

    def test_udp_monitor_tracks_datagrams_and_latest_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = os.path.join(tmpdir, "gateway.db")
            with patch.object(common, "DB_PATH", database_path):
                common.init_db()
                common.record_udp_activity("rx", "192.168.1.20:1799", "MeshCom zpráva")
                common.record_udp_activity("rx", "192.168.1.21:1799", "neplatný JSON")
                common.record_udp_activity("tx", "192.168.1.50:1799", "chyba", "Network unreachable", count_datagram=False)

                activity = common.get_udp_activity()

        self.assertEqual(activity["rx"]["datagrams"], 2)
        self.assertEqual(activity["rx"]["last_peer"], "192.168.1.21:1799")
        self.assertEqual(activity["rx"]["last_status"], "neplatný JSON")
        self.assertEqual(activity["tx"]["datagrams"], 0)
        self.assertEqual(activity["tx"]["last_error"], "Network unreachable")

    def test_imap_settings_are_available_and_local_queue_writes_messages(self):
        cfg = {
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_user": "gateway@example.com",
            "imap_password": "secret",
            "imap_use_ssl": True,
            "imap_folder": "INBOX",
            "maildir_new": "",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gateway.write_email_to_maildir(tmpdir, b"Test body")
            self.assertTrue(os.path.exists(path))
            self.assertIn(".eml", os.path.basename(path))

        self.assertIn("imap_host", cfg)
        self.assertIn("imap_folder", cfg)
        self.assertEqual(cfg["imap_port"], 993)

    def test_load_config_uses_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write('{"smtp_host": "config.example.com", "mesh_group": "99999"}')

            with patch.object(common, "CONFIG_PATH", config_path), patch.dict(
                os.environ,
                {
                    "MESHCOM_SMTP_HOST": "env.example.com",
                    "MESHCOM_SMTP_PORT": "2525",
                    "MESHCOM_WEB_PASSWORD": "env-pass",
                },
                clear=False,
            ):
                cfg = common.load_config()
                self.assertEqual(cfg["smtp_host"], "env.example.com")
                self.assertEqual(cfg["smtp_port"], 2525)
                self.assertEqual(cfg["web_password"], "env-pass")
                self.assertEqual(cfg["mesh_group"], "99999")

    def test_ensure_maildir_layout_creates_required_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            maildir_root = os.path.join(tmpdir, "Maildir")
            result = common.ensure_maildir_layout(maildir_root)

            self.assertEqual(result["new"], os.path.join(maildir_root, "new"))
            self.assertEqual(result["cur"], os.path.join(maildir_root, "cur"))
            self.assertEqual(result["tmp"], os.path.join(maildir_root, "tmp"))
            self.assertTrue(os.path.isdir(result["new"]))
            self.assertTrue(os.path.isdir(result["cur"]))
            self.assertTrue(os.path.isdir(result["tmp"]))

    def test_validate_config_values_rejects_invalid_ports(self):
        result = webapp.validate_config_values(
            {
                "mesh_udp_port": "abc",
                "smtp_port": "65536",
                "imap_port": "-1",
                "poll_interval_seconds": "0",
                "max_mesh_payload": "0",
            },
            {},
        )

        self.assertFalse(result["ok"])
        self.assertIn("mesh_udp_port", result["errors"]) 
        self.assertIn("smtp_port", result["errors"]) 
        self.assertIn("imap_port", result["errors"]) 
        self.assertIn("poll_interval_seconds", result["errors"]) 
        self.assertIn("max_mesh_payload", result["errors"]) 

    def test_long_email_is_split_and_attachments_are_ignored(self):
        body = "A" * 500
        msg = EmailMessage()
        msg["Subject"] = "Long subject"
        msg["From"] = "Alice <alice@example.com>"
        msg.set_content(body)
        msg.add_attachment(b"binarydata", maintype="application", subtype="pdf", filename="report.pdf")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/mail.eml"
            with open(path, "wb") as f:
                f.write(msg.as_bytes())

            sender, subject, text, attachments = gateway.parse_email_file(path)

            self.assertEqual(subject, "Long subject")
            self.assertEqual(attachments, ["report.pdf"])
            self.assertEqual(text, body)

            chunks = gateway.build_mesh_messages(sender, subject, text, attachments, 120)

            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
            self.assertIn("report.pdf", chunks[0])
            self.assertNotIn("binarydata", " ".join(chunks))


if __name__ == "__main__":
    unittest.main()
