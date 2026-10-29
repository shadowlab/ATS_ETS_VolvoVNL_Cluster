"""Check the J1708 side: the XY-485, its wiring and the ESP32 bridge.

    python j1708_selftest.py --port COM7
    python j1708_selftest.py --port COM7 --no-cluster

Run it with the bridge firmware on the ESP32-S3's native USB port and the
cluster powered, as in docs/wiring.md.

1. Open: the serial port opens.
2. Listen: for a few seconds, reads what arrives on the bus and splits it into
   J1708 frames by checksum. Valid frames, especially from the cluster
   (MID 140), prove A/B polarity, the XY-485's power and bias, the RXD wire to
   GPIO18 and 9600 baud. Bytes that never form a valid frame point at swapped
   A/B, missing bias or the wrong baud rate. Silence points at power, the A/B
   wires or TXD/RXD swapped on the XY-485.
3. Echo: sends three harmless frames (MID 128, road speed 0, engine speed 0)
   and looks for them coming back. The XY-485 only echoes if its receiver
   stays on while it sends, which auto-direction boards often don't do, so a
   missing echo is information, not a failure. An echo proves the whole send
   path (GPIO17, the XY-485, the bus and back).

--no-cluster is for the bench with the cluster off or unplugged, where
silence is expected. Exit code 0 when nothing failed.
"""

import argparse
import time
from collections import Counter

import serial

import j1587
from can_selftest import Report

LISTEN_SECONDS = 5.0
ECHO_FRAMES = 3
ECHO_GAP = 0.2  # seconds between test frames
ECHO_WAIT = 1.0  # seconds to listen for the echoes
CLUSTER_MID = 140
TEST_FRAME = j1587.make_frame(128, [84, 0, 190, 0, 0])


def read_frames(port, seconds):
    """J1708 frames heard within the time, as (seconds in, frame), and the
    chunks that wouldn't split into valid frames."""
    frames, bad = [], []
    chunk = bytearray()
    start = time.time()
    while time.time() - start < seconds:
        data = port.read(256)
        if data:
            chunk += data
            continue
        if not chunk:
            continue
        split = j1587.split_frames(chunk)
        if split is None:
            bad.append(bytes(chunk))
        else:
            frames += [(time.time() - start, f) for f in split]
        chunk.clear()
    return frames, bad


def check_listen(port, args, report):
    frames, bad = read_frames(port, args.seconds)
    mids = Counter(f[0] for _, f in frames)
    seen = ", ".join(f"MID {mid} ({n})" for mid, n in sorted(mids.items()))
    if frames and len(bad) <= len(frames):
        report.line("PASS", f"Listen: {len(frames)} valid frames in {args.seconds:g} s from {seen}. "
                    "A/B polarity, bias, RXD to GPIO18 and 9600 baud work for receiving")
        if bad:
            report.line("INFO", f"  {len(bad)} chunk(s) didn't split into frames; a few are normal when "
                        "two nodes collide, a lot means noise or weak bias")
        if CLUSTER_MID in mids:
            report.line("PASS", f"Cluster: MID {CLUSTER_MID} is talking on J1708")
        elif not args.no_cluster:
            report.line("WARN", f"Cluster: frames arrive but none from MID {CLUSTER_MID}; is the cluster "
                        "what's on this bus, and is it powered (A:1, A:3)?")
    elif bad:
        report.line("FAIL", f"Listen: {len(bad)} chunk(s) arrived and {len(frames)} valid frame(s). Bytes "
                    "that don't form frames mean A/B swapped, no bias (the XY-485's R1/R2), or the wrong baud rate")
        report.line("INFO", f"  first chunk: {bad[0].hex(' ')}")
    elif args.no_cluster:
        report.line("INFO", f"Listen: silent for {args.seconds:g} s (expected with the cluster off)")
    else:
        report.line("FAIL", f"Listen: nothing for {args.seconds:g} s. Check the cluster is powered, A:17/A:18 "
                    "reach the XY-485 A/B, the XY-485 has VCC and GND, and TXD/RXD aren't swapped "
                    "(its RXD goes to GPIO18); swap GPIO17/GPIO18 wires if the board labels are unclear")
    return frames


def check_echo(port, report):
    read_frames(port, 0.2)  # let anything already in flight pass
    for _ in range(ECHO_FRAMES):
        port.write(TEST_FRAME)
        time.sleep(ECHO_GAP)
    frames, _ = read_frames(port, ECHO_WAIT)
    echoes = sum(1 for _, f in frames if f == TEST_FRAME)
    if echoes:
        report.line("PASS", f"Echo: {echoes} of {ECHO_FRAMES} test frames came back, so sending works "
                    "all the way out the XY-485 and the receive path hears it too")
    else:
        report.line("INFO", "Echo: none of the test frames came back. Normal for an auto-direction XY-485, "
                    "which switches its receiver off while sending. The send path can't be checked from the PC: "
                    "watch A-B dip on a meter while it sends, capture GPIO17 with la_capture.py, or see the "
                    "cluster react")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. COM7 or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--gap", type=float, default=0.004, help="idle seconds that end a chunk")
    ap.add_argument("--seconds", type=float, default=LISTEN_SECONDS, help="how long to listen")
    ap.add_argument("--no-cluster", action="store_true", help="the cluster is off: silence is fine")
    ap.add_argument("--skip-send", action="store_true", help="don't send the echo frames")
    args = ap.parse_args()
    report = Report()

    try:
        port = serial.Serial(args.port, args.baud, timeout=args.gap)
    except Exception as e:  # noqa: BLE001 - any failure here is the answer
        report.line("FAIL", f"Open: {type(e).__name__}: {e}")
        return 1
    report.line("PASS", f"Open: {args.port}")
    try:
        check_listen(port, args, report)
        if not args.skip_send:
            check_echo(port, report)
    finally:
        port.close()

    print("\nAll good." if not report.failed else "\nSomething failed, see above.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
