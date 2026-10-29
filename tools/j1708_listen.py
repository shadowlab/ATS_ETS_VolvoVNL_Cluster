"""Log J1708/J1587 traffic from a USB-serial adapter wired to the XY-485.

Step 1 of the plan: see what the cluster (MID 140) says on its own,
especially requests aimed at the VECU.

    python j1708_listen.py --port COM6 --log first_power_j1708.csv
    python j1708_listen.py --port COM6 --send "80 BE 40 1F" --send-period 0.1

--send "MID DATA..." sends that frame every --send-period seconds while
listening, with the checksum added. It needs the ESP32 bridge firmware, which
puts each write on the bus once it's idle. Repeat it for several frames. The
example is MID 128 (engine) with PID 190 (engine speed) set to 0x1F40.

Bytes that arrive close together are treated as one chunk and split into
frames by checksum. Chunks that don't split cleanly are printed as BAD; a
lot of those means wiring or bias trouble, or the wrong baud rate.
"""

import argparse
import csv
import time
from collections import Counter

import serial

import j1587


def parse_send(text):
    """--send "MID DATA..." (hex bytes, no checksum) into a frame."""
    try:
        raw = bytes(int(b, 16) for b in text.replace(",", " ").split())
    except ValueError:
        raw = b""
    frame = j1587.make_frame(raw[0], raw[1:]) if raw else b""
    if len(raw) < 2 or len(frame) > j1587.MAX_FRAME:
        raise argparse.ArgumentTypeError(f"expected MID and data as hex bytes, at most {j1587.MAX_FRAME - 2} "
                                         f"after the MID, e.g. 80 BE 40 1F, got {text!r}")
    return frame


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. COM6 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--gap", type=float, default=0.004, help="idle seconds that end a chunk")
    ap.add_argument("--send", type=parse_send, action="append", default=[], metavar="MID DATA",
                    help="send this frame periodically while listening (repeatable)")
    ap.add_argument("--send-period", type=float, default=0.1, help="seconds between --send rounds (default 0.1)")
    ap.add_argument("--log", help="CSV file to write every frame to")
    ap.add_argument("--seconds", type=float, help="stop after this many seconds")
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=args.gap)
    log_file = open(args.log, "w", newline="") if args.log else None
    writer = csv.writer(log_file) if log_file else None
    if writer:
        writer.writerow(["time", "mid", "frame", "decoded"])

    for frame in args.send:
        print(f"Sending every {args.send_period * 1000:g} ms: {frame.hex(' ')}  {j1587.describe(frame)}")
    next_send = time.time()
    counts = Counter()
    bad = 0
    chunk = bytearray()
    start = time.time()
    try:
        while args.seconds is None or time.time() - start < args.seconds:
            if args.send and time.time() >= next_send:
                for frame in args.send:
                    port.write(frame)
                next_send = time.time() + args.send_period
            data = port.read(256)
            if data:
                chunk += data
                continue
            if not chunk:
                continue
            t = time.time()
            frames = j1587.split_frames(chunk)
            if frames is None:
                bad += 1
                print(f"BAD {t - start:9.3f}  {chunk.hex(' ')}")
            else:
                for frame in frames:
                    counts[frame[0]] += 1
                    text = j1587.describe(frame)
                    print(f"    {t - start:9.3f}  {frame.hex(' '):40}  {text}")
                    if writer:
                        writer.writerow([f"{t:.4f}", frame[0], frame.hex(" "), text])
            chunk.clear()
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        if log_file:
            log_file.close()

    print("\nFrames per MID")
    for mid, n in sorted(counts.items()):
        print(f"  MID {mid:3} {j1587.MID_NAMES.get(mid, ''):22} {n}")
    print(f"Unsplittable chunks: {bad}")


if __name__ == "__main__":
    main()
