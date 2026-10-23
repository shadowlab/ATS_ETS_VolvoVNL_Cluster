"""Send one J1939 PGN repeatedly, for hand tests and for waking the cluster.

    python can_send.py --channel COM5 --pgn F004 --sa 00 --data "FF FF FF 40 1F FF FF FF" --period 0.01

That example is EEC1 from the engine with engine speed (SPN 190, bytes 4-5,
0.125 rpm per bit) set to 0x1F40 = 1000 rpm. Data bytes are hex, and bytes
not given are padded with FF ("not available"). Ctrl+C stops.
"""

import argparse
import time

import can

import j1939


def parse_hex(text):
    return bytes(int(b, 16) for b in text.replace(",", " ").split())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interface", default="slcan")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--bitrate", type=int, default=250000)
    ap.add_argument("--pgn", required=True, help="PGN in hex, e.g. F004")
    ap.add_argument("--sa", default="00", help="source address in hex (default 00, engine)")
    ap.add_argument("--da", default="FF", help="destination for PDU1 PGNs, hex (default FF, global)")
    ap.add_argument("--priority", type=int, default=6)
    ap.add_argument("--data", default="", help="payload bytes in hex")
    ap.add_argument("--period", type=float, default=0.1, help="seconds between frames; 0 sends once")
    args = ap.parse_args()

    data = parse_hex(args.data)
    data = data + b"\xFF" * (8 - len(data)) if len(data) < 8 else data
    can_id = j1939.make_id(int(args.pgn, 16), int(args.sa, 16), int(args.da, 16), args.priority)
    msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=True)
    print(f"{can_id:08X}  {data.hex(' ')}  {j1939.describe(can_id, data)}")

    bus = can.Bus(interface=args.interface, channel=args.channel, bitrate=args.bitrate)
    try:
        if args.period <= 0:
            bus.send(msg)
            return
        task = bus.send_periodic(msg, args.period)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            task.stop()
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
