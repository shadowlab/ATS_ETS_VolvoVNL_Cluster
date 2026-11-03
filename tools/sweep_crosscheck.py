"""Find sweep candidates that changed what the cluster sends, without the camera.

Run a sweep while can_listen.py and/or j1708_listen.py log to CSV on the same
PC clock (all three write epoch seconds). The sweep writes windows.csv with
the time span of every candidate. This tool looks at what the cluster sent
during each window and flags windows that differ from the quiet background:

  * a message (CAN id+PGN, or J1708 MID+PID) that appears only in that window
  * a message with few distinct values overall (fault lists, status bits)
    whose value in the window is rare, or an error / BAD frame burst

Changing values (odometer, clock, counters) are ignored: a message with more
than --max-values distinct values is treated as noise.

    python sweep_crosscheck.py sweeps/run1 --can can.csv --j1708 j1708.csv

Prints a ranked list and writes crosscheck.csv in the sweep folder. It only
points at candidates worth a second look; confirm by resending one.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import j1587


def read_windows(path):
    with open(path, newline="") as f:
        return [(float(r["start"]), float(r["end"]), r["sa"], r["pgn"], r["payload"])
                for r in csv.DictReader(f)]


def read_can(path, cluster_sa="17"):
    """(time, key, value) for frames sent by the cluster; errors get key ('ERR',)."""
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                t = float(r["time"])
            except (KeyError, ValueError):
                continue
            if r["id"] == "ERROR":
                out.append((t, ("CAN", "ERROR"), r["data"]))
            elif r["sa"].upper() == cluster_sa.upper():
                out.append((t, ("CAN", r["pgn"].upper()), r["data"].replace(" ", "").lower()))
    return out


def read_j1708(path, cluster_mid=140):
    """(time, key, value) per PID in frames from the cluster; BAD chunks get key ('J1708','BAD')."""
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                t = float(r["time"])
            except (KeyError, ValueError):
                continue
            if r["decoded"].startswith("BAD"):
                out.append((t, ("J1708", "BAD"), ""))
                continue
            try:
                frame = bytes.fromhex(r["frame"])
            except ValueError:
                continue
            if not frame or frame[0] != cluster_mid or len(frame) < 3:
                continue
            for pid, val in j1587.parse_params(frame[1:-1]):
                out.append((t, ("J1708", f"MID{frame[0]} PID{pid}"), val.hex()))
    return out


def analyse(windows, events, max_values=8, margin=0.3):
    """Return [(score, window, [reasons])] for windows with something unusual.

    margin: seconds added after each window for the cluster's reply to arrive.
    Events inside any window are 'active'; the rest is background."""
    events = sorted(events, key=lambda e: e[0])
    spans = sorted((w[0], w[1] + margin) for w in windows)

    def window_of(t):
        for i, w in enumerate(windows):
            if w[0] <= t <= w[1] + margin:
                return i
        return None

    values = defaultdict(lambda: defaultdict(int))     # key -> value -> count overall
    wins_with = defaultdict(lambda: defaultdict(set))  # key -> value -> {window idx}
    in_window = defaultdict(set)                       # window idx -> {(key, value)}
    background_keys = set()
    for t, key, val in events:
        i = window_of(t)
        values[key][val] += 1
        if i is None:
            background_keys.add(key)
        else:
            wins_with[key][val].add(i)
            in_window[i].add((key, val))

    n = max(len(windows), 1)
    flagged = defaultdict(list)
    for i, items in in_window.items():
        for key, val in items:
            if key[1] in ("ERROR", "BAD"):
                flagged[i].append((2, f"{key[0]} {key[1]} seen"))
                continue
            if len(values[key]) > max_values:
                continue                                # clock, odometer, counters
            if key not in background_keys and all(
                    wins_with[key][v] <= {i} for v in values[key]):
                flagged[i].append((3, f"{key[0]} {key[1]} only appears here ({val})"))
            elif len(wins_with[key][val]) <= max(1, int(0.02 * n)) and len(values[key]) > 1:
                flagged[i].append((1, f"{key[0]} {key[1]} rare value {val}"))
    result = []
    for i, reasons in flagged.items():
        result.append((sum(r[0] for r in reasons), windows[i], [r[1] for r in reasons]))
    result.sort(key=lambda x: (-x[0], x[1][0]))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sweep_dir", help="sweep folder containing windows.csv")
    ap.add_argument("--can", help="CSV from can_listen.py --log")
    ap.add_argument("--j1708", help="CSV from j1708_listen.py --log")
    ap.add_argument("--cluster-sa", default="17", help="cluster CAN source address, hex (default 17)")
    ap.add_argument("--cluster-mid", type=int, default=140)
    ap.add_argument("--max-values", type=int, default=8,
                    help="messages with more distinct values than this are treated as noise (default 8)")
    ap.add_argument("--margin", type=float, default=0.3, help="seconds after a window to still count (default 0.3)")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args(argv)
    if not (args.can or args.j1708):
        ap.error("give --can and/or --j1708")

    wpath = os.path.join(args.sweep_dir, "windows.csv")
    if not os.path.exists(wpath):
        sys.exit(f"{wpath} not found. Sweeps run before this tool existed did not write it.")
    windows = read_windows(wpath)
    events = []
    if args.can:
        events += read_can(args.can, args.cluster_sa)
    if args.j1708:
        events += read_j1708(args.j1708, args.cluster_mid)
    if not events:
        sys.exit("No cluster frames found in the logs.")
    t0, t1 = min(e[0] for e in events), max(e[0] for e in events)
    covered = [w for w in windows if t0 <= w[0] and w[1] <= t1]
    print(f"{len(windows)} candidates, {len(covered)} inside the log time range, {len(events)} cluster events")
    if len(covered) < len(windows):
        print("  (log files do not cover the whole sweep: keep them running for its full length)")

    found = analyse(windows, events, args.max_values, args.margin)
    out = os.path.join(args.sweep_dir, "crosscheck.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["score", "start", "sa", "pgn", "payload", "why"])
        for score, win, why in found:
            w.writerow([score, f"{win[0]:.3f}", win[2], win[3], win[4], "; ".join(why)])
    if not found:
        print("Nothing in the cluster's own traffic changed during any candidate.")
        return 0
    print(f"{len(found)} candidates changed the cluster's traffic (best first, full list in {out}):")
    for score, win, why in found[:args.top]:
        print(f"  score {score}  SA {win[2]} PGN/PID {win[3]} data {win[4]}: {'; '.join(why)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
