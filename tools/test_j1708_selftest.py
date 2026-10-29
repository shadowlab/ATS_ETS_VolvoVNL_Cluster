"""Offline checks for j1708_selftest. Run with: python -m unittest test_j1708_selftest"""

import io
import unittest
from collections import deque
from contextlib import redirect_stdout
from unittest import mock

import j1587
import j1708_selftest

CLUSTER_FRAME = j1587.make_frame(140, [84, 0, 190, 0, 0])
OTHER_FRAME = j1587.make_frame(128, [84, 5])


class FakePort:
    """A serial port that plays one chunk per read, then silence."""

    def __init__(self, chunks=(), echo=False):
        self.chunks = deque(chunks)
        self.echo = echo
        self.written = []

    def read(self, n):
        return self.chunks.popleft() if self.chunks else b""

    def write(self, data):
        self.written.append(bytes(data))
        if self.echo:
            self.chunks.append(bytes(data))

    def close(self):
        pass


def run(port, *extra):
    out = io.StringIO()
    args = ["j1708_selftest.py", "--port", "COM7", "--seconds", "0.1", *extra]
    with mock.patch("sys.argv", args), mock.patch.object(j1708_selftest.serial, "Serial", return_value=port), \
            mock.patch.object(j1708_selftest, "ECHO_GAP", 0), mock.patch.object(j1708_selftest, "ECHO_WAIT", 0.1), \
            redirect_stdout(out):
        code = j1708_selftest.main()
    return code, out.getvalue()


class SelfTest(unittest.TestCase):
    def test_cluster_talking_and_echo(self):
        code, out = run(FakePort([CLUSTER_FRAME, OTHER_FRAME], echo=True))
        self.assertEqual(code, 0, out)
        self.assertIn("[PASS] Listen: 2 valid frames", out)
        self.assertIn("[PASS] Cluster: MID 140", out)
        self.assertIn("[PASS] Echo: 3 of 3", out)

    def test_cluster_talking_no_echo(self):
        port = FakePort([CLUSTER_FRAME])
        code, out = run(port)
        self.assertEqual(code, 0, out)
        self.assertIn("[INFO] Echo: none of the test frames", out)
        self.assertEqual(port.written, [j1708_selftest.TEST_FRAME] * 3)

    def test_other_node_but_no_cluster(self):
        code, out = run(FakePort([OTHER_FRAME]))
        self.assertEqual(code, 0, out)
        self.assertIn("[WARN] Cluster: frames arrive but none from MID 140", out)

    def test_silent_with_cluster_expected_fails(self):
        code, out = run(FakePort())
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] Listen: nothing for", out)

    def test_silent_without_cluster_is_fine(self):
        code, out = run(FakePort(), "--no-cluster")
        self.assertEqual(code, 0, out)
        self.assertIn("[INFO] Listen: silent", out)

    def test_garbage_fails(self):
        code, out = run(FakePort([b"\x01\x02\x03\x04"]))
        self.assertEqual(code, 1, out)
        self.assertIn("A/B swapped", out)
        self.assertIn("first chunk: 01 02 03 04", out)

    def test_skip_send_writes_nothing(self):
        port = FakePort([CLUSTER_FRAME])
        code, out = run(port, "--skip-send")
        self.assertEqual(code, 0, out)
        self.assertEqual(port.written, [])
        self.assertNotIn("Echo", out)

    def test_open_failure(self):
        out = io.StringIO()
        with mock.patch("sys.argv", ["j1708_selftest.py", "--port", "COM9"]), \
                mock.patch.object(j1708_selftest.serial, "Serial", side_effect=OSError("no such port")), \
                redirect_stdout(out):
            code = j1708_selftest.main()
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] Open: OSError: no such port", out.getvalue())


if __name__ == "__main__":
    unittest.main()
