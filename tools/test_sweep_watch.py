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

import can_wake
import j1587
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


    def test_j1708_candidates(self):
        got = sw.j1708_candidates([0x80], [0x54, 0x80, 0xC2, 0xFE, 0xFF], [0x00])
        self.assertEqual(got, [(0x80, 0x54, b"\x00"), (0x80, 0x80, b"\x00\x00"), (0x80, 0xC2, b"\x02\x00\x00")])
        self.assertEqual(len(sw.j1708_candidates([0x80, 0x90], range(0, 254), [0x00, 0xFF])), 2 * 254 * 2)


class RotateTest(unittest.TestCase):
    def test_rotate(self):
        img = np.zeros((2, 3), np.uint8)
        img[0, 0] = 1
        self.assertEqual(sw.rotate(img, 180)[1, 2], 1)
        self.assertEqual(sw.rotate(img, 90).shape, (3, 2))
        self.assertIs(sw.rotate(img, 0), img)


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


class FakePort:
    """A serial port that remembers when each frame was last written."""

    def __init__(self):
        self.last_write = {}

    def write(self, data):
        self.last_write[bytes(data)] = time.time()


class LampCamera(FakeCamera):
    """Like FakeCamera, but the lamp lights while the target frame is being written to a FakePort."""

    def __init__(self, port, target_frame):
        self.port = port
        self.target_frame = target_frame
        self.n = 0

    def frame(self, timeout=2.0):
        time.sleep(0.02)
        self.n += 1
        img = np.full((120, 160, 3), 20, np.uint8)
        img[5:15, 5:30] = (self.n * 37) % 255  # clock, masked out
        if time.time() - self.port.last_write.get(self.target_frame, 0.0) < 0.25:
            img[60:80, 100:120] = 230
        return img

    def close(self):
        pass


