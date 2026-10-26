"""Offline checks for la_capture. Run with: python -m unittest test_la_capture

The decode test builds a capture in memory and runs it through sigrok-cli,
so it is skipped where sigrok-cli isn't installed."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import j1587
import la_capture

RATE = 1_000_000
IDLE = 0b1100  # D2 and D3 (J1708 TX and RX) idle high


def uart_samples(frame, line_mask, extra=0):
    """One frame at 9600 8N1 on the lines in line_mask, other bits held."""
    out = bytearray()
    k = 0
    for b in frame:
        for v in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
            k += 1
            n = round(k * RATE / 9600) - len(out)
            out += bytes([(IDLE & ~line_mask) | (line_mask if v else 0) | extra]) * n
    return out


def synth_capture():
    """Ten test frames on TX under marker pulses, then a cluster request and
    a bad chunk on RX, as raw 8-channel samples."""
    s = bytearray([IDLE]) * 20000
    for f in la_capture.TEST_FRAMES:
        s += bytes([IDLE | 0x10]) * 300 + uart_samples(f, 0b0100, 0x10) + bytes([IDLE | 0x10]) * 200
        s += bytes([IDLE]) * 20000
    for f in (j1587.make_frame(140, [0, 84]), bytes([1, 2, 3])):
        s += uart_samples(f, 0b1000) + bytes([IDLE]) * 20000
    return bytes(s)


class ParseTest(unittest.TestCase):
    def test_uart_lines(self):
        text = "5104-5938 uart-1: 80\n6144-6978 uart-1: 54\nnoise\n"
        self.assertEqual(la_capture.parse_uart(text, 1e6), [(0.005104, 0.005938, 0x80), (0.006144, 0.006978, 0x54)])

    def test_frames_split_at_gap(self):
        a, b = j1587.make_frame(128, [84, 1]), j1587.make_frame(140, [0, 84])
        byte = 10 / 9600
        decoded = [(i * byte, (i + 1) * byte, x) for i, x in enumerate(a)]
        t = len(a) * byte + 0.005  # well over the 10-bit gap
        decoded += [(t + i * byte, t + (i + 1) * byte, x) for i, x in enumerate(b)]
        decoded += [(1.0, 1.001, 0x01), (1.001, 1.002, 0x02)]
        frames = la_capture.group_frames(decoded)
        self.assertEqual([f[1] for f in frames], [a, b, None])
        self.assertEqual(frames[2][2], bytes([1, 2]))

    def test_vcd_pulses(self):
        text = ("$timescale 1 us $end\n$var wire 1 ! D0 $end\n$var wire 1 % D4 $end\n$enddefinitions $end\n"
                "#0 0! 0%\n#100 1%\n#150 1!\n#300 0%\n#400 1%\n")
        pulses = [(round(a * 1e6), round(b * 1e6)) for a, b in la_capture.parse_vcd_pulses(text, "D4")]
        self.assertEqual(pulses, [(100, 300), (400, 400)])
        self.assertEqual(la_capture.parse_vcd_pulses(text, "D7"), [])

    def test_marker_window(self):
        pulses = [(1.0, 1.01)]
        self.assertTrue(la_capture.under_marker(1.0005, pulses))
        self.assertFalse(la_capture.under_marker(1.02, pulses))


@unittest.skipUnless(shutil.which("sigrok-cli"), "sigrok-cli not installed")
class DecodeTest(unittest.TestCase):
    def test_synthetic_capture(self):
        with tempfile.TemporaryDirectory() as d:
            raw, sr = os.path.join(d, "s.bin"), os.path.join(d, "s.sr")
            with open(raw, "wb") as f:
                f.write(synth_capture())
            subprocess.run(["sigrok-cli", "-I", f"binary:numchannels=8:samplerate={RATE}", "-i", raw, "-o", sr],
                           check=True)
            frames, marker = la_capture.decode("sigrok-cli", sr)
        tx = [(f, la_capture.under_marker(t, marker)) for t, line, f, _ in frames if line == "tx"]
        rx = [f for _, line, f, _ in frames if line == "rx"]
        self.assertEqual(tx, [(f, True) for f in la_capture.TEST_FRAMES])
        self.assertEqual(len(marker), 10)
        self.assertEqual(rx, [j1587.make_frame(140, [0, 84]), None])

    def test_selftest_through_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            raw, made, sr = (os.path.join(d, n) for n in ("s.bin", "made.sr", "s.sr"))
            with open(raw, "wb") as f:
                f.write(synth_capture())
            subprocess.run(["sigrok-cli", "-I", f"binary:numchannels=8:samplerate={RATE}", "-i", raw, "-o", made],
                           check=True)

            class FakeCapture:
                """Stands in for sigrok-cli capturing from the analyzer."""

                def __init__(self, cmd, **kwargs):
                    shutil.copy(made, cmd[cmd.index("-o") + 1])
                    self.returncode = 0

                def communicate(self, timeout=None):
                    return "", ""

                def poll(self):
                    return self.returncode

            real_run, real_popen = la_capture.run, subprocess.Popen

            def popen(cmd, **kwargs):
                return FakeCapture(cmd) if "-d" in cmd else real_popen(cmd, **kwargs)

            def run(sigrok, *args, **kwargs):
                if args[0] == "--scan":
                    return "The following devices were found:\nfx2lafw - Saleae Logic with 8 channels: D0 D1 D2"
                return real_run(sigrok, *args, **kwargs)

            args = SimpleNamespace(sigrok=None, driver="fx2lafw", samplerate="1m", seconds=3.0, bridge="COM7", out=sr)
            port = mock.MagicMock()
            with mock.patch.object(la_capture, "find_sigrok", return_value="sigrok-cli"), \
                    mock.patch.object(la_capture, "run", side_effect=run), \
                    mock.patch.object(la_capture.subprocess, "Popen", popen), \
                    mock.patch("serial.Serial", return_value=port), mock.patch.object(la_capture.time, "sleep"), \
                    redirect_stdout(io.StringIO()) as out:
                code = la_capture.cmd_selftest(args)
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual([c.args[0] for c in port.write.call_args_list], la_capture.TEST_FRAMES)
        self.assertIn("all 10 frames decoded intact", out.getvalue())
        self.assertIn("every frame inside one", out.getvalue())


if __name__ == "__main__":
    unittest.main()
