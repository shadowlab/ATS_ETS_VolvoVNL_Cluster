"""Capture the bench with the 8-channel logic analyzer and decode J1708.

Uses sigrok-cli with the fx2lafw driver (the Lonely Binary 8-channel and
other 24 MHz FX2 analyzers). Captures are .sr files that open in PulseView;
the J1708 lines are also decoded into a CSV like j1708_listen.py's, with a
column saying whether each frame went out under the ESP32's sweep marker.

    python la_capture.py scan
    python la_capture.py selftest --bridge COM7
    python la_capture.py capture --seconds 30 --out first_power.sr --log first_power_la.csv
    python la_capture.py decode first_power.sr --log first_power_la.csv

Channels follow docs/wiring.md: D0 GPIO4 (CAN TX, stage 2), D1 GPIO5
(CAN RX, stage 2), D2 GPIO17 (J1708 TX), D3 GPIO18 (J1708 RX), D4 GPIO6
(sweep marker). Probe the 3.3 V logic side only.

selftest needs no cluster: with the ESP32 running the J1708 bridge and
D2 on GPIO17, D4 on GPIO6 and GND on the ESP32's GND, it sends J1587
frames through the bridge while capturing, and checks that the analyzer
saw each one intact, inside a marker pulse.
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import j1587

J1708_BAUD = 9600
J1708_LINES = {"tx": "D2", "rx": "D3"}
MARKER = "D4"
FRAME_GAP = 10 / J1708_BAUD  # J1708: 10 idle bit times end a frame
WINDOWS_PATHS = [r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe",
                 r"C:\Program Files (x86)\sigrok\sigrok-cli\sigrok-cli.exe"]
TEST_FRAMES = [j1587.make_frame(128, [84, i, 190, 0x40, 0x1F]) for i in range(10)]


def find_sigrok(path=None):
    for candidate in [path, shutil.which("sigrok-cli")] + WINDOWS_PATHS:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise SystemExit("sigrok-cli not found. Install it from sigrok.org (Downloads, sigrok-cli for Windows), "
                     "or pass --sigrok with its path.")


def run(sigrok, *args, timeout=None):
    result = subprocess.run([sigrok, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise SystemExit(f"sigrok-cli {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def capture_args(args, out):
    return ["-d", args.driver, "--config", f"samplerate={args.samplerate}",
            "--time", f"{int(args.seconds * 1000)}ms", "-o", out]


def file_info(sigrok, path):
    """Sample rate in Hz and channel names, by index, of a capture."""
    text = run(sigrok, "-i", path, "--show")
    m = re.search(r"Samplerate:\s*([\d.]+)\s*([kMG]?)Hz", text) or re.search(r"Samplerate:\s*([\d.]+)()", text)
    rate = float(m.group(1)) * {"": 1, "k": 1e3, "M": 1e6, "G": 1e9}[m.group(2)]
    names = re.findall(r"^- (\S+?):", text, re.M)
    return rate, names


def channel(names, wanted):
    """sigrok's name for a channel: D2 on fx2lafw, 2 in some file formats."""
    if wanted in names:
        return wanted
    index = int(wanted.lstrip("D"))
    return names[index] if index < len(names) else wanted


def parse_uart(text, rate):
    """(start s, end s, byte) from uart annotations with sample numbers."""
    out = []
    for line in text.splitlines():
        m = re.match(r"(\d+)-(\d+) \S+: ([0-9A-Fa-f]{2})$", line.strip())
        if m:
            out.append((int(m.group(1)) / rate, int(m.group(2)) / rate, int(m.group(3), 16)))
    return out


