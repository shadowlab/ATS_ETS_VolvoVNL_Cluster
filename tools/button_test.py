"""Check the ESP32's GPIO41 button reaches the PC cleanly.

Needs the bridge firmware with button support (firmware/README.md) and a
button between GPIO41 and GND. Press it the requested number of times,
one clear press at a time:

    python button_test.py --port COM7 --presses 5

It prints every press as it arrives and then checks that
  * each press arrived exactly once (a bounce shows as a second press
    within 0.15 s, a miss as a gap in the press numbers),
  * the press numbers count up by one.
Exit code 0 on PASS. Other J1708 frames from the bridge are ignored.
"""

import argparse
import sys
import time

import j1587

BOUNCE_S = 0.15


def check(presses, wanted):
    """presses: [(time, seq)]. Returns (ok, [message])."""
    notes, ok = [], True
    if len(presses) < wanted:
        return False, [f"FAIL: saw {len(presses)} presses, wanted {wanted}"]
    for (t0, s0), (t1, s1) in zip(presses, presses[1:]):
        if (s1 - s0) & 0xFF != 1:
            ok = False
            notes.append(f"FAIL: press numbers jumped from {s0} to {s1} (a press was lost on the way to the PC)")
        if t1 - t0 < BOUNCE_S:
            ok = False
            notes.append(f"FAIL: presses {s0} and {s1} only {t1 - t0:.3f} s apart (contact bounce, or a very fast double press)")
    if len(presses) > wanted:
        ok = False
        notes.append(f"FAIL: {len(presses)} presses arrived, wanted {wanted}")
    if ok:
        notes.append(f"PASS: {len(presses)} presses, each seen once, numbered in order")
    return ok, notes


def read_presses(port, wanted, timeout, gap=0.004, quiet=1.0, log=print):
    """Collect button presses until `wanted` have arrived and `quiet` seconds pass with no more."""
    presses, chunk = [], bytearray()
    start = last_byte = last_press = time.time()
    while time.time() - start < timeout:
        data = port.read(256)
        now = time.time()
        if data:
            chunk += data
            last_byte = now
            continue
        if chunk and now - last_byte >= gap:
            for frame in j1587.split_frames(bytes(chunk)) or []:
                seq = j1587.parse_button(frame)
                if seq is not None:
                    presses.append((now, seq))
                    last_press = now
                    log(f"  press #{seq} at +{now - start:.1f} s")
            chunk.clear()
        if len(presses) >= wanted and now - last_press >= quiet:
            break
        time.sleep(0.001)
    return presses


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="the ESP32's native USB COM port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--presses", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=60, help="give up after this many seconds")
    args = ap.parse_args(argv)

    import serial

    port = serial.Serial(args.port, args.baud, timeout=0)
    print(f"Press the button {args.presses} times (waiting up to {args.timeout:g} s)...")
    try:
        presses = read_presses(port, args.presses, args.timeout)
    finally:
        port.close()
    ok, notes = check(presses, args.presses)
    for note in notes:
        print(note)
    if not presses:
        print("Nothing arrived: is the board running the button firmware, is the button on GPIO41 and GND, "
              "and is this the native USB port?")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
