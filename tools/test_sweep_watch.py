"""Offline checks for sweep_watch. Run with: python -m unittest test_sweep_watch

The end-to-end test uses a python-can virtual bus and a fake camera whose
picture lights a "telltale" while one particular frame is being received.
"""

import csv
import os
import tempfile
import threading
import time
import unittest

import can
import numpy as np

import j1939
import sweep_watch as sw


class FakeCamera:
    """Shows a lit square while TARGET frames keep arriving, plus a 'clock' that always changes."""

    def __init__(self, channel, target_id, target_data, latch=False):
        self.bus = can.Bus(interface="virtual", channel=channel)
        self.target = (target_id, bytes(target_data))
        self.latch = latch
        self.last_seen = 0.0
        self.lit_forever = False
        self.running = True
        self.n = 0
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while self.running:
            msg = self.bus.recv(timeout=0.05)
            if msg and (msg.arbitration_id, bytes(msg.data)) == self.target:
                self.last_seen = time.time()
                if self.latch:
                    self.lit_forever = True

    def frame(self, timeout=2.0):
        time.sleep(0.02)
        self.n += 1
        img = np.full((120, 160, 3), 20, np.uint8)
        img[5:15, 5:30] = (self.n * 37) % 255  # clock, masked out
        if self.lit_forever or time.time() - self.last_seen < 0.25:
            img[60:80, 100:120] = 230
        return img

    def close(self):
        self.running = False
        self.bus.shutdown()


class ParseTest(unittest.TestCase):
    def test_hex_list(self):
        self.assertEqual(sw.parse_hex_list("FF00-FF02, FEF1"), [0xFF00, 0xFF01, 0xFF02, 0xFEF1])

    def test_payloads(self):
        self.assertEqual(sw.parse_payloads("00,FF"), [b"\x00" * 8, b"\xff" * 8])
        self.assertEqual(sw.parse_payloads("0102030405060708"), [bytes(range(1, 9))])

    def test_bits(self):
        p = sw.bit_payloads(b"\x00" * 8)
        self.assertEqual(len(p), 64)
        self.assertEqual(p[0], b"\x01" + b"\x00" * 7)
        self.assertEqual(p[63], b"\x00" * 7 + b"\x80")
        self.assertEqual(len(sw.candidates([0xFF00], [0x21], [b"\x00" * 8], bits=True)), 64)


class DetectorTest(unittest.TestCase):
    def test_ignores_masked_area(self):
        base = np.full((100, 100, 3), 20, np.uint8)
        d = sw.ChangeDetector(ignore=[[0, 0, 30, 30]])
        d.set_baseline([base])
        clock = base.copy()
        clock[0:30, 0:30] = 255
        self.assertFalse(d.changed(clock))
        lamp = base.copy()
        lamp[50:60, 50:60] = 255
        self.assertTrue(d.changed(lamp))
        self.assertEqual(len(sw.boxes(d.diff(lamp)[1])), 1)


class SweepTest(unittest.TestCase):
    def run_case(self, latch):
        channel = f"sweep_test_{latch}"
        target_sa, target_pgn, target_payload = 0x21, 0xFF02, b"\xff" * 8
        target_id = j1939.make_id(target_pgn, target_sa)
        camera = FakeCamera(channel, target_id, target_payload, latch=latch)
        bus = can.Bus(interface="virtual", channel=channel)
        detector = sw.ChangeDetector(ignore=[[0, 0, 40, 20]])
        cands = sw.candidates([0xFF00, 0xFF01, 0xFF02, 0xFF03], [0x00, 0x21], sw.parse_payloads("00,FF"))
        out = tempfile.mkdtemp()
        try:
            hits = sw.run_sweep(bus, camera, detector, cands, out, dwell=0.3, settle=0.3, recover=1.0,
                                log=lambda *_: None)
        finally:
            bus.shutdown()
            camera.close()
        with open(os.path.join(out, "hits.csv"), newline="") as f:
            rows = list(csv.DictReader(f))
        return hits, rows, out, len(cands)

    def test_finds_only_the_target(self):
        hits, rows, out, n = self.run_case(latch=False)
        self.assertEqual(hits, 1)
        self.assertEqual((rows[0]["sa"], rows[0]["pgn"], rows[0]["payload"]), ("21", "0FF02", "ff ff ff ff ff ff ff ff"))
        self.assertEqual(rows[0]["latched"], "")
        for name in (rows[0]["before"], rows[0]["after"], "review.html"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        with open(os.path.join(out, "tried.csv")) as f:
            self.assertEqual(len(f.readlines()), n + 1)

    def test_latched_hit_takes_new_baseline(self):
        hits, rows, _, _ = self.run_case(latch=True)
        self.assertEqual(hits, 1)
        self.assertEqual(rows[0]["latched"], "yes")


if __name__ == "__main__":
    unittest.main()
