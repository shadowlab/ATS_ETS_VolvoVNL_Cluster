"""Log J1708/J1587 traffic from a USB-serial adapter wired to the XY-485.

Step 1 of the plan: see what the cluster (MID 140) says on its own,
especially requests aimed at the VECU.

    python j1708_listen.py --port COM6 --log first_power_j1708.csv
    python j1708_listen.py --port COM6 --send "80 BE 40 1F" --send-period 0.1

--send "MID DATA..." sends that frame every --send-period seconds while
listening, with the checksum added. It needs the ESP32 bridge firmware, which
puts each write on the bus once it's idle. Repeat it for several frames. The
example is MID 128 (engine) with PID 190 (engine speed) set to 0x1F40.

--vss "0,100,200,400" steps the bridge's speed-sensor output (GPIO7, see
docs/vecu_bench.md) through those frequencies in Hz, --vss-hold seconds each,
and logs a VSS row in the CSV at each step. The output is left off at exit.

    python j1708_listen.py --port COM11 --log vecu_j1708.csv --vss "0,50,100,200,400,600" --vss-hold 20

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


# The bridge ends a frame after a 3 ms pause on USB, so writes closer together would merge.
SEND_SPACING = 0.05


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


VSS_MID, VSS_PID = 254, 126   # bridge-local command, never put on the bus


def vss_frame(hz):
    """Bridge command FE 7E <Hz low> <Hz high> <checksum>: speed-sensor square wave, 0 = off."""
    if not 0 <= hz <= 0xFFFF:
        raise ValueError(f"VSS frequency {hz} Hz out of range 0-65535")
    return j1587.make_frame(VSS_MID, bytes([VSS_PID]) + int(hz).to_bytes(2, "little"))


def parse_vss(text):
    """--vss "0,100,200" into a list of whole Hz."""
    try:
        steps = [int(round(float(x))) for x in text.replace(" ", "").split(",") if x]
        for hz in steps:
            vss_frame(hz)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected Hz values 0-65535 separated by commas, got {text!r}")
    if not steps:
        raise argparse.ArgumentTypeError("--vss needs at least one frequency")
    return steps


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
    ap.add_argument("--vss", type=parse_vss, metavar="HZ,HZ,...",
                    help="step the bridge's speed-sensor output through these frequencies (Hz)")
    ap.add_argument("--vss-hold", type=float, default=20, help="seconds per --vss step (default 20)")
    ap.add_argument("--vss-loop", action="store_true", help="repeat the --vss steps until stopped")
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=args.gap)
    log_file = open(args.log, "w", newline="") if args.log else None
    writer = csv.writer(log_file) if log_file else None
    if writer:
        writer.writerow(["time", "mid", "frame", "decoded"])

    for frame in args.send:
        print(f"Sending every {args.send_period * 1000:g} ms: {frame.hex(' ')}  {j1587.describe(frame)}")
    next_send = time.time()
    vss_steps = list(args.vss or [])
    vss_index = -1
    next_vss = time.time() if vss_steps else float("inf")
    counts = Counter()
    bad = 0
    chunk = bytearray()
    start = time.time()
    try:
        while args.seconds is None or time.time() - start < args.seconds:
            if args.send and time.time() >= next_send:
                for frame in args.send:
                    port.write(frame)
                    time.sleep(SEND_SPACING)
                next_send = time.time() + args.send_period
            if time.time() >= next_vss:
                vss_index += 1
                if vss_index >= len(vss_steps) and not args.vss_loop:
                    vss_index = len(vss_steps) - 1
                    next_vss = float("inf")
                else:
                    hz = vss_steps[vss_index % len(vss_steps)]
                    port.write(vss_frame(hz))
                    time.sleep(SEND_SPACING)
                    t = time.time()
                    print(f"VSS {t - start:9.3f}  {hz} Hz")
                    if writer:
                        writer.writerow([f"{t:.4f}", "", "", f"VSS {hz} Hz"])
                    next_vss = t + args.vss_hold
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
                if writer:
                    writer.writerow([f"{t:.4f}", "", chunk.hex(" "), "BAD: not a valid frame"])
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
        if vss_steps:
            port.write(vss_frame(0))
            port.flush()
        port.close()
        if log_file:
            log_file.close()

    print("\nFrames per MID")
    for mid, n in sorted(counts.items()):
        print(f"  MID {mid:3} {j1587.MID_NAMES.get(mid, ''):22} {n}")
    print(f"Unsplittable chunks: {bad}")


if __name__ == "__main__":
    main()
