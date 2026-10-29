"""Log everything on J1939 CAN1 and summarize what the cluster sends.

Step 1 of the plan: power the cluster with nothing else on the bus and see
which PGNs it asks for (Request, 0xEA00) and which address it claims
(Address Claimed, 0xEE00).

    python can_listen.py --interface slcan --channel COM5 --log first_power.csv
    python can_listen.py --interface slcan --channel COM5 --poke
    python can_listen.py --interface gs_usb --channel 0 --log first_power.csv
    python can_listen.py --interface gs_usb --channel 0 --live
    python can_listen.py --interface gs_usb --channel 0 --send "F004:00:FF FF FF 40 1F FF FF FF" --send-period 0.01

--poke sends a global Request for Address Claimed from 0xF9 once at start,
which every J1939 node should answer.

--send PGN:SA:DATA sends that frame every --send-period seconds while
listening, like can_send.py. Repeat it for several frames. On Windows only
one program can open a gs_usb CANable, so this is how to send and log at
once. The example is EEC1 at 1000 rpm.

--live shows one row per source and PGN, updated in place, with the bytes
that just changed highlighted. Error frames (no ACK, bus off, wrong bitrate)
are counted and shown either way.

--log writes CSV, or a python-can log if the name ends in .asc, .blf or
.trc (.asc opens in SavvyCAN).
"""

import argparse
import csv
import os
import time
from collections import Counter, defaultdict

import can

import can_send
import canbus
import j1939

LIVE_REFRESH = 0.25
CHANGE_HIGHLIGHT = 1.0  # seconds a changed byte stays highlighted
STALE = 2.0  # rows not heard for this long are dimmed


def new_entry():
    return {"count": 0, "first": 0.0, "last": 0.0, "data": b"", "changed": [0.0] * 8}


def update(entry, data, t):
    """Count a frame and note which bytes changed since the last one."""
    if entry["count"]:
        for i, b in enumerate(data):
            if i >= len(entry["data"]) or entry["data"][i] != b:
                entry["changed"][i] = t
    else:
        entry["first"] = t
    entry["count"] += 1
    entry["last"] = t
    entry["data"] = data


def parse_send(text):
    """--send "PGN:SA:DATA" (all hex) into a frame."""
    try:
        pgn, sa, data = text.split(":", 2)
        return can_send.build_message(int(pgn, 16), int(sa, 16), can_send.parse_hex(data))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected PGN:SA:DATA in hex, e.g. F004:00:FF FF FF 40 1F, got {text!r}")