class J1708SweepTest(unittest.TestCase):
    def test_finds_only_the_target_frame(self):
        port = FakePort()
        target = j1587.make_frame(0x80, bytes([0x54, 0xFF]))
        camera = LampCamera(port, target)
        detector = sw.ChangeDetector(ignore=[[0, 0, 40, 20]])
        cands = sw.j1708_candidates([0x80, 0x90], [0x53, 0x54, 0x55], [0x00, 0xFF])
        out = tempfile.mkdtemp()
        hits = sw.run_sweep(None, camera, detector, cands, out, dwell=0.3, settle=0.3, recover=1.0,
                            log=lambda *_: None, transport=sw.J1708Transport(port))
        with open(os.path.join(out, "hits.csv"), newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(hits, 1)
        self.assertEqual((rows[0]["sa"], rows[0]["pgn"], rows[0]["payload"], rows[0]["name"]),
                         ("80", "00054", "ff", "Road speed"))
        with open(os.path.join(out, "review.html")) as f:
            self.assertIn("MID 80<br>PID 00054 Road speed", f.read())
        self.assertIn(j1587.make_frame(0x90, bytes([0x55, 0x00])), port.last_write)

    def test_periodic_writer_stops(self):
        port = FakePort()
        w = sw.PeriodicWriter(port, b"\x01\x02", 0.05)
        time.sleep(0.12)
        w.stop()
        seen = port.last_write[b"\x01\x02"]
        time.sleep(0.15)
        self.assertEqual(port.last_write[b"\x01\x02"], seen)


if __name__ == "__main__":
    unittest.main()


class FakeCluster:
    """Sends a clock frame that always changes, a status frame, and changes the status while TARGET arrives."""

    def __init__(self, channel, target_id):
        self.bus = can.Bus(interface="virtual", channel=channel)
        self.target_id = target_id
        self.last_seen = 0.0
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        n = 0
        next_send = 0.0
        while self.running:
            msg = self.bus.recv(timeout=0.01)
            if msg and msg.arbitration_id == self.target_id:
                self.last_seen = time.time()
            if time.time() >= next_send:
                n += 1
                speed = 0x40 if time.time() - self.last_seen < 0.3 else 0x00
                self.bus.send(can.Message(arbitration_id=j1939.make_id(0xFEE6, 0x17), data=[n & 0xFF] * 8,
                                          is_extended_id=True))
                self.bus.send(can.Message(arbitration_id=j1939.make_id(0xFEC1, 0x17), data=[speed] * 8,
                                          is_extended_id=True))
                next_send = time.time() + 0.1

    def close(self):
        self.running = False
        time.sleep(0.05)
        self.bus.shutdown()


class BusWatchTest(unittest.TestCase):
    def test_learns_noise_and_reports_changes(self):  # noqa
        w = sw.BusWatch()
        w.observe(("CAN", 0xFEE6), b"\x01")
        w.observe(("CAN", 0xFEC1), b"\x00")
        t = threading.Thread(target=lambda: [w.observe(("CAN", 0xFEE6), bytes([i])) or time.sleep(0.05)
                                             for i in range(2, 12)])
        t.start()
        w.learn(0.6, log=lambda *_: None)
        t.join()
        self.assertIn(("CAN", 0xFEE6), w.noisy)
        self.assertNotIn(("CAN", 0xFEC1), w.noisy)
        w.mark()
        w.observe(("CAN", 0xFEE6), b"\x99")
        self.assertEqual(w.changes(), [])
        w.observe(("CAN", 0xFEC1), b"\x40")
        w.observe(("CAN", 0xFEC1), b"\x00")      # back again before the window ends: still a change
        w.observe(("J1708", 140, 245, None), b"\xf5\x04")
        self.assertEqual(len(w.changes()), 2)

    def test_repeat_offender_becomes_noise(self):
        w = sw.BusWatch(noisy_after=2)
        key = ("J1708", 140, 251, None)
        w.blame([key], log=lambda *_: None)
        self.assertNotIn(key, w.noisy)
        w.blame([key], log=lambda *_: None)
        self.assertIn(key, w.noisy)

    def test_j1708_keys(self):
        w = sw.BusWatch()
        w.observe_j1708(j1587.make_frame(140, [254, 128, 4, 0xd4, 0x80]))
        w.observe_j1708(j1587.make_frame(140, [254, 127, 4, 0xd4, 0xc2]))
        w.observe_j1708(j1587.make_frame(140, [245, 4, 1, 2, 3, 4]))
        self.assertEqual(len(w.latest), 3)

    def test_sweep_without_camera_finds_target(self):
        channel = "bus_watch_sweep"
        target_id = j1939.make_id(0xFF02, 0x27)
        cluster = FakeCluster(channel, target_id)
        bus = can.Bus(interface="virtual", channel=channel)
        watch = sw.BusWatch()
        transport = sw.CanTransport(bus, sender=can_wake.CanSender(bus, on_rx=watch.observe_can))
        out = tempfile.mkdtemp()
        try:
            watch.learn(0.6, log=lambda *_: None)
            cands = sw.candidates([0xFF00, 0xFF01, 0xFF02, 0xFF03], [0x27], sw.parse_payloads("40"))
            hits = sw.run_sweep(bus, sw.NullCamera(), sw.ChangeDetector(), cands, out, dwell=0.4, settle=0.3,
                                recover=0.5, log=lambda *_: None, transport=transport, watch=watch)
        finally:
            transport.sender.close()
            bus.shutdown()
            cluster.close()
        rows = sw.read_rows(os.path.join(out, "hits.csv"))
        self.assertEqual(hits, 1)
        self.assertEqual((rows[0]["sa"], rows[0]["pgn"]), ("27", "0FF02"))
        self.assertIn("FEC1", rows[0]["bus"])
        self.assertNotIn("FEE6", rows[0]["bus"])


class CheckTest(unittest.TestCase):
    def run_check(self, rpm_seen):
        channel = f"check_{rpm_seen}"
        eec1 = next(m for n, _, m in can_wake.heartbeats(0x27, rpm_seen, 0, 13.8) if n == "EEC1")
        camera = FakeCamera(channel, eec1.arbitration_id, eec1.data)
        cluster = FakeCluster(channel, 0)
        bus = can.Bus(interface="virtual", channel=channel)
        sender = can_wake.CanSender(bus, can_wake.heartbeats(0x27, 1000, 0, 13.8))
        try:
            time.sleep(0.3)
            return sw.run_check(sender, camera, sw.ChangeDetector(ignore=[[0, 0, 40, 20]]), None, True, 1.0, 2000,
                                1000, recover=0.1, log=lambda *_: None)
        finally:
            sender.close()
            bus.shutdown()
            camera.close()
            cluster.close()

    def test_passes_when_the_tach_moves(self):
        self.assertTrue(self.run_check(2000))

    def test_fails_when_nothing_moves(self):
        self.assertFalse(self.run_check(3000))
