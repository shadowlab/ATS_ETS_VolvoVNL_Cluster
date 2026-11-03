"""Offline checks for sweep_crosscheck. Run with: python -m unittest test_sweep_crosscheck"""

import csv
import os
import tempfile
import unittest

import j1587
import sweep_crosscheck as sc


def j1708_row(t, pid, val):
    body = bytes([140, pid]) + val
    frame = j1587.make_frame(140, bytes([pid]) + val)
    return [f"{t:.4f}", 140, frame.hex(" "), ""]


class CrossCheckTest(unittest.TestCase):
    def test_flags_only_the_changed_window(self):
        windows = [(100.0 + 2 * i, 101.5 + 2 * i, "00", f"{0xFF00 + i:05X}", "40") for i in range(20)]
        events = []
        for i in range(0, 42):
            t = 100.0 + i
            events.append((t, ("CAN", "FEFC"), "00" * 8))                  # steady
            events.append((t, ("CAN", "FEE6"), f"{i:02x}" * 8))            # counter: noise
        events.append((111.2, ("CAN", "FF28"), "01"))                      # new message in window 5
        events.append((111.2, ("CAN", "FEFC"), "ff" * 8))                  # fault change in window 5
        found = sc.analyse(windows, events)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1][3], f"{0xFF05:05X}")

    def test_nothing_changes(self):
        windows = [(10.0 * i, 10.0 * i + 1.5, "00", "FF00", "00") for i in range(5)]
        events = [(float(i), ("CAN", "FEFC"), "00") for i in range(50)]
        self.assertEqual(sc.analyse(windows, events), [])

    def test_reads_j1708_log(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "j.csv")
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["time", "mid", "frame", "decoded"])
                w.writerow(j1708_row(5.0, 245, bytes([1, 2, 3, 4])))
                w.writerow(["6.0", "", "aa bb", "BAD: not a valid frame"])
            ev = sc.read_j1708(p)
        keys = [e[1] for e in ev]
        self.assertIn(("J1708", "BAD"), keys)
        self.assertTrue(any(k[1] == "MID140 PID245" for k in keys))


if __name__ == "__main__":
    unittest.main()


class AnalyzerTest(unittest.TestCase):
    def test_windows_from_tx_frames(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "la.csv")
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["time", "line", "mid", "frame", "decoded", "marker"])
                for k in range(2):
                    fr = j1587.make_frame(80, bytes([k, 0]))
                    for i in range(5):
                        w.writerow([f"{k * 1.5 + i * 0.1:.4f}", "tx", 80, fr.hex(" "), "", 1])
                w.writerow(["0.6", "rx", 140, j1587.make_frame(140, bytes([84, 5])).hex(" "), "", 0])
            windows, events = sc.read_la(p)
        self.assertEqual([w[3] for w in windows], ["00", "01"])
        self.assertAlmostEqual(windows[0][1], 0.4)
        self.assertEqual(len(events), 1)