def group_frames(decoded):
    """Split decoded bytes into chunks at J1708's frame gap, then into frames
    by checksum. Returns (start s, frame bytes or None for a bad chunk, raw)."""
    chunks = []
    for start, end, byte in decoded:
        if chunks and start - chunks[-1][1] < FRAME_GAP:
            chunks[-1][1] = end
            chunks[-1][2].append(byte)
        else:
            chunks.append([start, end, bytearray([byte])])
    out = []
    for start, _, raw in chunks:
        frames = j1587.split_frames(raw)
        if frames is None:
            out.append((start, None, bytes(raw)))
        else:
            out.extend((start, bytes(f), bytes(f)) for f in frames)
    return out


def parse_vcd_pulses(text, name):
    """High pulses of one channel in a VCD dump, as (start, end) in seconds.
    The whole dump is parsed because sigrok-cli 0.7's -C on a saved file
    picks the wrong channel."""
    scale = 1e-12
    m = re.search(r"\$timescale\s+(\d+)\s*(s|ms|us|ns|ps)", text)
    if m:
        scale = int(m.group(1)) * {"s": 1, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12}[m.group(2)]
    m = re.search(r"\$var wire 1 (\S+) " + re.escape(name) + r" \$end", text)
    if not m:
        return []
    ident = m.group(1)
    body = text[text.index("$enddefinitions"):].split("$end", 1)[1]
    pulses, t, rise = [], 0.0, None
    for token in body.split():
        if token.startswith("#"):
            t = int(token[1:]) * scale
        elif token[1:] == ident and token[0] in "01":
            if token[0] == "1" and rise is None:
                rise = t
            elif token[0] == "0" and rise is not None:
                pulses.append((rise, t))
                rise = None
    if rise is not None:
        pulses.append((rise, t))
    return pulses


def decode(sigrok, path):
    """Every J1708 frame on both lines, and the marker pulses."""
    rate, names = file_info(sigrok, path)
    frames = []
    for line, wanted in J1708_LINES.items():
        ch = channel(names, wanted)
        text = run(sigrok, "-i", path, "-P", f"uart:rx={ch}:baudrate={J1708_BAUD}:format=hex",
                   "-A", "uart=rx-data", "--protocol-decoder-samplenum")
        frames += [(start, line, frame, raw) for start, frame, raw in group_frames(parse_uart(text, rate))]
    marker = parse_vcd_pulses(run(sigrok, "-i", path, "-O", "vcd"), channel(names, MARKER))
    return sorted(frames, key=lambda f: f[0]), marker


def under_marker(t, pulses):
    return any(start - FRAME_GAP <= t <= end for start, end in pulses)


def report(frames, marker, log_path=None):
    writer = None
    log_file = open(log_path, "w", newline="") if log_path else None
    if log_file:
        writer = csv.writer(log_file)
        writer.writerow(["time", "line", "mid", "frame", "decoded", "marker"])
    for t, line, frame, raw in frames:
        mark = under_marker(t, marker)
        text = j1587.describe(frame) if frame else "BAD (checksum or framing)"
        print(f"{line:2} {t:9.4f}  {'M' if mark else ' '}  {raw.hex(' '):40}  {text}")
        if writer:
            writer.writerow([f"{t:.6f}", line, frame[0] if frame else "", raw.hex(" "), text, int(mark)])
    if log_file:
        log_file.close()
    bad = sum(1 for f in frames if f[2] is None)
    print(f"\n{len(frames) - bad} frames, {bad} bad, {len(marker)} marker pulses")


def cmd_scan(args):
    print(run(find_sigrok(args.sigrok), "--scan", timeout=30).strip() or "No devices found.")


def cmd_capture(args):
    sigrok = find_sigrok(args.sigrok)
    print(f"Capturing {args.seconds} s at {args.samplerate} to {args.out}")
    run(sigrok, *capture_args(args, args.out))
    report(*decode(sigrok, args.out), args.log)


def cmd_decode(args):
    report(*decode(find_sigrok(args.sigrok), args.file), args.log)


