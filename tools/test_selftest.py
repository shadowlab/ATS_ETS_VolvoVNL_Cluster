"""Offline checks for can_selftest. Run with: python -m unittest test_selftest"""

import io
import unittest
from collections import deque
from contextlib import redirect_stdout
from unittest import mock

import can

import can_selftest


def error_frame(classes, ctrl=0, tx=0):
    return can.Message(arbitration_id=classes, is_extended_id=False, is_error_frame=True, dlc=8,
                       data=[0, ctrl, 0, 0, 0, 0, tx, 0])


class FakeBus:
    """A CANable that echoes what it sends, loops back in loopback mode, and
    plays canned replies to the frame sent in normal mode."""

    def __init__(self, loopback_ok=True, replies=(), traffic=(), ack=False):
        self.mode = "normal"
        self.ack = ack
        self.unacked = 0
        self.loopback_ok = loopback_ok
        self.replies = list(replies)
        self.queue = deque()
        self.traffic = list(traffic)

    def send(self, msg):
        # Without an ACK the controller holds 3 frames and the rest never echo.
        if self.mode == "normal" and not self.ack:
            self.unacked += 1
            if self.unacked > 3:
                return
        self.queue.append(can.Message(arbitration_id=msg.arbitration_id, data=msg.data, is_rx=False))
        if self.mode == "loopback" and self.loopback_ok:
            self.queue.append(can.Message(arbitration_id=msg.arbitration_id, data=msg.data, is_extended_id=True))
        if self.mode == "normal":
            self.queue.extend(self.replies)

    def reset(self):
        self.unacked = 0

    def recv(self, timeout=None):
        if self.mode == "normal" and self.traffic:  # slcan: never switched
            self.queue.extend(self.traffic)
            self.traffic = []
        return self.queue.popleft() if self.queue else None


def run(bus, interface="gs_usb"):
    def set_mode(b, loopback=False, **k):
        b.mode = "loopback" if loopback else "normal"
        b.reset()
        if not loopback:
            b.queue.extend(b.traffic)
            b.traffic = []
        return True

    args = ["can_selftest.py", "--interface", interface, "--channel", "0"]
    out = io.StringIO()
    with mock.patch("sys.argv", args), mock.patch.object(can_selftest.canbus, "open_bus", return_value=bus), \
            mock.patch.object(can_selftest.canbus, "close_bus"), \
            mock.patch.object(can_selftest.canbus, "set_gs_usb_mode", side_effect=set_mode), \
            mock.patch.object(can_selftest, "LISTEN_SECONDS", 0.1), redirect_stdout(out):
        code = can_selftest.main()
    return code, out.getvalue()


class SelfTest(unittest.TestCase):
    def test_nothing_connected(self):
        code, out = run(FakeBus(replies=[error_frame(0x20), error_frame(0x04, ctrl=0x20, tx=128)]))
        self.assertEqual(code, 0, out)
        self.assertIn("50 frames out and back intact", out)
        self.assertIn("nobody ACKed", out)

    def test_canable2_firmware(self):
        # candleLight for the CANable 2.0: echoes but no loopback, no error frames.
        code, out = run(FakeBus(loopback_ok=False))
        self.assertEqual(code, 0, out)
        self.assertIn("[WARN] Loopback: the firmware took the frames", out)
        self.assertIn("doesn't report bus errors", out)

    def test_cluster_silent_to_request(self):
        traffic = [can.Message(arbitration_id=0x18FEF117, data=[0] * 8) for _ in range(3)]
        code, out = run(FakeBus(traffic=traffic))
        self.assertEqual(code, 0, out)
        self.assertIn("sending isn't proven yet", out)

    def test_cluster_on_bus(self):
        traffic = [can.Message(arbitration_id=0x18FEF117, data=[0] * 8) for _ in range(5)]
        claim = can.Message(arbitration_id=0x18EEFF17, data=[1, 0, 0x20, 0, 0, 0x17, 0, 0x80])
        code, out = run(FakeBus(traffic=traffic, replies=[claim]))
        self.assertEqual(code, 0, out)
        self.assertIn("heard 5 frames", out)
        self.assertIn("work both ways", out)
        self.assertIn("claim: SA 17", out)

    def test_cluster_with_errors(self):
        traffic = [can.Message(arbitration_id=0x18FEF117, data=[0] * 8), error_frame(0x08)]
        code, out = run(FakeBus(traffic=traffic))
        self.assertEqual(code, 1)
        self.assertIn("check termination", out)

    def test_slcan_skips_loopback(self):
        code, out = run(FakeBus(), interface="slcan")
        self.assertEqual(code, 0, out)
        self.assertIn("not available on slcan", out)
        self.assertIn("doesn't report errors", out)

    def test_open_failure(self):
        with mock.patch("sys.argv", ["x", "--channel", "0"]), \
                mock.patch.object(can_selftest.canbus, "open_bus", side_effect=OSError("no device")), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(can_selftest.main(), 1)
        self.assertIn("FAIL", out.getvalue())


class AckTest(unittest.TestCase):
    def test_nobody_acks(self):
        code, out = run(FakeBus(loopback_ok=False))
        self.assertEqual(code, 0, out)
        self.assertIn("[WARN] ACK: only 3 of 12", out)

    def test_cluster_acks(self):
        code, out = run(FakeBus(loopback_ok=False, ack=True))
        self.assertEqual(code, 0, out)
        self.assertIn("[PASS] ACK: all 12 test frames went out", out)


if __name__ == "__main__":
    unittest.main()
