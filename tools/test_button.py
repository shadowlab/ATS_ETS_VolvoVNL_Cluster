"""Offline checks for the bench button. Run with: python -m unittest test_button"""

import os
import tempfile
import unittest

import button_test as bt
import j1587
import sweep_watch as sw


class FakePort:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        pass


class ButtonFrameTest(unittest.TestCase):
    def test_frame_round_trip(self):
        for seq in (1, 2, 255, 0):
            f = j1587.button_frame(seq)
            self.assertTrue(j1587.is_valid(f))
            self.assertEqual(j1587.parse_button(f), seq)
        self.assertIsNone(j1587.parse_button(j1587.make_frame(140, [245, 1, 2])))
        self.assertIn("BUTTON", j1587.describe(j1587.button_frame(7)))

    def test_firmware_checksum_matches(self):
        # main.cpp: frame[3] = -(MID + PID + seq)
        for seq in range(256):
            f = bytes([254, 127, seq, (-(254 + 127 + seq)) & 0xFF])
            self.assertEqual(f, j1587.button_frame(seq))


class CheckTest(unittest.TestCase):
    def test_pass(self):
        ok, notes = bt.check([(1.0, 1), (2.0, 2), (3.0, 3)], 3)
        self.assertTrue(ok, notes)

    def test_wraparound_passes(self):
        ok, _ = bt.check([(1.0, 255), (2.0, 0)], 2)
        self.assertTrue(ok)

    def test_bounce_and_jump_fail(self):
        self.assertFalse(bt.check([(1.0, 1), (1.05, 2)], 2)[0])
        self.assertFalse(bt.check([(1.0, 1), (2.0, 3)], 2)[0])
        self.assertFalse(bt.check([(1.0, 1)], 2)[0])
        self.assertFalse(bt.check([(1.0, 1), (2.0, 2), (3.0, 3)], 2)[0])

    def test_read_presses_ignores_other_frames(self):
        other = j1587.make_frame(140, [245, 4, 1, 2, 3, 4])
        port = FakePort([j1587.button_frame(1), b"", b"", b"", b"", other, b"", b"", b"", b"", j1587.button_frame(2)])
        presses = bt.read_presses(port, 2, 5, gap=0.0, quiet=0.0, log=lambda *_: None)
        self.assertEqual([s for _, s in presses], [1, 2])


class ButtonLogTest(unittest.TestCase):
    def test_press_records_current_and_previous_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            lines = []
            log = sw.ButtonLog(d, log=lines.append)
            log.sending("MID 80 PID 000C2 data 02 00 00")
            log.sending("MID 80 PID 000C2 data 02 ff ff")
            log.press(5)
            with open(os.path.join(d, "buttons.csv")) as f:
                text = f.read()
        self.assertIn("02 ff ff", text)
        self.assertIn("02 00 00", text)
        self.assertIn("BUTTON #5", lines[0])

    def test_press_before_any_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            lines = []
            sw.ButtonLog(d, log=lines.append).press(1)
        self.assertIn("nothing yet", lines[0])

    def test_reader_routes_button_frames(self):
        seen, watched = [], []

        class Watch:
            def observe_j1708(self, frame):
                watched.append(frame)

        other = j1587.make_frame(140, [245, 4, 1, 2, 3, 4])
        port = FakePort([j1587.button_frame(9), b"", b"", b"", b"", b"", other])
        reader = sw.J1708Reader(port, Watch(), gap=0.0, on_button=seen.append)
        import time
        time.sleep(0.3)
        reader.close()
        self.assertEqual(seen, [9])
        self.assertEqual(watched, [other])


if __name__ == "__main__":
    unittest.main()
