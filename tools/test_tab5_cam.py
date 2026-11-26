"""Offline checks for tab5_cam. Run with: python -m unittest test_tab5_cam"""

import csv
import os
import tempfile
import unittest

import tab5_cam as tc


class Tab5CamTest(unittest.TestCase):
    def test_parse_sse(self):
        raw = [b'data: {"ev":"hello","t":5}\n', b"\n", b"id: 7\n", b'data: {"ev":"start","t":10,"id":1}\n', b"\n",
               b"data: not json\n", b"\n"]
        got = list(tc.parse_sse(raw))
        self.assertEqual(got, [(0, {"ev": "hello", "t": 5}), (7, {"ev": "start", "t": 10, "id": 1})])

    def test_clock_sync_keeps_smallest_delay_and_resets_on_reboot(self):
        c = tc.ClockSync()
        c.observe(1000, now=101.050)
        c.observe(2000, now=102.010)
        c.observe(3000, now=103.200)
        self.assertAlmostEqual(c.to_pc(5000), 105.010)
        c.observe(100, now=200.0)          # device clock went back: rebooted
        self.assertAlmostEqual(c.to_pc(100), 200.0)

    def test_match(self):
        windows = [(10.0, 11.5, "90", "46", "ff"), (11.6, 13.1, "90", "47", "ff")]
        self.assertEqual(tc.match(windows, 11.0)[0][3], "46")
        w, late = tc.match(windows, 13.4)
        self.assertEqual((w[3], late), ("47", 0.3))
        self.assertEqual(tc.match(windows, 14.0), (None, None))
        self.assertEqual(tc.match(windows, 9.0), (None, None))

    def test_match_command(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "windows.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["start", "end", "sa", "pgn", "payload"])
                w.writerow([100.0, 101.5, "90", "46", "ff"])
            for row in ({"time": "101.2", "ev": "start", "id": "3", "roi": "lamp_park", "score": "0.4"},
                        {"time": "101.3", "ev": "glare", "id": "4"}):
                tc.append(os.path.join(d, "cam_events.csv"), tc.EVENT_FIELDS, row)
            tc.main(["match", d])
            with open(os.path.join(d, "cam_hits.csv"), newline="") as f:
                hits = list(csv.DictReader(f))
            self.assertEqual(len(hits), 1)
            self.assertEqual((hits[0]["roi"], hits[0]["pgn"], hits[0]["payload"]), ("lamp_park", "46", "ff"))


if __name__ == "__main__":
    unittest.main()
