import os
import tempfile
import unittest
from email.message import EmailMessage
from unittest.mock import patch

import common
import gateway


class GatewayEmailTests(unittest.TestCase):
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
