import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TNC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lora_tnc")
sys.path.insert(0, TNC_DIR)
# The TNC needs Raspberry Pi GPIO/SPI libraries; replace them for tests.
for name in ("RPi", "RPi.GPIO", "spidev"):
    sys.modules.setdefault(name, MagicMock())

import KissHelper  # noqa: E402
import LoraAprsKissTnc_sx127x as tnc_module  # noqa: E402


def make_tnc():
    tnc = object.__new__(tnc_module.LoraAprsKissTnc)
    tnc.radio_lock = tnc_module.threading.RLock()
    tnc.tx_idle = tnc_module.threading.Event()
    tnc.tx_idle.set()
    tnc.tx_timeout = 1.0
    tnc.sf, tnc.bandwidth_hz, tnc.codingrate, tnc.preamble_symbols = 12, 125000, 5, 8
    tnc.low_data_rate_optim = True
    tnc.server = MagicMock()
    for method in ("clear_irq_flags", "reset_ptr_rx", "set_mode", "set_dio_mapping",
                   "write_payload", "get_pkt_rssi_value", "get_pkt_snr_value"):
        setattr(tnc, method, MagicMock(return_value=0))
    tnc.get_irq_flags = MagicMock(return_value={"crc_error": 0, "rx_timeout": 0})
    tnc.get_modem_status = MagicMock(return_value={"signal_detected": 0})
    return tnc


class KissTests(unittest.TestCase):
    def test_data_frame_is_unescaped(self):
        frame = bytes([0xC0, 0x00, 0x01, 0xDB, 0xDC, 0x02, 0xDB, 0xDD, 0x03, 0xC0])
        self.assertEqual(KissHelper.decode_kiss_AX25(frame), bytes([0x01, 0xC0, 0x02, 0xDB, 0x03]))

    def test_kiss_parameter_frames_are_not_transmitted(self):
        self.assertIsNone(KissHelper.decode_kiss_AX25(bytes([0xC0, 0x01, 0x32, 0xC0])))

    def test_parser_keeps_frame_after_repeated_fend(self):
        frames = []
        parser = KissHelper.SerialParser(frames.append)
        parser.parse(bytes([0xC0, 0xC0, 0x00, 0x41, 0xC0, 0xC0, 0x00, 0x42, 0xC0]))
        self.assertEqual([bytes(frame) for frame in frames],
                         [bytes([0xC0, 0x00, 0x41, 0xC0]), bytes([0xC0, 0x00, 0x42, 0xC0])])

    def test_encode_round_trip(self):
        payload = bytes([0x10, 0xC0, 0xDB, 0x20])
        encoded = KissHelper.encode_kiss_AX25(payload, "")
        self.assertEqual(KissHelper.decode_kiss_AX25(bytes(encoded)), payload)


class LoraTncTests(unittest.TestCase):
    def test_airtime_matches_semtech_formula(self):
        # SF12, 125 kHz, CR 4/5, preamble 8, LDRO, 20 bytes: 1318.9 ms
        self.assertAlmostEqual(make_tnc().airtime(20), 1.3189, places=3)

    def test_queued_frame_is_sent_without_polling_delay(self):
        tnc = make_tnc()
        tnc.queue = MagicMock()
        tnc.queue.get.side_effect = [b"frame", KeyboardInterrupt()]
        with patch.object(tnc_module.time, "sleep") as sleep, \
             patch.object(tnc_module.BOARD, "teardown"):
            tnc.startListening()
        tnc.write_payload.assert_called_once_with(list(b"frame"))
        sleep.assert_not_called()
        self.assertFalse(tnc.tx_idle.is_set())
        self.assertGreater(tnc.tx_timeout, tnc.airtime(5))

    def test_next_frame_waits_for_tx_done(self):
        tnc = make_tnc()
        tnc.transmit(b"one")
        self.assertFalse(tnc.tx_idle.is_set())
        tnc.on_tx_done()
        self.assertTrue(tnc.tx_idle.is_set())

    def test_missing_tx_done_returns_radio_to_rx(self):
        tnc = make_tnc()
        tnc.tx_idle.clear()
        tnc.tx_timeout = 0.01
        tnc.wait_for_tx_idle()
        self.assertTrue(tnc.tx_idle.is_set())
        tnc.set_mode.assert_called_with(tnc_module.MODE.RXCONT)

    def test_rx_without_payload_still_rearms_receiver(self):
        tnc = make_tnc()
        tnc.read_payload = MagicMock(return_value=[])
        tnc.on_rx_done()
        tnc.clear_irq_flags.assert_called_once()
        tnc.set_mode.assert_called_with(tnc_module.MODE.RXCONT)
        tnc.server.send.assert_not_called()

    def test_received_frame_is_forwarded_after_rx_rearm(self):
        tnc = make_tnc()
        tnc.read_payload = MagicMock(return_value=[0x41, 0x42])
        tnc.on_rx_done()
        tnc.server.send.assert_called_once()
        self.assertEqual(tnc.server.send.call_args.args[0], b"AB")


if __name__ == "__main__":
    unittest.main()
