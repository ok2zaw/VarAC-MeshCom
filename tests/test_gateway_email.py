import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from email.message import EmailMessage
from unittest.mock import patch

import common
import gateway
import update_manager
import webapp


class GatewayEmailTests(unittest.TestCase):
    def test_update_check_shows_apply_only_when_restart_helper_is_ready(self):
        state = {"version": "0.1", "commit": "abc123", "branch": "main"}
        check = {
            **state,
            "latest_version": "0.1",
            "latest_commit": "def456",
            "commits_behind": 1,
            "update_available": True,
            "blockers": [],
            "can_update": True,
        }
        with patch.object(webapp.common, "load_config", return_value={"web_password": ""}), \
             patch.object(update_manager, "current_state", return_value=state), \
             patch.object(update_manager, "check_for_updates", return_value=check), \
             patch.object(update_manager, "check_restart_helper") as helper_check:
            with webapp.app.test_client() as client:
                with client.session_transaction() as browser_session:
                    browser_session["_csrf_token"] = "test-token"
                response = client.post(
                    "/updates", data={"action": "check", "csrf_token": "test-token"}
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Aktualizovat a restartovat".encode(), response.data)
        helper_check.assert_called_once_with()

    def test_update_check_requires_csrf_token(self):
        with patch.object(webapp.common, "load_config", return_value={"web_password": ""}), \
             patch.object(update_manager, "check_for_updates") as check_for_updates:
            response = webapp.app.test_client().post(
                "/updates", data={"action": "check"}
            )

        self.assertEqual(response.status_code, 400)
        check_for_updates.assert_not_called()

    def test_update_manager_does_not_merge_when_manual_steps_are_required(self):
        blocked_check = {
            "can_update": False,
            "blockers": ["Aktualizace mění systémové soubory."],
        }
        with patch.object(update_manager, "check_for_updates", return_value=blocked_check), \
             patch.object(update_manager, "_git") as git:
            with self.assertRaises(update_manager.UpdateError):
                update_manager.apply_update()

        git.assert_not_called()

    def test_mesh_messages_are_stored_per_channel_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
                common.init_db()
                group = {
                    "src_type": "lora",
                    "type": "msg",
                    "src": "OK1AAA-1,OK2BBB-2",
                    "dst": "OK3CCC-3,54321",
                    "msg": "RF check{034",
                    "msg_id": "0123A0CD",
                    "rssi": -94,
                    "snr": 5,
                }

                self.assertTrue(common.record_rf_message(group, "OK9NODE-1"))
                # Firmware retry: attempt counter in bits 10-11 of msg_id.
                self.assertFalse(common.record_rf_message({**group, "msg_id": "0123A4CD"}, "OK9NODE-1"))
                # Relay copy of a foreign frame from the node itself.
                self.assertFalse(common.record_rf_message(
                    {**group, "src_type": "node", "msg_id": "11111111"}, "OK9NODE-1"))
                self.assertFalse(common.record_rf_message(
                    {**group, "msg": "OK1AAA-1:ack034", "msg_id": "22222222"}, "OK9NODE-1"))
                self.assertFalse(common.record_rf_message(
                    {**group, "msg": "{CET}2026-11-03 18:00:00", "msg_id": "33333333"}, "OK9NODE-1"))
                self.assertTrue(common.record_rf_message(
                    {**group, "dst": "*", "msg": "CQ", "msg_id": "44444444"}, "OK9NODE-1"))
                self.assertTrue(common.record_rf_message(
                    {"src_type": "node", "type": "msg", "src": "OK9NODE-1", "dst": "OK1AAA-1",
                     "msg": "73{001", "msg_id": "55555555"}, "OK9NODE-1"))

                rows = common.get_heard_rf_messages(0, 4_102_444_800, "54321")
                direct = common.get_heard_rf_messages(0, 4_102_444_800, kind="direct")
                groups = common.count_heard_rf_messages(0, 4_102_444_800, kind="group")
                channels = common.get_rf_channel_summary(0, 4_102_444_800)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["station"], "OK1AAA-1")
        self.assertEqual(rows[0]["source_path"], "OK1AAA-1,OK2BBB-2")
        self.assertEqual(rows[0]["body"], "RF check")
        self.assertEqual(rows[0]["rssi"], -94)
        self.assertEqual([(r["channel"], r["src_type"], r["body"]) for r in direct],
                         [("OK1AAA-1", "node", "73")])
        self.assertEqual(groups, 1)
        self.assertEqual([(c["channel"], c["kind"]) for c in channels],
                         [("*", "broadcast"), ("54321", "group"), ("OK1AAA-1", "direct")])

    def test_group_message_is_emailed_once_and_own_echo_is_not(self):
        cfg = {"listen_ip": "127.0.0.1", "mesh_udp_port": 1799, "mesh_group": "12345",
               "callsign": "OK9NODE-1"}
        packet = {"src_type": "lora", "type": "msg", "src": "OK1AAA-1", "dst": "12345",
                  "msg": "Hello{012", "msg_id": "0000ABCD"}
        datagrams = [
            json.dumps(packet).encode(),
            json.dumps({**packet, "msg_id": "0000AFCD"}).encode(),  # retry
            json.dumps({**packet, "src_type": "node", "src": "OK9NODE-1", "msg_id": "00001111"}).encode(),
        ]

        class FakeSocket:
            def recvfrom(self, size):
                if datagrams:
                    return datagrams.pop(0), ("192.168.1.50", 1799)
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")), \
             patch.object(gateway.common, "load_config", return_value=cfg), \
             patch.object(gateway, "bind_udp_listener", return_value=FakeSocket()), \
             patch.object(gateway, "send_email") as send_email, \
             patch.object(gateway.common, "log_message"):
            common.init_db()
            with self.assertRaises(KeyboardInterrupt):
                gateway.udp_listener()
            statuses = [p["status"] for p in common.get_udp_packets()]

        send_email.assert_called_once_with(cfg, "[MeshCom 12345] OK1AAA-1", "From: OK1AAA-1\nGroup: 12345\n\nHello")
        self.assertEqual(statuses, ["vlastní zpráva (echo uzlu)", "duplikát, potvrzení nebo echo",
                                    "MeshCom zpráva → e-mail"])

    def test_mesh_email_command_selects_recipient(self):
        cfg = {"mesh_email_forward": True, "mesh_email_addresses": True, "mesh_email_all_domains": True,
               "mesh_email_domains": ""}
        aliases = {"OK1AAA": "ok1aaa@example.net"}
        resolve = lambda text, **over: gateway.mesh_email_recipient({**cfg, **over}, text, aliases)
        self.assertEqual(resolve("EMAIL: jan@example.net Ahoj, zítra v 18:00"),
                         ("jan@example.net", "Ahoj, zítra v 18:00", None))
        self.assertEqual(resolve("e-mail:jan@example.net, test"), ("jan@example.net", "test", None))
        self.assertEqual(resolve("EMAIL: jan@example.net"), ("jan@example.net", "(bez textu)", None))
        self.assertEqual(resolve("Ahoj EMAIL: jan@example.net"), (None, "Ahoj EMAIL: jan@example.net", None))
        self.assertEqual(resolve("EMAIL: jan@x text")[2], "neplatná adresa jan@x")
        self.assertEqual(resolve("EMAIL: jan@example.net x", mesh_email_forward=False),
                         (None, "EMAIL: jan@example.net x", None))

        # Aliases work in every mode, case-insensitively; unknown ones fall back to mail_to.
        self.assertEqual(resolve("EMAIL: ok1aaa Ahoj", mesh_email_addresses=False),
                         ("ok1aaa@example.net", "Ahoj", None))
        self.assertEqual(resolve("EMAIL: OK2BBB Ahoj"), (None, "EMAIL: OK2BBB Ahoj", "neznámý alias OK2BBB"))

        only_aliases = resolve("EMAIL: jan@example.net x", mesh_email_addresses=False)
        self.assertEqual(only_aliases[:2], (None, "EMAIL: jan@example.net x"))
        self.assertIn("jen aliasy", only_aliases[2])

        limited = {"mesh_email_all_domains": False, "mesh_email_domains": "example.net, @Example.com"}
        self.assertEqual(resolve("EMAIL: Jan@EXAMPLE.com x", **limited)[0], "Jan@EXAMPLE.com")
        address, text, refused = resolve("EMAIL: jan@example.org x", **limited)
        self.assertIsNone(address)
        self.assertEqual(text, "EMAIL: jan@example.org x")
        self.assertIn("není povolena", refused)
        self.assertIn("není povolena", resolve("EMAIL: jan@example.net x", mesh_email_all_domains=False)[2])

    def test_web_times_follow_pi_zone_or_utc(self):
        with patch.dict(os.environ, {"TZ": "Europe/Prague"}):
            time.tzset()
            try:
                rows = [{"ts": "2026-11-04 12:00:00"}, {"ts": "2026-07-04 12:00:00"}, {"ts": None}]
                self.assertEqual([r["ts"] for r in webapp.format_message_rows([dict(r) for r in rows], {})],
                                 ["2026-11-04 13:00:00", "2026-07-04 14:00:00", None])
                self.assertEqual([r["ts"] for r in webapp.format_message_rows([dict(r) for r in rows],
                                                                              {"display_utc": True})][:2],
                                 ["2026-11-04 12:00:00 UTC", "2026-07-04 12:00:00 UTC"])
                epoch = 1793793600  # 2026-11-04 12:00:00 UTC
                self.assertEqual(webapp.format_ts(epoch, {}, "%H:%M"), "13:00")
                self.assertEqual(webapp.format_ts(epoch, {"display_utc": True}, "%H:%M"), "12:00 UTC")
                with webapp.app.test_request_context("/?start=2026-11-04&end=2026-11-04"):
                    local = webapp.parse_date_range(7, {})
                    utc = webapp.parse_date_range(7, {"display_utc": True})
                self.assertEqual(local[2], epoch - 13 * 3600)
                self.assertEqual(utc[2:], (epoch - 12 * 3600, epoch + 12 * 3600))
            finally:
                os.environ.pop("TZ", None)
                time.tzset()

    def test_email_aliases_are_stored_uppercase_and_validated(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
            common.init_db()
            self.assertEqual(common.set_email_alias(" ok2abu ", "ok2abu@example.net"), "OK2ABU")
            common.set_email_alias("OK2ABU", "jan@example.net")
            common.set_email_alias("OK1AAA-1", "ok1aaa@example.net")
            for alias, email in (("", "a@example.net"), ("OK 1", "a@example.net"), ("OK1", "nope")):
                with self.assertRaises(ValueError):
                    common.set_email_alias(alias, email)
            self.assertEqual(common.get_email_aliases(),
                             {"OK1AAA-1": "ok1aaa@example.net", "OK2ABU": "jan@example.net"})
            self.assertEqual(gateway.mesh_email_recipient({"mesh_email_forward": True}, "EMAIL: ok2abu Ahoj"),
                             ("jan@example.net", "Ahoj", None))
            common.delete_email_alias("ok2abu")
            self.assertEqual(list(common.get_email_aliases()), ["OK1AAA-1"])

    def test_web_manages_email_aliases(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")), \
             patch.object(webapp.common, "load_config", return_value={**common.DEFAULT_CONFIG, "web_password": ""}):
            common.init_db()
            client = webapp.app.test_client()
            with client.session_transaction() as session:
                session["_csrf_token"] = "token"
            response = client.post("/config/aliases", data={"csrf_token": "token", "alias": "ok2abu",
                                                            "email": "ok2abu@example.net"})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/config#email-aliases"))
            client.post("/config/aliases", data={"csrf_token": "token", "alias": "bad alias", "email": "x@example.net"})
            page = client.get("/config").get_data(as_text=True)
            self.assertIn("<td>OK2ABU</td>", page)
            self.assertIn("<td>ok2abu@example.net</td>", page)
            self.assertIn("Alias musí mít", page)
            client.post("/config/aliases", data={"csrf_token": "token", "action": "delete", "alias": "OK2ABU"})
            self.assertEqual(common.get_email_aliases(), {})

    def test_mesh_email_command_is_forwarded_only_in_own_group(self):
        cfg = {"listen_ip": "127.0.0.1", "mesh_udp_port": 1799, "mesh_group": "12345",
               "callsign": "OK9NODE-1", "mesh_email_forward": True, "mesh_email_addresses": True,
               "mesh_email_all_domains": True, "mesh_email_domains": ""}
        packet = {"src_type": "lora", "type": "msg", "src": "OK1AAA-1", "dst": "12345",
                  "msg": "EMAIL: jan@example.net Ahoj Jane{013", "msg_id": "0000ABCD"}
        datagrams = [
            json.dumps(packet).encode(),
            json.dumps({**packet, "dst": "262", "msg_id": "0000BBBB"}).encode(),
        ]

        class FakeSocket:
            def recvfrom(self, size):
                if datagrams:
                    return datagrams.pop(0), ("192.168.1.50", 1799)
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")), \
             patch.object(gateway.common, "load_config", return_value=cfg), \
             patch.object(gateway, "bind_udp_listener", return_value=FakeSocket()), \
             patch.object(gateway, "send_email") as send_email, \
             patch.object(gateway.common, "log_message") as log_message:
            common.init_db()
            with self.assertRaises(KeyboardInterrupt):
                gateway.udp_listener()
            statuses = [p["status"] for p in common.get_udp_packets()]

        send_email.assert_called_once_with(cfg, "[MeshCom 12345] OK1AAA-1",
                                           "From: OK1AAA-1\nGroup: 12345\n\nAhoj Jane", to="jan@example.net")
        log_message.assert_called_once_with("mesh_to_email", "OK1AAA-1",
                                            "[MeshCom 12345] OK1AAA-1 → jan@example.net", "Ahoj Jane", status="ok")
        self.assertEqual(statuses, ["zpráva pro 262", "MeshCom zpráva → jan@example.net"])

    def test_delivery_is_tracked_from_node_echo_and_acks(self):
        own = "OK9NODE-1"
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
            common.init_db()
            group_id = common.record_outgoing_message("web", "12345", "Hello group")
            direct_id = common.record_outgoing_message("email", "OK1AAA-1", "Hi Jan")
            old_fw_id = common.record_outgoing_message("web", "OK2BBB-2", "Old firmware")

            # Node echoes: the group one has no {NNN}, direct ones do.
            self.assertEqual(common.track_delivery({"src_type": "node", "type": "msg", "src": own,
                "dst": "12345", "msg": "Hello group", "msg_id": "AAAA0001"}, own), "echo")
            self.assertEqual(common.track_delivery({"src_type": "node", "type": "msg", "src": own,
                "dst": "DB0RPT-1,OK1AAA-1", "msg": "Hi Jan{007", "msg_id": "AAAA0002"}, own), "echo")
            common.track_delivery({"src_type": "node", "type": "msg", "src": own,
                "dst": "OK2BBB-2", "msg": "Old firmware{042", "msg_id": "AAAA0003"}, own)

            # Relay heard repeating our group message, then the 4.40a JSON acks.
            self.assertEqual(common.track_delivery({"src_type": "lora", "type": "msg", "src": f"{own},DB0RPT-1",
                "dst": "12345", "msg": "Hello group", "msg_id": "AAAA0001"}, own), "heard")
            self.assertEqual(common.track_delivery({"type": "ack", "msg_id": "AAAA0002", "status": 0}, own), "heard")
            self.assertEqual(common.track_delivery(
                {"type": "ack", "msg_id": "AAAA0002", "status": 2, "from": "OK1AAA-1", "via": "udp"}, own), "delivered")
            # A late "failed" never downgrades a confirmed message.
            self.assertIsNone(common.track_delivery({"type": "ack", "msg_id": "AAAA0002", "status": 3}, own))
            # Older firmware: text ACK from the recipient with the {NNN} number.
            self.assertEqual(common.track_delivery({"src_type": "lora", "type": "msg", "src": "OK2BBB-2",
                "dst": own, "msg": f"{own}:ack042", "msg_id": "BBBB0001"}, own), "delivered")
            self.assertIsNone(common.track_delivery({"type": "ack", "msg_id": "FFFF0000", "status": 2}, own))

            rows = {row["id"]: row for row in common.get_outgoing_messages()}
            by_msg_id = common.get_delivery_by_msg_id(["AAAA0002"])

        self.assertEqual(rows[group_id]["status"], "heard")
        self.assertEqual(rows[direct_id]["status"], "delivered")
        self.assertEqual(rows[direct_id]["ack_from"], "OK1AAA-1")
        self.assertEqual(rows[direct_id]["seq"], "007")
        self.assertEqual(rows[old_fw_id]["status_label"], "potvrzeno příjemcem")
        self.assertEqual(by_msg_id["AAAA0002"]["status"], "delivered")

    def test_send_udp_message_records_tracked_messages_only(self):
        cfg = {"max_mesh_payload": 5, "mesh_node_ip": "127.0.0.1", "mesh_udp_port": 9}
        with patch.object(gateway.socket, "socket"), \
             patch.object(gateway, "update_udp_monitor"), \
             patch.object(gateway, "log_udp_packet"), \
             patch.object(gateway.common, "record_outgoing_message") as record:
            gateway.send_udp_message(cfg, "262", "Hello world", source="email")
            gateway.send_udp_message(cfg, "262", "untracked")

        record.assert_called_once_with("email", "262", "Hello")

    def test_email_subject_group_tag_selects_destination(self):
        self.assertEqual(gateway.email_destination("Test", "12345"), ("12345", "Test"))
        self.assertEqual(gateway.email_destination("GROUP: 262", "12345"), ("262", ""))
        self.assertEqual(gateway.email_destination("group:#2620 - Porucha", "12345"), ("2620", "Porucha"))
        self.assertEqual(gateway.email_destination("Re: GROUP: 9 Cvičení", "12345"), ("9", "Re: Cvičení"))
        self.assertEqual(gateway.email_destination("Re: GROUP: 9", "12345"), ("9", ""))
        self.assertEqual(gateway.email_destination("GROUPS: 9", "12345"), ("12345", "GROUPS: 9"))
        self.assertEqual(gateway.build_mesh_messages("Jan <jan@example.com>", "", "Ahoj", [], 150), ["Jan: Ahoj"])

    def test_maildir_email_goes_to_group_from_subject(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            maildir = os.path.join(tmpdir, "new")
            os.makedirs(maildir)
            cfg = {"maildir_new": maildir, "maildir_cur": os.path.join(tmpdir, "cur"),
                   "poll_interval_seconds": 0, "max_mesh_payload": 150, "mesh_group": "12345"}
            sends = []

            def fake_sleep(seconds):
                if sends or os.listdir(maildir):
                    if sends:
                        raise KeyboardInterrupt
                    return
                message = EmailMessage()
                message["From"] = "Jan <jan@example.com>"
                message["Subject"] = "GROUP: 262 Schůzka"
                message.set_content("Zítra v 18:00")
                with open(os.path.join(maildir, "1.eml"), "wb") as handle:
                    handle.write(message.as_bytes())

            with patch.object(gateway.common, "load_config", return_value=cfg), \
                 patch.object(gateway.time, "sleep", side_effect=fake_sleep), \
                 patch.object(gateway, "send_udp_message", side_effect=lambda *a, **k: sends.append((a, k))), \
                 patch.object(gateway.common, "log_message"), \
                 patch.object(gateway.common, "archive_mail_file", return_value="archived"):
                with self.assertRaises(KeyboardInterrupt):
                    gateway.poll_maildir()

        self.assertEqual(sends, [((cfg, "262", "Jan: Schůzka - Zítra v 18:00"), {"source": "email"})])

    def test_mesh_page_filters_by_channel_and_kind(self):
        with patch.object(webapp.common, "load_config", return_value={"web_password": "", "mesh_group": "12345"}), \
             patch.object(webapp.common, "get_rf_channel_summary", return_value=[
                 {"channel": "*", "kind": "broadcast", "messages": 2, "last_at": 0},
                 {"channel": "12345", "kind": "group", "messages": 1, "last_at": 0}]), \
             patch.object(webapp.common, "count_heard_rf_messages", return_value=1) as count, \
             patch.object(webapp.common, "get_heard_rf_messages", return_value=[
                 {"observed_at": 0, "channel": "*", "station": "OK1AAA-1", "body": "CQ CQ",
                  "src_type": "lora", "rssi": -90, "snr": 4, "source_path": "OK1AAA-1",
                  "destination_path": "*"}]) as messages:
            client = webapp.app.test_client()
            page = client.get("/mesh?start=2026-11-01&end=2026-11-03&channel=*").get_data(as_text=True)
            client.get("/mesh?start=2026-11-01&end=2026-11-03&kind=direct")

        since = datetime(2026, 11, 1).timestamp()
        until = datetime(2026, 11, 4).timestamp()
        self.assertEqual(messages.call_args_list[0].args, (since, until, "*"))
        self.assertEqual(messages.call_args_list[1].kwargs["kind"], "direct")
        self.assertIn("CQ CQ", page)
        self.assertIn("* všichni", page)
        self.assertIn("#12345", page)

    def test_udp_packet_decoding_tolerates_terminator_and_bom(self):
        payload = b'{"type": "pos", "src_type": "lora", "src": "OK1AAA-1"}'
        for raw in (payload, payload + b"\x00", b"\xef\xbb\xbf" + payload + b"\r\n"):
            self.assertEqual(gateway.decode_udp_packet(raw)["src"], "OK1AAA-1")

    def test_station_page_applies_inclusive_date_filter(self):
        with patch.object(webapp.common, "load_config", return_value={"web_password": ""}), \
             patch.object(webapp.common, "get_station_summary", return_value=[]) as summary, \
             patch.object(webapp.common, "count_station_history", return_value=0) as count_history, \
             patch.object(webapp.common, "get_station_history", return_value=[]) as history, \
             patch.object(webapp.common, "get_own_position", return_value=None), \
             patch.object(webapp.common, "get_node_position", return_value=None), \
             patch.object(webapp.common, "get_station_paths", return_value=[]):
            response = webapp.app.test_client().get(
                "/stations?start=2026-10-01&end=2026-10-30&station=OK1AAA-1"
            )

        self.assertEqual(response.status_code, 200)
        expected_start = datetime(2026, 10, 1).timestamp()
        expected_end = datetime(2026, 10, 31).timestamp()
        summary.assert_called_once_with(expected_start, expected_end)
        count_history.assert_called_once_with(expected_start, expected_end, "OK1AAA-1")
        history.assert_called_once_with(expected_start, expected_end, "OK1AAA-1", limit=500)

    def test_own_node_position_is_stored_from_node_beacon_only(self):
        beacon = {"src_type": "node", "type": "pos", "src": "OK9NODE-1", "lat": 49.1951,
                  "lat_dir": "N", "long": 16.6068, "long_dir": "E", "alt": 237}
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
            common.init_db()
            self.assertIsNone(common.get_own_position())
            self.assertFalse(common.record_own_position({**beacon, "src_type": "lora"}, "OK9NODE-1"))
            self.assertFalse(common.record_own_position({**beacon, "src": "OK1AAA-1"}, "OK9NODE-1"))
            self.assertFalse(common.record_own_position({**beacon, "lat": 0, "long": 0}, "OK9NODE-1"))
            self.assertTrue(common.record_own_position(beacon, "ok9node-1"))
            self.assertTrue(common.record_own_position({**beacon, "lat": 50.0755, "long": 14.4378}, "OK9NODE-1"))
            position = common.get_own_position()

        self.assertEqual((position["latitude"], position["longitude"]), (50.0755, 14.4378))
        self.assertEqual(position["altitude"], 237)
        # Brno - Praha is about 185 km
        self.assertAlmostEqual(common.distance_km(49.1951, 16.6068, 50.0755, 14.4378), 185, delta=2)

    def test_station_presence_follows_time_since_last_heard(self):
        now = 1_000_000
        self.assertEqual(common.station_presence(now - 44 * 60, now), "recent")
        self.assertEqual(common.station_presence(now - 45 * 60, now), "uncertain")
        self.assertEqual(common.station_presence(now - 119 * 60, now), "uncertain")
        self.assertEqual(common.station_presence(now - 120 * 60, now), "gone")
        self.assertEqual(common.age_label(125), "před 2 min")
        self.assertEqual(common.age_label(3 * 3600 + 5 * 60), "před 3 h 5 min")
        self.assertEqual(common.age_label(2 * 86400 + 10), "před 2 d")

    def test_station_page_draws_map_with_distance_and_direct_links(self):
        summary = [
            {"station": "OK1AAA-1", "heard_from": "OK1AAA-1", "source_path": "OK1AAA-1", "observations": 3,
             "first_seen": 0, "last_seen": 0, "latitude": 50.0755, "longitude": 14.4378, "rssi": -101,
             "snr": 4, "altitude": 300, "battery_percent": 80, "last_packet_type": "pos"},
            {"station": "OK2BBB-2", "heard_from": "OK2RPT-9", "source_path": "OK2BBB-2,OK2RPT-9",
             "observations": 1, "first_seen": 0, "last_seen": 0, "latitude": None, "longitude": None,
             "rssi": -90, "snr": 7, "altitude": None, "battery_percent": None, "last_packet_type": "msg"},
        ]
        with patch.object(webapp.common, "load_config", return_value={"web_password": "", "callsign": "OK9NODE-1"}), \
             patch.object(webapp.common, "get_station_summary", return_value=summary), \
             patch.object(webapp.common, "count_station_history", return_value=0), \
             patch.object(webapp.common, "get_station_history", return_value=[]), \
             patch.object(webapp.common, "get_own_position",
                          return_value={"latitude": 49.1951, "longitude": 16.6068, "altitude": 237,
                                        "source": "manual"}), \
             patch.object(webapp.common, "get_node_position", return_value=None), \
             patch.object(webapp.common, "get_station_paths", return_value=[
                 {"station": "OK2BBB-2", "source_path": "OK2BBB-2,OK2RPT-9", "observations": 1,
                  "last_seen": 0}]):
            page = webapp.app.test_client().get("/stations").get_data(as_text=True)

        self.assertIn('id="station-map"', page)
        self.assertIn('"station": "OK1AAA-1"', page)
        self.assertIn('"direct": true', page)
        stations_json = next(line for line in page.splitlines() if "const stations =" in line)
        self.assertNotIn('"station": "OK2BBB-2"', stations_json)   # no position, table only
        self.assertIn("184.3 km", page)
        self.assertIn('"presence": "gone"', page)   # last_seen 0 = long ago
        self.assertIn('title="pryč – před', page)
        self.assertIn("leaflet@1.9.4", page)
        self.assertIn('"source_path": "OK2BBB-2,OK2RPT-9"', page)   # routes layer data
        self.assertIn('id="map-routes"', page)
        self.assertIn("poloha uzlu: ruční 49.19510, 16.60680", page)
        self.assertIn('value="clear"', page)

    def test_manual_position_overrides_node_beacon(self):
        beacon = {"src_type": "node", "type": "pos", "src": "OK9NODE-1", "lat": 49.1951,
                  "lat_dir": "N", "long": 16.6068, "long_dir": "E"}
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
            common.init_db()
            common.record_own_position(beacon, "OK9NODE-1")
            self.assertEqual(common.get_own_position()["source"], "node")
            with self.assertRaises(ValueError):
                common.set_manual_position(91, 14)
            with self.assertRaises(ValueError):
                common.set_manual_position("", 14)
            common.set_manual_position("50.07551234", "14.4378")
            common.record_own_position({**beacon, "lat": 48.0}, "OK9NODE-1")   # newer beacon
            manual = common.get_own_position()
            common.clear_manual_position()
            node = common.get_own_position()

        self.assertEqual((manual["source"], manual["latitude"], manual["longitude"]), ("manual", 50.075512, 14.4378))
        self.assertEqual((node["source"], node["latitude"]), ("node", 48.0))

    def test_station_paths_group_distinct_routes(self):
        rows = [("OK1AAA-1", "OK1AAA-1,OK2RPT-9", 100.0), ("OK1AAA-1", "OK1AAA-1,OK2RPT-9", 200.0),
                ("OK1AAA-1", "OK1AAA-1", 150.0), ("OK1AAA-1", "OK1AAA-1", 5000.0)]
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
            common.init_db()
            conn = common.get_db()
            for station, path, ts in rows:
                conn.execute(
                    "INSERT INTO station_observations (observed_at, station, heard_from, source_path, "
                    "src_type, packet_type) VALUES (?, ?, ?, ?, 'lora', 'pos')",
                    (ts, station, path.split(",")[-1], path))
            conn.commit()
            conn.close()
            paths = common.get_station_paths(0, 1000)

        self.assertEqual([(p["source_path"], p["observations"]) for p in paths],
                         [("OK1AAA-1,OK2RPT-9", 2), ("OK1AAA-1", 1)])

    def test_last_positions_reach_before_the_period(self):
        rows = [("OK3MID-1", "pos", 10.0, 49.0, 100.0), ("OK3MID-1", "pos", 11.0, 49.5, 300.0),
                ("OK3MID-1", "msg", None, None, 400.0), ("OK3MID-1", "pos", 12.0, 50.0, 2000.0),
                ("OK4NOPOS-1", "msg", None, None, 300.0)]
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(common, "DB_PATH", os.path.join(tmpdir, "gateway.db")):
            common.init_db()
            conn = common.get_db()
            for station, kind, lon, lat, ts in rows:
                conn.execute(
                    "INSERT INTO station_observations (observed_at, station, heard_from, source_path, "
                    "src_type, packet_type, latitude, longitude) VALUES (?, ?, ?, ?, 'lora', ?, ?, ?)",
                    (ts, station, station, station, kind, lat, lon))
            conn.commit()
            conn.close()
            found = common.get_last_positions({"ok3mid-1", "OK4NOPOS-1"}, until=1000)
            self.assertEqual(common.get_last_positions(set(), until=1000), {})

        self.assertEqual(list(found), ["OK3MID-1"])
        self.assertEqual((found["OK3MID-1"]["latitude"], found["OK3MID-1"]["longitude"]), (49.5, 11.0))

    def test_station_map_gets_positions_of_silent_relays(self):
        now = time.time()
        summary = [{"station": "OK1AAA-1", "heard_from": "OK2RPT-9", "source_path": "OK1AAA-1,OK3MID-1,OK2RPT-9",
                    "observations": 1, "first_seen": now, "last_seen": now, "latitude": 50.0, "longitude": 14.4,
                    "rssi": -100, "snr": 3, "altitude": None, "battery_percent": None, "last_packet_type": "pos"}]
        paths = [{"station": "OK1AAA-1", "source_path": "OK1AAA-1,OK3MID-1,OK2RPT-9", "observations": 1,
                  "last_seen": now}]
        relay = {"OK3MID-1": {"station": "OK3MID-1", "latitude": 49.9, "longitude": 15.0, "observed_at": now}}
        with patch.object(webapp.common, "load_config", return_value={"web_password": "", "callsign": "OK9NODE-1"}), \
             patch.object(webapp.common, "get_station_summary", return_value=summary), \
             patch.object(webapp.common, "count_station_history", return_value=0), \
             patch.object(webapp.common, "get_station_history", return_value=[]), \
             patch.object(webapp.common, "get_own_position", return_value=None), \
             patch.object(webapp.common, "get_node_position", return_value=None), \
             patch.object(webapp.common, "get_station_paths", return_value=paths), \
             patch.object(webapp.common, "get_last_positions", return_value=relay) as last_positions:
            page = webapp.app.test_client().get("/stations").get_data(as_text=True)

        self.assertEqual(last_positions.call_args.args[0], {"OK3MID-1", "OK2RPT-9"})
        relay_line = next(line for line in page.splitlines() if "const relayPositions =" in line)
        self.assertIn('"station": "OK3MID-1"', relay_line)

    def test_station_position_form_saves_and_clears(self):
        with patch.object(webapp.common, "load_config", return_value={"web_password": ""}), \
             patch.object(webapp.common, "set_manual_position") as set_position, \
             patch.object(webapp.common, "clear_manual_position") as clear_position:
            with webapp.app.test_client() as client:
                rejected = client.post("/stations/position", data={"latitude": "50", "longitude": "14"})
                with client.session_transaction() as browser_session:
                    browser_session["_csrf_token"] = "test-token"
                saved = client.post("/stations/position", data={
                    "csrf_token": "test-token", "action": "set", "latitude": "50.1", "longitude": "14.2",
                    "start": "2026-11-01", "end": "2026-11-04"})
                cleared = client.post("/stations/position", data={"csrf_token": "test-token", "action": "clear"})

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(saved.status_code, 302)
        self.assertIn("/stations?start=2026-11-01&end=2026-11-04#station-map-heading", saved.headers["Location"])
        set_position.assert_called_once_with("50.1", "14.2")
        self.assertEqual(cleared.status_code, 302)
        clear_position.assert_called_once_with()

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
        self.assertEqual(response.location, "/messages#send-message")
        send_udp.assert_called_once_with(cfg, "12345", "Maintenance starts", source="web")
        log_message.assert_called_once_with(
            "web_to_mesh", "12345", "Web → MeshCom", "Maintenance starts"
        )

    def test_web_send_uses_group_from_form(self):
        cfg = {"web_password": "", "mesh_group": "12345", "max_mesh_payload": 150}
        with patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.common, "get_rf_channel_summary", return_value=[
                 {"channel": "262", "kind": "group"}, {"channel": "*", "kind": "broadcast"}]), \
             patch.object(webapp.gateway, "send_udp_message") as send_udp, \
             patch.object(webapp.common, "log_message"):
            with webapp.app.test_client() as client:
                with client.session_transaction() as browser_session:
                    browser_session["_csrf_token"] = "test-token"
                sent = client.post("/send", data={"csrf_token": "test-token", "group": "#262", "message": "Ahoj"})
                rejected = client.post("/send", data={"csrf_token": "test-token", "group": "OK1AAA", "message": "Ahoj"})
                page = client.get(sent.location).get_data(as_text=True)

        send_udp.assert_called_once_with(cfg, "262", "Ahoj", source="web")
        self.assertEqual(sent.location, "/messages?group=262#send-message")
        self.assertEqual(rejected.location, "/messages?group=OK1AAA#send-message")
        self.assertIn('name="group" value="262"', page)
        self.assertIn('<option value="262">', page)
        self.assertNotIn('<option value="*">', page)

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

    def test_udp_packet_log_keeps_newest_datagrams_and_filters_direction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = os.path.join(tmpdir, "gateway.db")
            with patch.object(common, "DB_PATH", database_path), \
                 patch.object(common, "UDP_PACKET_LOG_LIMIT", 3):
                common.init_db()
                for index in range(4):
                    common.record_udp_packet("rx", "192.168.1.50:1799", f'{{"n":{index}}}'.encode(), "přijato")
                common.record_udp_packet("tx", "192.168.1.50:1799", b'{"type":"msg"}', "odesláno")

                packets = common.get_udp_packets()
                received = common.get_udp_packets(direction="rx")

        self.assertEqual([p["payload"] for p in packets], ['{"type":"msg"}', '{"n":3}', '{"n":2}'])
        self.assertEqual([p["payload"] for p in received], ['{"n":3}', '{"n":2}'])
        self.assertEqual(packets[0]["size"], 14)

    def test_udp_listener_retries_failed_bind_and_reports_error(self):
        cfg = {"listen_ip": "192.168.99.99", "mesh_udp_port": 1799}
        sockets = []

        class FakeSocket:
            def __init__(self, *args):
                self.fail = not sockets
                sockets.append(self)

            def bind(self, address):
                if self.fail:
                    raise OSError("Cannot assign requested address")

            def settimeout(self, value):
                pass

            def close(self):
                pass

        with patch.object(gateway.common, "load_config", return_value=cfg), \
             patch.object(gateway.socket, "socket", FakeSocket), \
             patch.object(gateway.time, "sleep") as sleep, \
             patch.object(gateway, "update_udp_monitor") as monitor:
            sock = gateway.bind_udp_listener()

        self.assertIs(sock, sockets[1])
        sleep.assert_called_once_with(gateway.UDP_BIND_RETRY_SECONDS)
        self.assertIn("Cannot assign requested address", monitor.call_args_list[0].kwargs["error"])
        self.assertEqual(monitor.call_args_list[1].kwargs["status"], "naslouchá na 192.168.99.99:1799")

    def test_udp_monitor_page_shows_packets_and_listener_state(self):
        cfg = {"web_password": "", "listen_ip": "0.0.0.0", "mesh_udp_port": 1799,
               "mesh_node_ip": "127.0.0.1"}
        packets = [{"ts": 0, "direction": "rx", "peer": "192.168.1.50:1799", "size": 20,
                    "status": "RF pozorování", "payload": '{"type":"pos","src":"OK1ABC-1"}'}]
        with patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.common, "get_udp_packets", return_value=packets) as get_packets, \
             patch.object(webapp.common, "get_heartbeat", return_value=None):
            response = webapp.app.test_client().get("/udp?direction=rx")

        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        get_packets.assert_called_once_with(limit=200, direction="rx")
        self.assertIn("OK1ABC-1", page)
        self.assertIn("Démon neběží", page)
        self.assertIn("--extudpip 127.0.0.1", page)

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

    def test_validate_config_allows_local_smtp_relay_without_auth(self):
        for host in ("localhost", "127.0.0.1", "::1"):
            with self.subTest(host=host):
                result = webapp.validate_config_values(
                    {"smtp_host": host, "smtp_user": ""}, {}
                )
                self.assertTrue(result["ok"])

        remote_result = webapp.validate_config_values(
            {"smtp_host": "smtp.example.com", "smtp_user": ""}, {}
        )
        self.assertIn("smtp_user", remote_result["errors"])

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

    def test_email_parts_are_numbered_and_fit_node_byte_limit(self):
        text = "Příliš žluťoučký kůň úpěl ďábelské ódy. " * 6
        parts = gateway.build_mesh_messages("Jan Novák <jan@example.net>", "Hlášení", text, ["a.pdf"], 150)

        self.assertEqual(len(parts), 4)   # 329 bytes of text
        self.assertTrue(all(gateway.utf8_len(part) <= 150 for part in parts))
        self.assertTrue(parts[0].startswith("Jan Novák: Hlášení (1/4) | Příloha: a.pdf - Příliš"))
        self.assertTrue(parts[3].startswith("Jan Novák: Hlášení (4/4) - "))
        self.assertNotIn("Příloha", parts[1])
        # word boundaries: nothing lost, nothing glued together
        bodies = [part.split(" - ", 1)[1] for part in parts]
        self.assertEqual(" ".join(bodies), text.strip())

        self.assertEqual(gateway.build_mesh_messages("Jan <j@x>", "", "a " * 100, [], 150)[1][:11], "Jan (2/2): ")
        self.assertEqual(gateway.build_mesh_messages("Jan <j@x>", "Krátce", "Ahoj", [], 150), ["Jan: Krátce - Ahoj"])

    def test_email_is_cut_after_max_parts(self):
        text = " ".join(f"slovo{i}" for i in range(200))
        parts = gateway.build_mesh_messages("Jan <j@x>", "Dlouhé", text, [], 150, max_parts=4)

        self.assertEqual(len(parts), 4)
        self.assertTrue(parts[3].startswith("Jan: Dlouhé (4/4) - "))
        self.assertTrue(parts[3].endswith(" [zkráceno]"))
        self.assertTrue(all(gateway.utf8_len(part) <= 150 for part in parts))
        self.assertRegex(parts[3], r"slovo\d+ \[zkráceno\]$")   # cut between words
        self.assertEqual(len(gateway.build_mesh_messages("Jan <j@x>", "", text, [], 150, max_parts=0)), 13)

    def test_send_udp_message_cuts_bytes_on_character_boundary(self):
        cfg = {"max_mesh_payload": 250, "mesh_node_ip": "127.0.0.1", "mesh_udp_port": 9}
        with patch.object(gateway.socket, "socket") as sock, \
             patch.object(gateway, "update_udp_monitor"), \
             patch.object(gateway, "log_udp_packet"):
            gateway.send_udp_message(cfg, "262", "ž" * 100)

        payload = gateway.json.loads(sock.return_value.sendto.call_args[0][0])
        self.assertEqual(payload["msg"], "ž" * 75)   # 150 bytes, the firmware limit

    def test_maildir_email_parts_are_sent_with_pause(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            maildir = os.path.join(tmpdir, "new")
            os.makedirs(maildir)
            cfg = {"maildir_new": maildir, "maildir_cur": os.path.join(tmpdir, "cur"),
                   "poll_interval_seconds": 0, "max_mesh_payload": 150, "mesh_group": "12345",
                   "max_email_parts": 2, "email_part_delay_seconds": 7}
            sends, pauses = [], []

            def fake_sleep(seconds):
                if seconds == 7:
                    pauses.append(seconds)
                    return
                if sends:
                    raise KeyboardInterrupt
                if not os.listdir(maildir):
                    message = EmailMessage()
                    message["From"] = "Jan <jan@example.com>"
                    message["Subject"] = "Zpráva"
                    message.set_content("text " * 100)
                    with open(os.path.join(maildir, "1.eml"), "wb") as handle:
                        handle.write(message.as_bytes())

            with patch.object(gateway.common, "load_config", return_value=cfg), \
                 patch.object(gateway.time, "sleep", side_effect=fake_sleep), \
                 patch.object(gateway, "send_udp_message", side_effect=lambda *a, **k: sends.append(a[2])), \
                 patch.object(gateway.common, "log_message"), \
                 patch.object(gateway.common, "archive_mail_file", return_value="archived"):
                with self.assertRaises(KeyboardInterrupt):
                    gateway.poll_maildir()

        self.assertEqual(len(sends), 2)
        self.assertEqual(pauses, [7])
        self.assertTrue(sends[0].startswith("Jan: Zpráva (1/2) - "))
        self.assertTrue(sends[1].endswith(" [zkráceno]"))

    def test_web_send_counts_bytes(self):
        cfg = {"web_password": "", "mesh_group": "12345", "max_mesh_payload": 150}
        with patch.object(webapp.common, "load_config", return_value=cfg), \
             patch.object(webapp.gateway, "send_udp_message") as send_udp:
            with webapp.app.test_client() as client:
                with client.session_transaction() as browser_session:
                    browser_session["_csrf_token"] = "test-token"
                client.post("/send", data={"csrf_token": "test-token", "message": "ř" * 76})
                page = client.get("/messages").get_data(as_text=True)

        send_udp.assert_not_called()   # 76 characters, but 152 bytes
        self.assertIn("152 bajtů", page)
        self.assertIn('id="mesh-message-left">150<', page)

    def test_validate_config_limits_parts_and_payload_bytes(self):
        errors = webapp.validate_config_values(
            {"max_mesh_payload": "200", "max_email_parts": "0", "email_part_delay_seconds": "-1"}, {})["errors"]
        self.assertEqual(set(errors), {"max_mesh_payload", "max_email_parts", "email_part_delay_seconds"})
        self.assertTrue(webapp.validate_config_values(
            {"max_mesh_payload": "150", "max_email_parts": "4", "email_part_delay_seconds": "5"}, {})["ok"])

    def test_validate_config_checks_email_forward_domains(self):
        self.assertTrue(webapp.validate_config_values({"mesh_email_domains": "example.net, @example.com"}, {})["ok"])
        errors = webapp.validate_config_values({"mesh_email_domains": "example.net, bad domain"}, {})["errors"]
        self.assertEqual(errors, {"mesh_email_domains": "neplatná doména bad domain"})


if __name__ == "__main__":
    unittest.main()