def render(seen, now, start, errors, last_error, requests):
    """The live view, as lines of text with ANSI highlighting."""
    total = sum(e["count"] for e in seen.values())
    lines = [f"{now - start:8.1f} s   {total} frames   {len(seen)} source/PGN pairs   "
             f"error frames {errors}" + (f"  (last: {last_error})" if last_error else ""),
             "",
             f"{'SA':2}  {'PGN':5} {'name':15} {'count':>7} {'period':>9}  data"]
    for (sa, pgn), e in sorted(seen.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        period = (e["last"] - e["first"]) / (e["count"] - 1) * 1000 if e["count"] > 1 else 0
        cells = []
        for i, b in enumerate(e["data"]):
            cell = f"{b:02X}"
            cells.append(f"\x1b[7m{cell}\x1b[27m" if now - e["changed"][i] < CHANGE_HIGHLIGHT else cell)
        row = (f"{sa:02X}  {pgn:05X} {j1939.pgn_name(pgn):15} {e['count']:7} {period:7.1f}ms  "
               + " ".join(cells))
        lines.append(f"\x1b[2m{row}\x1b[22m" if now - e["last"] > STALE else row)
    if requests:
        lines += ["", "Requested: " + ", ".join(f"SA {sa:02X} -> {req:05X} {j1939.pgn_name(req)}"
                                                 for sa, req in sorted(requests))]
    return lines


def draw(lines):
    # Home, rewrite each line and clear what's left, so the table doesn't flicker.
    print("\x1b[H" + "".join(line + "\x1b[K\n" for line in lines) + "\x1b[J", end="", flush=True)


class Log:
    """CSV with J1939 columns, or any format python-can writes."""

    def __init__(self, path):
        self.csv_file = self.logger = self.writer = None
        if path.lower().endswith(".csv"):
            self.csv_file = open(path, "w", newline="")
            self.writer = csv.writer(self.csv_file)
            self.writer.writerow(["time", "id", "sa", "pgn", "name", "dlc", "data"])
        else:
            self.logger = can.Logger(path)

    def write(self, msg, t):
        if self.logger:
            self.logger.on_message_received(msg)
        elif msg.is_error_frame:
            self.writer.writerow([f"{t:.4f}", "ERROR", "", "", canbus.describe_error(msg), msg.dlc,
                                  bytes(msg.data).hex(" ")])
        else:
            _, pgn, sa, _ = j1939.parse_id(msg.arbitration_id)
            self.writer.writerow([f"{t:.4f}", f"{msg.arbitration_id:08X}", f"{sa:02X}", f"{pgn:05X}",
                                  j1939.pgn_name(pgn), msg.dlc, bytes(msg.data).hex(" ")])

    def close(self):
        if self.logger:
            self.logger.stop()
        if self.csv_file:
            self.csv_file.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    canbus.add_bus_args(ap)
    ap.add_argument("--log", help="CSV file to write every frame to")
    ap.add_argument("--poke", action="store_true", help="send a Request for Address Claimed at start")
    ap.add_argument("--send", type=parse_send, action="append", default=[], metavar="PGN:SA:DATA",
                    help="send this frame periodically while listening (repeatable)")
    ap.add_argument("--send-period", type=float, default=0.1, help="seconds between --send frames (default 0.1)")
    ap.add_argument("--quiet", action="store_true", help="only print new PGNs, requests and claims")
    ap.add_argument("--live", action="store_true", help="live table instead of one line per frame")
    ap.add_argument("--seconds", type=float, help="stop after this many seconds")
    args = ap.parse_args()

    bus = canbus.open_bus(args)
    log = Log(args.log) if args.log else None

    if args.poke:
        bus.send(can.Message(arbitration_id=j1939.make_id(0xEA00, 0xF9), data=[0x00, 0xEE, 0x00], is_extended_id=True))
    tasks = []
    for msg in args.send:
        print(f"Sending every {args.send_period * 1000:g} ms: {msg.arbitration_id:08X}  {bytes(msg.data).hex(' ')}  "
              f"{j1939.describe(msg.arbitration_id, bytes(msg.data))}")
        tasks.append(bus.send_periodic(msg, args.send_period))

    seen = defaultdict(new_entry)
    requests = set()
    errors = Counter()
    last_error = ""
    start = time.time()
    next_draw = 0.0
    if args.live:
        os.system("")  # turns on ANSI escapes in the Windows console
        print("\x1b[2J", end="")
    try:
        while args.seconds is None or time.time() - start < args.seconds:
            msg = bus.recv(timeout=0.1 if args.live else 0.5)
            t = time.time()
            if args.live and t >= next_draw:
                draw(render(seen, t, start, sum(errors.values()), last_error, requests))
                next_draw = t + LIVE_REFRESH
            # Not is_rx: the adapter echoing a frame we sent (--poke, --send).
            if msg is None or not msg.is_rx:
                continue
            if log:
                log.write(msg, t)
            if msg.is_error_frame:
                last_error = canbus.describe_error(msg)
                kind = canbus.describe_error(msg, counters=False)
                errors[kind] += 1
                if not args.live and errors[kind] <= 3:
                    print(f"ERR  {t - start:9.3f}  {last_error}")
                continue
            if not msg.is_extended_id:
                continue
            _, pgn, sa, _ = j1939.parse_id(msg.arbitration_id)
            data = bytes(msg.data)
            entry = seen[(sa, pgn)]
            is_new = entry["count"] == 0
            update(entry, data, t)

            if pgn == 0xEA00 and j1939.requested_pgn(data) is not None:
                requests.add((sa, j1939.requested_pgn(data)))
            if not args.live and (not args.quiet or is_new or pgn in (0xEA00, 0xEE00)):
                flag = "NEW " if is_new else "    "
                print(f"{flag}{t - start:9.3f}  {msg.arbitration_id:08X}  {data.hex(' '):23}  "
                      f"{j1939.describe(msg.arbitration_id, data)}")
    except KeyboardInterrupt:
        pass
    finally:
        for task in tasks:
            task.stop()
        canbus.close_bus(bus)
        if log:
            log.close()

    print("\nSummary (source, PGN, count, average period, last payload)")
    for (sa, pgn), e in sorted(seen.items()):
        period = (e["last"] - e["first"]) / (e["count"] - 1) * 1000 if e["count"] > 1 else 0
        print(f"  SA {sa:02X}  PGN {pgn:05X} {j1939.pgn_name(pgn):15} {e['count']:6}  "
              f"{period:8.1f} ms  {e['data'].hex(' ')}")
    if requests:
        print("\nPGNs requested")
        for sa, req in sorted(requests):
            print(f"  SA {sa:02X} asked for {req:05X} {j1939.pgn_name(req)}")

    if errors:
        print("\nError frames")
        for text, n in errors.most_common():
            print(f"  {n:6}  {text}")


if __name__ == "__main__":
    main()