def cmd_selftest(args):
    import serial

    sigrok = find_sigrok(args.sigrok)
    scan = run(sigrok, "--scan", timeout=30)
    if args.driver not in scan:
        print(scan.strip() or "No devices found.")
        raise SystemExit(f"[FAIL] No {args.driver} analyzer found. Check Zadig (WinUSB) and the USB cable.")
    print(f"[PASS] Analyzer found: {scan.strip().splitlines()[-1].strip()}")

    out = args.out or os.path.join(tempfile.gettempdir(), "la_selftest.sr")
    port = serial.Serial(args.bridge, 115200, timeout=0)
    proc = subprocess.Popen([sigrok, *capture_args(args, out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1.0)  # the FX2 takes a moment to load firmware and start sampling
        for frame in TEST_FRAMES:
            port.write(frame)
            time.sleep(0.1)
        _, err = proc.communicate(timeout=args.seconds + 30)
    finally:
        port.close()
        if proc.poll() is None:
            proc.kill()
    if proc.returncode:
        raise SystemExit(f"[FAIL] Capture failed:\n{err.strip()}")
    print(f"[PASS] Captured {args.seconds:.0f} s at {args.samplerate} ({out}, opens in PulseView)")

    frames, marker = decode(sigrok, out)
    sent = [f for _, line, f, _ in frames if line == "tx"]
    marked = [f for t, line, f, _ in frames if line == "tx" and under_marker(t, marker)]
    failed = False
    if sent == TEST_FRAMES:
        print(f"[PASS] J1708 TX (D2): all {len(TEST_FRAMES)} frames decoded intact")
    elif not sent:
        failed = True
        print("[FAIL] J1708 TX (D2): nothing decoded. Check D2 is on GPIO17, GND on the ESP32's GND, "
              "and that the bridge firmware is running on --bridge's port")
    else:
        failed = True
        print(f"[FAIL] J1708 TX (D2): sent {len(TEST_FRAMES)}, decoded {len(sent)}, "
              f"{sum(1 for f in TEST_FRAMES if f not in sent)} missing or changed")
    if len(marker) >= len(TEST_FRAMES) and len(marked) == len(sent) and sent:
        print(f"[PASS] Marker (D4): {len(marker)} pulses, every frame inside one")
    elif not marker:
        failed = True
        print("[FAIL] Marker (D4): no pulses. Check D4 is on GPIO6")
    else:
        failed = True
        print(f"[FAIL] Marker (D4): {len(marker)} pulses, {len(marked)} of {len(sent)} frames inside one")
    rx = [f for _, line, f, _ in frames if line == "rx"]
    if rx:
        print(f"[INFO] J1708 RX (D3): {len(rx)} frames (the XY-485 echoing our own, or bus traffic)")
    print("\nSomething failed, see above." if failed else "\nAll good.")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sigrok", help="path to sigrok-cli, if it isn't found on its own")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="list analyzers sigrok can see")

    def capture_opts(p):
        p.add_argument("--driver", default="fx2lafw")
        p.add_argument("--samplerate", default="2m", help="2m gives 8 samples per CAN bit at 250k")
        p.add_argument("--seconds", type=float, default=10.0)

    p = sub.add_parser("capture", help="capture, save a .sr for PulseView, and decode J1708")
    capture_opts(p)
    p.add_argument("--out", default="capture.sr")
    p.add_argument("--log", help="CSV of decoded J1708 frames")

    p = sub.add_parser("decode", help="decode J1708 from a saved .sr")
    p.add_argument("file")
    p.add_argument("--log", help="CSV of decoded J1708 frames")

    p = sub.add_parser("selftest", help="no-cluster check through the ESP32 J1708 bridge")
    capture_opts(p)
    p.add_argument("--bridge", required=True, help="the ESP32's COM port")
    p.add_argument("--out", help="where to keep the capture (default: temp folder)")
    p.set_defaults(seconds=3.0)

    args = ap.parse_args()
    return {"scan": cmd_scan, "capture": cmd_capture, "decode": cmd_decode, "selftest": cmd_selftest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
