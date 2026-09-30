import tempfile
import unittest
from email.message import EmailMessage

import gateway


class GatewayEmailTests(unittest.TestCase):
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
