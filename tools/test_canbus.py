"""Offline checks for canbus. Run with: python -m unittest test_canbus"""

import importlib.util
import types
import unittest
from unittest import mock

import can

import canbus


def capability(fclk=170_000_000, tseg1_max=256, tseg2_max=128, sjw_max=128, brp_max=1024):
    return types.SimpleNamespace(fclk_can=fclk, tseg1_min=1, tseg1_max=tseg1_max, tseg2_min=1,
                                 tseg2_max=tseg2_max, sjw_max=sjw_max, brp_min=1, brp_max=brp_max, brp_inc=1)


class TimingTest(unittest.TestCase):
    def test_canable2_250k(self):
        t = canbus.pick_timing(170_000_000, 250_000, limits=capability())
        self.assertEqual((t.brp, t.tseg1, t.tseg2, t.sjw), (34, 16, 3, 3))
        self.assertEqual(t.bitrate, 250_000)

    def test_device_limits(self):
        t = canbus.pick_timing(48_000_000, 250_000, limits=capability(48_000_000, tseg1_max=8))
        self.assertLessEqual(t.tseg1, 8)
        self.assertEqual(t.bitrate, 250_000)

    def test_impossible(self):
        with self.assertRaises(ValueError):
            canbus.pick_timing(1_000_000, 250_000)


class FakeDevice:
    """Like WinUSB: a second open while the first handle is held is refused."""

    holders = 0

    def __init__(self):
        self._capability = capability()
        self.timing = None
        self.gs_usb = self

    @property
    def device_capability(self):
        if FakeDevice.holders:
            raise PermissionError("Access denied (insufficient permissions)")
        FakeDevice.holders += 1
        return self._capability

    def set_timing(self, prop_seg, phase_seg1, phase_seg2, sjw, brp):
        self.timing = (prop_seg, phase_seg1, phase_seg2, sjw, brp)

    def start(self, *a):
        pass

    def stop(self):
        raise ValueError("device gone")


class GsUsbTest(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec("gs_usb") is None:
            self.skipTest("gs_usb not installed")

    def patches(self, devices):
        def dispose(dev):
            FakeDevice.holders = 0
        FakeDevice.holders = 0
        return (mock.patch("gs_usb.gs_usb.GsUsb.scan", return_value=devices),
                mock.patch.object(canbus, "prime_libusb"),
                mock.patch("usb.util.dispose_resources", side_effect=dispose))

    def test_open_uses_our_timing_and_close_survives(self):
        dev = FakeDevice()
        original = can.BitTiming.__dict__["from_sample_point"]
        p1, p2, p3 = self.patches([dev])
        with p1, p2, p3:
            bus = canbus.open_gs_usb(0, 250_000)
            self.assertEqual(dev.timing, (1, 15, 3, 3, 34))
            self.assertIs(can.BitTiming.__dict__["from_sample_point"], original)
            canbus.close_bus(bus)

    def test_missing_device(self):
        p1, p2, p3 = self.patches([])
        with p1, p2, p3:
            with self.assertRaises(SystemExit):
                canbus.open_gs_usb(0, 250_000)

    def test_failed_open_is_marked_shut_down(self):
        from can.interfaces.gs_usb import GsUsbBus

        made = []
        real_new = GsUsbBus.__new__

        def new(cls, *a, **k):
            made.append(real_new(cls))
            return made[-1]

        dev = FakeDevice()
        dev.set_timing = mock.Mock(side_effect=OSError("boom"))
        p1, p2, p3 = self.patches([dev])
        with p1, p2, p3, mock.patch.object(GsUsbBus, "__new__", new):
            with self.assertRaises(OSError):
                canbus.open_gs_usb(0, 250_000)
        self.assertTrue(made[0]._is_shutdown)

    def test_mode_switch_keeps_timing(self):
        from gs_usb import constants as c

        dev = FakeDevice()
        dev._capability.feature = c.GS_CAN_MODE_LOOP_BACK | c.GS_CAN_MODE_HW_TIMESTAMP
        dev.ctrl_transfer = mock.Mock()
        p1, p2, p3 = self.patches([dev])
        with p1, p2, p3:
            bus = canbus.open_gs_usb(0, 250_000)
            FakeDevice.holders = 0
            dev.timing = None
            dev.stop = mock.Mock()
            self.assertTrue(canbus.set_gs_usb_mode(bus, loopback=True))
            self.assertEqual(dev.timing, (1, 15, 3, 3, 34))
            self.assertEqual(dev.device_flags, c.GS_CAN_MODE_LOOP_BACK | c.GS_CAN_MODE_HW_TIMESTAMP)
            FakeDevice.holders = 0  # reads through the bus's own handle, which is fine on WinUSB
            self.assertFalse(canbus.set_gs_usb_mode(bus, listen_only=True))
            canbus.close_bus(bus)


class ErrorFrameTest(unittest.TestCase):
    def frame(self, classes, data):
        return can.Message(arbitration_id=classes, is_error_frame=True, is_extended_id=False, dlc=8, data=data)

    def test_no_ack(self):
        msg = self.frame(0x24, [0, 0x20, 0, 0, 0, 0, 128, 0])
        self.assertEqual(canbus.describe_error(msg), "controller, no ACK, TX passive (TX errors 128, RX errors 0)")
        self.assertEqual(canbus.describe_error(msg, counters=False), "controller, no ACK, TX passive")
        self.assertIn("no ACK", canbus.error_classes(msg))

    def test_protocol(self):
        msg = self.frame(0x08, [0, 0, 0x04, 0, 0, 0, 0, 9])
        self.assertEqual(canbus.describe_error(msg, counters=False), "protocol violation, stuff error")


if __name__ == "__main__":
    unittest.main()
