"""Offline checks for the helpers. Run with: python -m unittest test_tools"""

import argparse
import unittest

import can_listen
import can_wake
import j1587
import j1708_listen
import j1939


class J1939Test(unittest.TestCase):
    def test_pdu2_roundtrip(self):
        can_id = j1939.make_id(0xF004, 0x00, priority=3)
        self.assertEqual(can_id, 0x0CF00400)
        self.assertEqual(j1939.parse_id(can_id), (3, 0xF004, 0x00, 0xFF))

    def test_pdu1_destination(self):
        can_id = j1939.make_id(0xEA00, 0xF9, da=0x17)
        self.assertEqual(can_id, 0x18EA17F9)
        self.assertEqual(j1939.parse_id(can_id), (6, 0xEA00, 0xF9, 0x17))

    def test_request(self):
        self.assertEqual(j1939.requested_pgn([0xC1, 0xFE, 0x00]), 0xFEC1)
        self.assertIn("asks for 0FEC1 VDHR", j1939.describe(0x18EA0017, bytes([0xC1, 0xFE, 0x00])))

    def test_name(self):
        name = j1939.decode_name(bytes([0x01, 0x00, 0x20, 0x00, 0x00, 0x17, 0x00, 0x80]))
        self.assertEqual(name["identity"], 1)
        self.assertEqual(name["manufacturer"], 1)
        self.assertEqual(name["function"], 0x17)
        self.assertEqual(name["arbitrary_address"], 1)


class J1587Test(unittest.TestCase):
    def test_frame_checksum(self):
        frame = j1587.make_frame(128, [84, 100, 190, 0x40, 0x1F])
        self.assertTrue(j1587.is_valid(frame))
        self.assertEqual(sum(frame) & 0xFF, 0)

    def test_split_back_to_back(self):
        a = j1587.make_frame(128, [84, 100])
        b = j1587.make_frame(140, [0, 84])
        self.assertEqual(j1587.split_frames(a + b), [a, b])
        self.assertIsNone(j1587.split_frames(a + b[:-1]))

    def test_params(self):
        params = j1587.parse_params(bytes([84, 100, 190, 0x40, 0x1F, 194, 2, 0xAA, 0xBB, 96, 50]))
        self.assertEqual(params, [(84, b"\x64"), (190, b"\x40\x1f"), (194, b"\xaa\xbb"), (96, b"\x32")])

    def test_data_link_escape_frames(self):
        # Seen from the cluster on the bench; they used to be rejected as unsplittable.
        short = bytes.fromhex("8c fe 80 04 d4 80 69 80 b5")
        long = bytes.fromhex("8c fe 7f 0f d4 c2 0c c8 b9 04 16 89 03 c9 bc 01 eb bc 01 f1")
        clock = bytes.fromhex("8c fb 03 d4 37 11 5a 8c fc 03 74 0a 29 ce")
        self.assertTrue(j1587.is_valid(short))
        self.assertTrue(j1587.is_valid(long))
        self.assertEqual(j1587.split_frames(clock + short), [bytes.fromhex("8c fb 03 d4 37 11 5a"),
                                                               bytes.fromhex("8c fc 03 74 0a 29 ce"), short])
        self.assertIn("data link escape to MID 128 [d4 80 69 80]", j1587.describe(short))
        self.assertIn("to MID 127", j1587.describe(long))
        self.assertIn("SID 201 Data link, vehicle ECU (MID 144) FMI 12 device lost (active, x1)",
                      j1587.describe(long))

    def test_diag_codes(self):
        # The cluster's own list, joined from its two PID 192 sections.
        codes = j1587.decode_diag_codes(bytes.fromhex("75 a4 03 ab a5 03 54 a9 03 76 a4 03 b1 a9 03 60 a5 03 4d a5 03"))
        self.assertEqual(len(codes), 7)
        self.assertEqual(codes[0], "PID 117 Air pressure primary FMI 4 shorted low (active, x3)")
        self.assertEqual(codes[2], "PID 84 Road speed FMI 9 abnormal update rate (active, x3)")
        self.assertEqual(j1587.decode_diag_codes(bytes([22, 0x89, 3])),
                         ["PID 278 FMI 9 abnormal update rate (active, x3)"])
        self.assertEqual(j1587.decode_diag_codes(bytes([200, 0xF9])),
                         ["SID 200 Data link, engine ECU (MID 128) FMI 9 abnormal update rate (inactive)"])

    def test_describe_request(self):
        text = j1587.describe(j1587.make_frame(140, [128, 84, 144]))
        self.assertIn("asks MID 144 for PID 84 Road speed", text)


