import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aprs_rx  # noqa: E402
import common  # noqa: E402
import gateway  # noqa: E402
import radio_apps  # noqa: E402
import webapp  # noqa: E402


def address(call: str, last: bool = False, repeated: bool = False) -> bytes:
    base, _, ssid = call.partition("-")
    encoded = bytes(ord(char) << 1 for char in base.ljust(6))
    flags = 0x60 | (int(ssid or 0) << 1) | (0x01 if last else 0) | (0x80 if repeated else 0)
    return encoded + bytes([flags])


def ui_frame(source: str, destination: str, digis=(), info: str = "") -> bytes:
    calls = [(destination, False), (source, False), *digis]
    data = b"".join(address(call, last=index == len(calls) - 1, repeated=repeated)
                    for index, (call, repeated) in enumerate(calls))
    return data + b"\x03\xf0" + info.encode()


def kiss(frame: bytes) -> bytes:
    escaped = frame.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")
    return b"\xc0\x00" + escaped + b"\xc0"


class AprsRxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {**common.DEFAULT_CONFIG, "aprs_enabled": True, "aprs_callsign": "OK2ZAW-1"}
        self.own = {"latitude": 49.1951, "longitude": 16.6068}

    def test_passcode_matches_aprs_is_hash(self):
        self.assertEqual(aprs_rx.passcode("N0CALL"), 13023)
        self.assertEqual(aprs_rx.passcode("n0call-10"), 13023)

    def test_settings_validate_and_fill_defaults(self):
        values = aprs_rx.settings(self.cfg, self.own)
        self.assertEqual((values["frequency_hz"], values["gain"], values["ppm"], values["device"]),
                         (144800000, None, 0, "0"))
        self.assertEqual(values["passcode"], aprs_rx.passcode("OK2ZAW"))
        self.assertEqual((values["latitude"], values["longitude"], values["position_source"]),
                         (49.1951, 16.6068, "node"))
        manual = aprs_rx.settings({**self.cfg, "aprs_latitude": "50,1", "aprs_longitude": "14.4",
                                   "aprs_gain": "38.6", "aprs_passcode": "12345"}, self.own)
        self.assertEqual((manual["latitude"], manual["gain"], manual["passcode"], manual["position_source"]),
                         (50.1, 38.6, 12345, "form"))
        picked = aprs_rx.settings(self.cfg, {**self.own, "source": "manual"})
        self.assertEqual(picked["position_source"], "manual")
        # No position anywhere: the iGate still works, only the beacon is left out.
        unknown = aprs_rx.settings(self.cfg, None)
        self.assertIsNone(unknown["latitude"])
        self.assertFalse(aprs_rx.beacon_active(unknown))
        self.assertNotIn("PBEACON", aprs_rx.direwolf_config(unknown))
        self.assertIn("IGLOGIN", aprs_rx.direwolf_config(unknown))
        self.assertTrue(aprs_rx.beacon_active(values))
        for bad in ({"aprs_callsign": ""}, {"aprs_callsign": "OK2ZAW-16"}, {"aprs_frequency_mhz": "x"},
                    {"aprs_gain": "60"}, {"aprs_device": "0; rm"}, {"aprs_passcode": "abc"},
                    {"aprs_comment": 'a"b'}, {"aprs_server": "bad host"}):
            with self.assertRaises(radio_apps.RadioError, msg=bad):
                aprs_rx.settings({**self.cfg, **bad}, self.own)

    def test_direwolf_config_and_command(self):
        values = aprs_rx.settings({**self.cfg, "aprs_gain": "40", "aprs_ppm": "-3"}, self.own)
        config = aprs_rx.direwolf_config(values)
        for line in ("ADEVICE null null", "MYCALL OK2ZAW-1", "KISSPORT 8001",
                     "IGSERVER euro.aprs2.net", f"IGLOGIN OK2ZAW-1 {aprs_rx.passcode('OK2ZAW')}"):
            self.assertIn(line + "\n", config)
        self.assertIn('lat=49.19510 long=16.60680 comment="RX iGate (RTL-SDR)"', config)
        self.assertNotIn("\nIGTXVIA", config)  # only mentioned in a comment: no RF transmission
        offline = aprs_rx.direwolf_config({**values, "igate": False})
        self.assertNotIn("IGSERVER", offline)
        self.assertNotIn("PBEACON", offline)
        self.assertEqual(aprs_rx.command(values),
                         "rtl_fm -d 0 -f 144800000 -M fm -s 24000 -E dc -p -3 -g 40 - | "
                         f"direwolf -c {aprs_rx.DIREWOLF_CONFIG} -t 0 -r 24000 -")
        unit = aprs_rx.unit_content(values, {**self.cfg, "aprs_delay": 20})
        self.assertIn("ExecStartPre=/bin/sleep 20\n", unit)
        self.assertIn(f"ExecStart=/bin/sh -c '{aprs_rx.command(values)}'\n", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_write_unit_writes_files_and_enables(self):
        with patch.object(aprs_rx, "UNIT_DIR", os.path.join(self.tmp.name, "units")), \
             patch.object(aprs_rx, "DIREWOLF_CONFIG", os.path.join(self.tmp.name, "dw.conf")), \
             patch.object(aprs_rx, "systemctl") as systemctl:
            aprs_rx.write_unit(self.cfg, self.own)
            self.assertTrue(os.path.exists(aprs_rx.unit_path()))
            self.assertTrue(os.path.exists(aprs_rx.DIREWOLF_CONFIG))
            self.assertIn(("enable", aprs_rx.UNIT), [call.args[:2] for call in systemctl.call_args_list])
            aprs_rx.write_unit({**self.cfg, "aprs_enabled": False})
            self.assertFalse(os.path.exists(aprs_rx.unit_path()))

    def test_igate_state_from_log(self):
        self.assertIsNone(aprs_rx.igate_state("Dire Wolf version 1.7\n"))
        log = "Connect to IGate server euro.aprs2.net ...\nNow connected to IGate server euro.aprs2.net (1.2.3.4)\n"
        self.assertEqual(aprs_rx.igate_state(log), "connected")
        self.assertEqual(aprs_rx.igate_state(log + "Error reading from IGate server.  Closing connection.\n"),
                         "disconnected")

    def test_kiss_and_ax25_decoding(self):
        frame = ui_frame("OK1ABC-9", "APRS", [("OK0DIG-2", True), ("WIDE2-1", False)],
                         "!4903.50N/01601.75E-Test \xc0")
        frames, rest = aprs_rx.kiss_frames(kiss(frame) + b"\xc0\x00partial")
        self.assertEqual((frames, rest), ([frame], b"\xc0\x00partial"))
        packet = aprs_rx.ax25_packet(frame)
        self.assertEqual(packet, {"source": "OK1ABC-9", "destination": "APRS", "path": "OK0DIG-2*,WIDE2-1",
                                  "info": "!4903.50N/01601.75E-Test \xc0", "heard_from": "OK0DIG-2"})
        self.assertEqual(aprs_rx.tnc2(packet), "OK1ABC-9>APRS,OK0DIG-2*,WIDE2-1:!4903.50N/01601.75E-Test \xc0")
        direct = aprs_rx.ax25_packet(ui_frame("OK1ABC", "APRS", [("WIDE1-1", False)], ">status"))
        self.assertEqual(direct["heard_from"], "OK1ABC")
        generic = aprs_rx.ax25_packet(ui_frame("OK1ABC", "APRS", [("WIDE1", True)], ">status"))
        self.assertIsNone(generic["heard_from"])  # repeated by a digi that did not insert its call
        self.assertIsNone(aprs_rx.ax25_packet(frame[:-30]))  # cut inside the address field
        self.assertIsNone(aprs_rx.ax25_packet(ui_frame("OK1ABC", "APRS", info="x")[:-3] + b"\x13\xf0x"))

    def test_decode_positions(self):
        packet = {"source": "OK1ABC-9", "destination": "APRS", "path": "", "info": "!4903.50N/01601.75E-Test"}
        decoded = aprs_rx.decode(packet)
        self.assertEqual((decoded["packet_format"], decoded["symbol_table"], decoded["symbol"], decoded["comment"]),
                         ("uncompressed", "/", "-", "Test"))
        self.assertAlmostEqual(decoded["latitude"], 49.0583, places=3)
        self.assertIsNone(aprs_rx.decode({**packet, "info": "garbage"})["latitude"])

    def test_monitor_stores_packets_and_queries(self):
        frame = ui_frame("OK1ABC-9", "APRS", [("OK0DIG-2", True)], "!4903.50N/01601.75E-Test")
        with patch.object(common, "DB_PATH", os.path.join(self.tmp.name, "gateway.db")):
            common.init_db()
            stored = gateway.store_aprs_frame(frame)
            gateway.store_aprs_frame(ui_frame("OK1ABC-9", "APRS", [], ">only status"))
            gateway.store_aprs_frame(ui_frame("OK2XYZ", "APRS", [], ">hello"))
            self.assertIsNone(gateway.store_aprs_frame(b"\x00\x01"))
            stations = common.get_aprs_stations(time.time() - 3600)
            packets = common.get_aprs_packets(time.time() - 3600, "ok1abc-9")
            counts = common.get_aprs_counts()
            common.prune_aprs_packets(0)
            self.assertEqual(common.get_aprs_counts()["day"], 0)

        self.assertEqual(stored["heard_from"], "OK0DIG-2")
        self.assertEqual([row["source"] for row in stations], ["OK2XYZ", "OK1ABC-9"])
        abc = stations[1]
        self.assertEqual((abc["packets"], abc["direct_packets"], abc["heard_from"], abc["symbol"]),
                         (2, 1, "OK1ABC-9", "-"))
        self.assertAlmostEqual(abc["latitude"], 49.0583, places=3)  # from the older position packet
        self.assertEqual(len(packets), 2)
        self.assertEqual((counts["hour"], counts["day"], counts["stations"]), (3, 3, 2))

    def test_monitor_reads_kiss_over_tcp(self):
        import socket
        import threading
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        frame = ui_frame("OK1ABC-9", "APRS", [], "!4903.50N/01601.75E-Test")
        data = kiss(frame)

        def serve():
            connection, _ = server.accept()
            connection.sendall(data[:7])  # a frame split over two reads
            time.sleep(0.05)
            connection.sendall(data[7:])
            connection.close()

        threading.Thread(target=serve, daemon=True).start()
        stop = threading.Event()
        with patch.object(common, "DB_PATH", os.path.join(self.tmp.name, "gateway.db")), \
             patch.object(gateway.common, "load_config",
                          return_value={**self.cfg, "aprs_kiss_port": port}):
            common.init_db()
            gateway.aprs_monitor(stop, sleep=lambda seconds: stop.set())
            packets = common.get_aprs_packets(0)
            self.assertIsNotNone(common.get_heartbeat(gateway.APRS_MONITOR_HEARTBEAT))
        server.close()
        self.assertEqual([row["source"] for row in packets], ["OK1ABC-9"])

    def test_aprs_page_renders_and_saves(self):
        with patch.object(common, "DB_PATH", os.path.join(self.tmp.name, "gateway.db")):
            common.init_db()
            gateway.store_aprs_frame(ui_frame("OK1ABC-9", "APRS", [], "!4903.50N/01601.75E-Test"))
            with patch.object(webapp.common, "load_config", return_value={**self.cfg, "web_password": ""}), \
                 patch.object(webapp.common, "get_own_position", return_value=self.own), \
                 patch.object(webapp.aprs_rx, "status",
                              return_value={"state": "active", "sub_state": "running", "since": "dnes",
                                            "error": None, "enabled": "enabled"}), \
                 patch.object(webapp.aprs_rx, "recent_log",
                              return_value="Now connected to IGate server euro.aprs2.net (1.2.3.4)"), \
                 patch.object(webapp.aprs_rx, "missing_programs", return_value=[]):
                page = webapp.app.test_client().get("/aprs").get_data(as_text=True)
        self.assertIn("🟢 Připojeno", page)
        self.assertIn("OK1ABC-9&gt;APRS:!4903.50N/01601.75E-Test", page)
        self.assertIn('"source": "OK1ABC-9"', page)

        form = {"aprs_enabled": "on", "aprs_igate": "on", "aprs_callsign": "ok2zaw-1",
                "aprs_frequency_mhz": "144.800", "aprs_ppm": "2", "aprs_kiss_port": "8001",
                "aprs_delay": "0", "aprs_keep_days": "30", "aprs_device": "0", "aprs_server": "euro.aprs2.net"}
        with patch.object(webapp.common, "get_own_position", return_value=None):
            new_cfg, errors = webapp.save_aprs_settings(form, self.cfg)
        self.assertEqual(errors, [])
        self.assertEqual((new_cfg["aprs_callsign"], new_cfg["aprs_ppm"], new_cfg["aprs_beacon"]),
                         ("OK2ZAW-1", 2, False))
        with patch.object(webapp.common, "get_own_position", return_value=None):
            _, errors = webapp.save_aprs_settings({**form, "aprs_beacon": "on", "aprs_keep_days": "0"}, self.cfg)
        self.assertEqual(len(errors), 1)  # keep days out of range; a missing position is not an error
        # Disabled reception is saved without validating the rest.
        _, errors = webapp.save_aprs_settings({"aprs_callsign": ""}, self.cfg)
        self.assertEqual(errors, [])

    def test_aprs_page_uses_typed_position_for_map_and_distance(self):
        self.assertIsNone(aprs_rx.configured_position(self.cfg))
        self.assertIsNone(aprs_rx.configured_position({**self.cfg, "aprs_latitude": "95", "aprs_longitude": "16"}))
        cfg = {**self.cfg, "web_password": "", "aprs_latitude": "49,1951", "aprs_longitude": "16.6068"}
        self.assertEqual(aprs_rx.configured_position(cfg),
                         {"latitude": 49.1951, "longitude": 16.6068, "source": "aprs"})
        with patch.object(common, "DB_PATH", os.path.join(self.tmp.name, "gateway.db")):
            common.init_db()
            gateway.store_aprs_frame(ui_frame("OK1ABC-9", "APRS", [], "!5004.53N/01426.27E-Praha"))
            with patch.object(webapp.common, "load_config", return_value=cfg), \
                 patch.object(webapp.common, "get_own_position", return_value=None), \
                 patch.object(webapp.aprs_rx, "status", return_value={"state": "inactive", "sub_state": "dead",
                                                                      "since": "", "error": None, "enabled": ""}), \
                 patch.object(webapp.aprs_rx, "recent_log", return_value=""):
                page = webapp.app.test_client().get("/aprs").get_data(as_text=True)
        self.assertRegex(page, r"<td>18\d\.\d km</td>")  # Brno - Praha ~ 185 km
        self.assertIn('const own = {"latitude": 49.1951, "longitude": 16.6068, "source": "aprs"};', page)

    def test_port_conflicts_across_tabs(self):
        import ports
        base = {**common.DEFAULT_CONFIG}
        self.assertEqual(ports.conflicts(base), [])
        self.assertEqual(ports.conflicts(self.cfg), [])  # APRS on with defaults: 8001 and 8000 are free
        clash = {**self.cfg, "aprs_kiss_port": 8008}
        self.assertEqual(ports.conflicts(clash), ["Port 8008 používají zároveň: web LinBPQ, Dire Wolf KISS (APRS)."])
        self.assertEqual(len(ports.conflicts(clash, "aprs")), 1)
        self.assertEqual(ports.conflicts(clash, "radio"), [])
        self.assertEqual(ports.conflicts({**self.cfg, "linbpq_web_port": 8000}, "bpq"),
                         ["Port 8000 používají zároveň: web LinBPQ, Dire Wolf AGW (APRS)."])
        # Alternative modems may share 8300; anything else on it is a clash.
        self.assertEqual(ports.conflicts({**base, "radio_mercury_args": "-x alsa"}), [])
        self.assertEqual(ports.conflicts({**base, "tnc1_enabled": True, "tnc2_enabled": True,
                                          "tnc2_tcp_port": 10001}), [])  # checked on the BPQ tab
        with patch.object(webapp.common, "get_own_position", return_value=self.own):
            _, errors = webapp.save_aprs_settings({
                "aprs_enabled": "on", "aprs_callsign": "OK2ZAW-1", "aprs_frequency_mhz": "144.8",
                "aprs_kiss_port": "8010", "aprs_delay": "0", "aprs_keep_days": "30", "aprs_device": "0",
                "aprs_ppm": "0"}, self.cfg)
        self.assertEqual(errors, ["Port 8010 používají zároveň: telnet LinBPQ, Dire Wolf KISS (APRS)."])


if __name__ == "__main__":
    unittest.main()
