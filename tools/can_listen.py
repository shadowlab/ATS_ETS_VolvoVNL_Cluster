"""Log everything on J1939 CAN1 and summarize what the cluster sends.

Step 1 of the plan: power the cluster with nothing else on the bus and see
which PGNs it asks for (Request, 0xEA00) and which address it claims
(Address Claimed, 0xEE00).

    python can_listen.py --interface slcan --channel COM5 --log first_power.csv
    python can_listen.py --interface slcan --channel COM5 --poke
    python can_listen.py --interface gs_usb --channel 0 --log first_power.csv

--poke sends a global Request for Address Claimed from 0xF9 once at start,
which every J1939 node should answer.
"""

import argparse
import csv
import time
from collections import defaultdict

import can

import canbus
import j1939


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    canbus.add_bus_args(ap)
    ap.add_argument("--log", help="CSV file to write every frame to")
    ap.add_argument("--poke", action="store_true", help="send a Request for Address Claimed at start")
    ap.add_argument("--quiet", action="store_true", help="only print new PGNs, requests and claims")
    ap.add_argument("--seconds", type=float, help="stop after this many seconds")
    args = ap.parse_args()

    bus = canbus.open_bus(args)
    log_file = open(args.log, "w", newline="") if args.log else None
    writer = csv.writer(log_file) if log_file else None
    if writer:
        writer.writerow(["time", "id", "sa", "pgn", "name", "dlc", "data"])

    if args.poke:
        bus.send(can.Message(arbitration_id=j1939.make_id(0xEA00, 0xF9), data=[0x00, 0xEE, 0x00], is_extended_id=True))

    seen = defaultdict(lambda: {"count": 0, "first": 0.0, "last": 0.0, "data": b""})
    requests = set()
    start = time.time()
    try:
        while args.seconds is None or time.time() - start < args.seconds:
            msg = bus.recv(timeout=0.5)
            if msg is None or not msg.is_extended_id or msg.is_error_frame:
                continue
            t = time.time()
            _, pgn, sa, _ = j1939.parse_id(msg.arbitration_id)
            data = bytes(msg.data)
            key = (sa, pgn)
            entry = seen[key]
            is_new = entry["count"] == 0
            if is_new:
                entry["first"] = t
            entry["count"] += 1
            entry["last"] = t
            entry["data"] = data

            if pgn == 0xEA00:
                requests.add((sa, j1939.requested_pgn(data)))
            if writer:
                writer.writerow([f"{t:.4f}", f"{msg.arbitration_id:08X}", f"{sa:02X}", f"{pgn:05X}",
                                 j1939.pgn_name(pgn), msg.dlc, data.hex(" ")])
            if not args.quiet or is_new or pgn in (0xEA00, 0xEE00):
                flag = "NEW " if is_new else "    "
                print(f"{flag}{t - start:9.3f}  {msg.arbitration_id:08X}  {data.hex(' '):23}  "
                      f"{j1939.describe(msg.arbitration_id, data)}")
    except KeyboardInterrupt:
        pass
    finally:
        canbus.close_bus(bus)
        if log_file:
            log_file.close()

    print("\nSummary (source, PGN, count, average period, last payload)")
    for (sa, pgn), e in sorted(seen.items()):
        period = (e["last"] - e["first"]) / (e["count"] - 1) * 1000 if e["count"] > 1 else 0
        print(f"  SA {sa:02X}  PGN {pgn:05X} {j1939.pgn_name(pgn):15} {e['count']:6}  "
              f"{period:8.1f} ms  {e['data'].hex(' ')}")
    if requests:
        print("\nPGNs requested")
        for sa, req in sorted(requests, key=lambda r: (r[0], r[1] or 0)):
            print(f"  SA {sa:02X} asked for {req:05X} {j1939.pgn_name(req)}")


if __name__ == "__main__":
    main()
