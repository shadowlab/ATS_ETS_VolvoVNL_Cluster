import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import auto_sweep


def write(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class AutoSweepTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_count_candidates(self):
        self.assertEqual(auto_sweep.count_candidates(
            ["--pgns", "FF00,FF03-FFFF", "--sources", "00", "--payloads", "00,FF"]), 254 * 2)
        self.assertEqual(auto_sweep.count_candidates(
            ["--transport", "j1708", "--mids", "80,90", "--pids", "00-FF"]), 2 * 254 * 2)
        self.assertEqual(auto_sweep.count_candidates(["--pgns", "FF23", "--sources", "21", "--bits"]), 64)

    def test_report_groups_camera_hits(self):
        out = self.tmp / "run"
        d = out / "can-ffxx"
        os.makedirs(d / "cam")
        (out / "plan.json").write_text(json.dumps([{"name": "can-ffxx", "args": ["--pgns", "FF00-FFFF"]}]))
        write(d / "cam_hits.csv", ["id", "roi", "sa", "pgn", "payload", "picture"], [
            {"id": 1, "roi": "brake", "sa": "11", "pgn": "FF23", "payload": "FF" * 8, "picture": "cam/hit_0001.jpg"},
            {"id": 2, "roi": "tach", "sa": "11", "pgn": "FF23", "payload": "FF" * 8, "picture": "cam/hit_0002.jpg"},
            {"id": 3, "roi": "stop", "sa": "", "pgn": "", "payload": "", "picture": "cam/hit_0003.jpg"}])
        write(d / "tried.csv", ["sa"], [{"sa": "11"}] * 5)
        (d / "stage_done").write_text("x")
        page = Path(auto_sweep.write_report(str(out))).read_text(encoding="utf-8")
        self.assertIn("5 candidates sent, finished", page)
        self.assertIn("2 candidates moved something", page)
        self.assertIn("brake, tach (2)", page)
        self.assertIn("can-ffxx/cam/hit_0001.jpg", page)

    def test_run_resumes_and_skips_finished(self):
        fake = self.tmp / "bin"
        fake.mkdir()
        (fake / "sweep_watch.py").write_text(
            "import sys, os\n"
            "out = sys.argv[sys.argv.index('--out') + 1]\n"
            "open(os.path.join(out, 'args.txt'), 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(3 if 'FAIL' in sys.argv else 0)\n")
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps([{"name": "a", "args": ["--pgns", "FF00"]},
                                    {"name": "b", "args": ["--transport", "j1708", "--mids", "90", "--pids", "00",
                                                           "--fills", "FAIL"]}]))
        out = self.tmp / "run"
        base = ["run", "--out", str(out), "--plan", str(plan), "--no-camera", "--port", "COM11"]
        with mock.patch.object(auto_sweep, "HERE", str(fake)), mock.patch("builtins.print"):
            auto_sweep.main(base)
            self.assertTrue((out / "a" / "stage_done").exists())
            self.assertFalse((out / "b" / "stage_done").exists())   # sweep_watch failed: stop, resume later
            a_args = (out / "a" / "args.txt").read_text()
            self.assertIn("--camera none", a_args)
            self.assertIn("--j1708-port COM11", a_args)
            self.assertIn("--port COM11 --wake", (out / "b" / "args.txt").read_text())
            auto_sweep.main(base)                                    # resume: a is skipped
        self.assertEqual(len((out / "a" / "args.txt").read_text().splitlines()), 1)
        self.assertTrue((out / "review.html").exists())

    def test_relay_cycles_when_a_region_latches(self):
        fake = self.tmp / "bin"
        fake.mkdir()
        # each run sends for 2 s; the first run gets a latched event and is stopped, the second finishes
        (fake / "sweep_watch.py").write_text(
            "import sys, os, time\n"
            "out = sys.argv[sys.argv.index('--out') + 1]\n"
            "open(os.path.join(out, 'runs.txt'), 'a').write('run\\n')\n"
            "time.sleep(2)\n")
        cycles = []

        class FakeRelay:
            def cycle(self, off_s):
                cycles.append(off_s)

        def fake_watch(host, sweep, out, margin, delay, stop, log, on_event):
            time.sleep(0.5)
            on_event({"ev": "latched", "roi": "brake"}, time.time())
            stop.wait(10)

        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps([{"name": "a", "args": ["--pgns", "FF00"]}]))
        out = self.tmp / "run"
        args = ["run", "--out", str(out), "--plan", str(plan), "--relay", "COM9", "--relay-off", "0",
                "--relay-boot", "0", "--delay", "0", "--margin", "0"]
        with mock.patch.object(auto_sweep, "HERE", str(fake)), mock.patch("builtins.print"), \
                mock.patch.object(auto_sweep, "Relay", lambda port: FakeRelay()), \
                mock.patch.object(auto_sweep, "camera_ok", lambda *a: {"fps": 30}), \
                mock.patch.object(auto_sweep.tab5_cam, "post", lambda *a, **k: {}), \
                mock.patch.object(auto_sweep.tab5_cam, "watch", fake_watch):
            auto_sweep.main(args)
        self.assertEqual(len(cycles), 2)                       # stage start + the latched lamp
        self.assertEqual((out / "a" / "runs.txt").read_text().count("run"), 2)   # resumed after the cycle
        self.assertEqual(len((out / "a" / "cycles.csv").read_text().splitlines()), 3)
        self.assertTrue((out / "a" / "stage_done").exists())


if __name__ == "__main__":
    unittest.main()
