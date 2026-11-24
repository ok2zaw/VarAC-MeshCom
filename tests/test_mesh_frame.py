import random
import unittest

import mesh_frame

# Pinned firmware format: little-endian message ID, big-endian byte-sum FCS.
FRAME = bytes.fromhex("3A04030201944F4B314141412D312C4F4B325250542D393E3236323A48656C6C6F7B30303100091309462809617E")


class MeshFrameTests(unittest.TestCase):
    def test_text_paths_flags_fcs_and_trailer(self):
        p = mesh_frame.decode_frame(FRAME)
        self.assertEqual((p["frame_type"], p["decode_status"], p["fcs_ok"]), ("msg", "ok", True))
        self.assertEqual((p["source"], p["heard_from"], p["destination"]), ("OK1AAA-1", "OK2RPT-9", "262"))
        self.assertEqual(p["msg_id"], "01020304")
        self.assertEqual(p["payload_text"], "Hello{001")
        self.assertEqual(p["remaining_hops"], 4)
        self.assertTrue(p["via_server"])
        self.assertEqual((p["hw_id"], p["lora_mod"], p["firmware_byte"]), (9, 3, 40))

    def test_bad_fcs_retains_readable_data_without_claiming_radio_crc(self):
        damaged = FRAME.replace(b"Hello", b"Jello")
        p = mesh_frame.decode_frame(damaged)
        self.assertEqual(p["decode_status"], "bad_fcs")
        self.assertFalse(p["fcs_ok"])
        self.assertEqual(p["payload_text"], "Jello{001")
        self.assertIn("CRC", p["error"])

    def test_rf_ack_has_ids_but_no_invented_sender(self):
        p = mesh_frame.decode_frame(bytes.fromhex("414433221184040302010100"))
        self.assertEqual((p["frame_type"], p["decode_status"]), ("ack", "ok"))
        self.assertEqual((p["msg_id"], p["acked_msg_id"]), ("11223344", "01020304"))
        self.assertIsNone(p["source"])
        self.assertIsNone(p["fcs_ok"])
        self.assertEqual(mesh_frame.decode_frame(b"A wrong packet")["decode_status"], "malformed")

    def test_every_truncation_is_handled_and_unknown_bytes_remain_diagnostic(self):
        for n in range(len(FRAME)):
            with self.subTest(n=n):
                p = mesh_frame.decode_frame(FRAME[:n])
                if n < len(FRAME) - 4:
                    self.assertNotEqual(p["decode_status"], "ok")
        unknown = mesh_frame.decode_frame(b"\x00\xff\x00")
        self.assertEqual(unknown["decode_status"], "unsupported")
        self.assertIsNone(unknown["source"])

    def test_position_and_hn_use_their_own_separators(self):
        for tag, destination, expected in ((b"!", b"*", "pos"), (b"@", b"HN", "hn"), (b"@", b"H", "hey")):
            data = tag + b"\x01\0\0\0\x14" + b"OK1AAA-1>" + destination + tag + b"payload\0\x09\x13"
            data += sum(data).to_bytes(2, "big")
            p = mesh_frame.decode_frame(data)
            self.assertEqual((p["frame_type"], p["decode_status"]), (expected, "ok"))

    def test_arbitrary_received_bytes_cannot_crash_the_inspector(self):
        rng = random.Random(4630)
        for size in range(256):
            for prefix in (0, 0x3A, 0x21, 0x40, 0x41, 0xFF):
                data = bytes([prefix]) + bytes(rng.randrange(256) for _ in range(size))
                p = mesh_frame.decode_frame(data)
                self.assertIn(p["decode_status"], mesh_frame.DECODE_STATES)


if __name__ == "__main__":
    unittest.main()