class WakeTest(unittest.TestCase):
    def test_heartbeats(self):
        frames = {name: msg for name, _, msg in can_wake.heartbeats(0x27, 1000, 50, 13.8)}
        self.assertEqual(frames["EEC1"].arbitration_id, 0x0CF00400)
        self.assertEqual(bytes(frames["EEC1"].data[3:5]), b"\x40\x1f")
        self.assertEqual(frames["CCVS1"].arbitration_id, 0x18FEF127)
        self.assertEqual(bytes(frames["CCVS1"].data[:3]), b"\xf3\x00\x32")
        self.assertEqual(bytes(frames["VEP1"].data[4:]), b"\x14\x01\x14\x01")
        self.assertEqual(frames["VEP1"].arbitration_id, 0x18FEF727)

    def test_parse_vecu(self):
        self.assertEqual(can_wake.parse_vecu("27,21"), [[0x27], [0x21]])
        self.assertEqual(can_wake.parse_vecu("15-18"), [[0x15, 0x16, 0x18]])   # skips the cluster, 17

    def test_parse_extra_range(self):
        one = can_wake.parse_extra("FE56:3D:7D 41")
        self.assertEqual([m.arbitration_id for m in one], [0x18FE563D])
        self.assertEqual(bytes(one[0].data), bytes.fromhex("7D41FFFFFFFFFFFF"))
        many = can_wake.parse_extra("FE56:00-FD:7D")
        sas = [m.arbitration_id & 0xFF for m in many]
        self.assertEqual(len(sas), 253)                # 00-FD without the cluster, 17
        self.assertIn(0x00, sas)
        self.assertNotIn(0x17, sas)
        turns = can_wake.parse_vecu("sweep")
        flat = [sa for t in turns for sa in t]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertNotIn(0x00, flat)
        self.assertNotIn(0x17, flat)
        self.assertEqual((min(flat), max(flat)), (0x01, 0xFD))
        self.assertTrue(all(len(t) <= 16 for t in turns))

    def test_group_heartbeats(self):
        frames = can_wake.heartbeats([0x21, 0x22], 1000, 50, 13.8)
        ids = {msg.arbitration_id for _, _, msg in frames}
        self.assertTrue({0x18FEF721, 0x18FEF722, 0x18FEF121, 0x18FEF122, 0x0CF00400} <= ids)
        self.assertEqual(len({name for name, _, _ in frames}), len(frames))

    def test_scheduler_rates(self):
        sched = can_wake.Scheduler(can_wake.heartbeats(0x27, 1000, 50, 13.8), 0.0)
        counts = {}
        t = 0.0
        while t < 10.0:
            for msg in sched.due(t):
                counts[msg.arbitration_id] = counts.get(msg.arbitration_id, 0) + 1
            t += 0.001
        self.assertAlmostEqual(counts[0x0CF00400], 500, delta=2)   # EEC1, 20 ms
        self.assertAlmostEqual(counts[0x18FEF727], 100, delta=2)   # VEP1, 100 ms
        self.assertAlmostEqual(counts[0x18FEEE00], 10, delta=1)    # ET1, 1 s

    def test_scheduler_swap_keeps_engine_going(self):
        sched = can_wake.Scheduler(can_wake.heartbeats(0x27, 1000, 50, 13.8), 0.0)
        sched.due(0.0)
        sched.swap(can_wake.heartbeats(0x21, 1000, 50, 13.8), 0.005)
        self.assertEqual(sched.due(0.005), [])      # nothing re-sent early
        ids = [m.arbitration_id for m in sched.due(0.02)]
        self.assertIn(0x0CF00400, ids)               # EEC1 still on its 20 ms beat
        ids = [m.arbitration_id for m in sched.due(0.1)]
        self.assertIn(0x18FEF721, ids)               # VEP1 now from 0x21

    def test_scheduler_skips_ahead_after_stall(self):
        sched = can_wake.Scheduler(can_wake.heartbeats(0x27, 1000, 50, 13.8), 0.0)
        sched.due(0.0)
        burst = [m for m in sched.due(1.0) if m.arbitration_id == 0x0CF00400]
        self.assertEqual(len(burst), 1)


class SendArgTest(unittest.TestCase):
    def test_parse_send_adds_checksum(self):
        frame = j1708_listen.parse_send("80 BE 40 1F")
        self.assertEqual(frame, j1587.make_frame(0x80, [0xBE, 0x40, 0x1F]))
        self.assertTrue(j1587.is_valid(frame))

    def test_parse_send_rejects_bad_input(self):
        for text in ("", "80", "zz 01", "80 " + "01 " * 25):
            with self.assertRaises(argparse.ArgumentTypeError, msg=text):
                j1708_listen.parse_send(text)


class LiveViewTest(unittest.TestCase):
    def test_changed_bytes_highlighted_then_fade(self):
        entry = can_listen.new_entry()
        can_listen.update(entry, bytes([1, 2, 3]), 10.0)
        can_listen.update(entry, bytes([1, 9, 3]), 10.1)
        seen = {(0x00, 0xF004): entry}
        row = can_listen.render(seen, 10.2, 0.0, 0, "", set())[3]
        self.assertIn("01 \x1b[7m09\x1b[27m 03", row)
        self.assertIn("100.0ms", row)
        later = can_listen.render(seen, 11.5, 0.0, 0, "", set())[3]
        self.assertNotIn("\x1b[7m", later)

    def test_stale_rows_dimmed_and_requests_listed(self):
        entry = can_listen.new_entry()
        can_listen.update(entry, bytes([0xC1, 0xFE, 0x00]), 1.0)
        lines = can_listen.render({(0x17, 0xEA00): entry}, 5.0, 0.0, 3, "no ACK", {(0x17, 0xFEC1)})
        self.assertIn("error frames 3  (last: no ACK)", lines[0])
        self.assertTrue(lines[3].startswith("\x1b[2m"))
        self.assertIn("SA 17 -> 0FEC1 VDHR", lines[-1])

    def test_parse_send(self):
        msg = can_listen.parse_send("F004:00:FF FF FF 40 1F")
        self.assertEqual(msg.arbitration_id, 0x18F00400)
        self.assertEqual(bytes(msg.data), bytes([0xFF, 0xFF, 0xFF, 0x40, 0x1F, 0xFF, 0xFF, 0xFF]))
        self.assertTrue(msg.is_extended_id)
        with self.assertRaises(argparse.ArgumentTypeError):
            can_listen.parse_send("F004 00 FF")


if __name__ == "__main__":
    unittest.main()
