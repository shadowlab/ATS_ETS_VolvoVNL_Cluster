"""Play a simulated truck to the cluster: key-on, then engine and VECU heartbeats.

    python can_wake.py --channel COM5 --vecu 27,21,31 --dwell 30 --rpm 1000 --kmh 50

Starts with the key off (nothing sent) for --off seconds, then sends all the
frames below together, at their usual J1939 rates. With several --vecu
addresses it tries each for --dwell seconds and loops, so one run with
j1708_listen.py logging alongside shows which address clears the cluster's
SID 201 (VECU lost). Only one program can open the CANable, which is why this
sends everything from one place. Ctrl+C stops.

Which frames the cluster needs is not known yet; this is a test, see
docs/messages.md.
"""

import argparse
import time

import canbus
import j1939
from can_send import build_message


def heartbeats(vecu, rpm, kmh, volts):
    """(name, period in s, message) for every frame of one simulated truck."""
    rpm_raw = round(rpm / 0.125)
    speed_raw = round(kmh * 256)
    volts_raw = round(volts / 0.05)
    v = volts_raw.to_bytes(2, "little")
    return [
        # VEP1: battery potential (SPN 168) and keyswitch potential (SPN 158).
        ("VEP1", 0.1, build_message(0xFEF7, vecu, bytes([0xFF] * 4) + v + v)),
        # CCVS1: parking brake released (SPN 70 = 00), wheel-based speed.
        ("CCVS1", 0.1, build_message(0xFEF1, vecu, bytes([0xF3]) + speed_raw.to_bytes(2, "little"))),
        # EEC1 with real-looking torque fields, as an engine sends it.
        ("EEC1", 0.02, build_message(0xF004, 0x00, bytes([0xF0, 0x7D, 0x83]) + rpm_raw.to_bytes(2, "little")
                                     + bytes([0x00, 0xF0, 0x7D]), priority=3)),
        # ET1: coolant 85 C (offset -40).
        ("ET1", 1.0, build_message(0xFEEE, 0x00, bytes([85 + 40]))),
        # EFL/P1: oil pressure 300 kPa in byte 4 (4 kPa/bit).
        ("EFL/P1", 0.5, build_message(0xFEEF, 0x00, bytes([0xFF, 0xFF, 0xFF, 300 // 4]))),
        # DM1 from the engine: all lamps off, no fault.
        ("DM1", 1.0, build_message(0xFECA, 0x00, bytes([0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF]))),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    canbus.add_bus_args(ap)
    ap.add_argument("--vecu", default="27", help="VECU source address(es) in hex, comma separated (default 27)")
    ap.add_argument("--dwell", type=float, default=30, help="seconds per VECU address when several are given")
    ap.add_argument("--off", type=float, default=2, help="seconds of silence before key-on")
    ap.add_argument("--rpm", type=float, default=0)
    ap.add_argument("--kmh", type=float, default=0)
    ap.add_argument("--volts", type=float, default=13.8)
    args = ap.parse_args()
    addresses = [int(a, 16) for a in args.vecu.split(",")]

    bus = canbus.open_bus(args)
    try:
        time.sleep(args.off)
        while True:
            for vecu in addresses:
                frames = heartbeats(vecu, args.rpm, args.kmh, args.volts)
                print(time.strftime("%H:%M:%S"), f"key on, VECU at SA 0x{vecu:02X}")
                for name, period, msg in frames:
                    print(f"  {name:7} every {period * 1000:g} ms  {msg.arbitration_id:08X}  {bytes(msg.data).hex(' ')}"
                          f"  {j1939.describe(msg.arbitration_id, bytes(msg.data))}")
                tasks = [bus.send_periodic(msg, period) for _, period, msg in frames]
                try:
                    time.sleep(args.dwell if len(addresses) > 1 else 1e9)
                finally:
                    for task in tasks:
                        task.stop()
    except KeyboardInterrupt:
        pass
    finally:
        canbus.close_bus(bus)


if __name__ == "__main__":
    main()
