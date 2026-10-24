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
    def __init__(self):
        self.device_capability = capability()
        self.timing = None

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

    def test_open_uses_our_timing_and_close_survives(self):
        dev = FakeDevice()
        original = can.BitTiming.__dict__["from_sample_point"]
        with mock.patch("gs_usb.gs_usb.GsUsb.scan", return_value=[dev]), mock.patch.object(canbus, "prime_libusb"):
            bus = canbus.open_gs_usb(0, 250_000)
            self.assertEqual(dev.timing, (1, 15, 3, 3, 34))
            self.assertIs(can.BitTiming.__dict__["from_sample_point"], original)
            canbus.close_bus(bus)

    def test_missing_device(self):
        with mock.patch("gs_usb.gs_usb.GsUsb.scan", return_value=[]), mock.patch.object(canbus, "prime_libusb"):
            with self.assertRaises(SystemExit):
                canbus.open_gs_usb(0, 250_000)


if __name__ == "__main__":
    unittest.main()
