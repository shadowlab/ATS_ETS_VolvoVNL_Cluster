"""Check the CANable and the PC side, with or without the cluster.

    python can_selftest.py --interface gs_usb --channel 0
    python can_selftest.py --interface slcan --channel COM5

1. Open: finds the adapter and sets 250k (driver, libusb, firmware, timing).
2. Loopback (candleLight only): the CANable sends frames to itself with
   nothing wired, and each must come back intact. Proves USB both ways and
   the CAN controller.
3. Bus: listens for two seconds, then sends one Request for Address Claimed
   from F9. With nothing connected nobody can ACK it, and the CANable should
   report "no ACK"; that's a pass, it shows the transmitter is driving. With
   the cluster powered on CAN1, traffic and no errors prove the wiring,
   termination and bitrate.

Exit code 0 when nothing failed.
"""

import argparse
import time
from collections import Counter

import can

import canbus
import j1939

LOOPBACK_FRAMES = 50
LISTEN_SECONDS = 2.0


class Report:
    def __init__(self):
        self.failed = False

    def line(self, status, text):
        print(f"[{status:4}] {text}")
        if status == "FAIL":
            self.failed = True


def drain(bus, seconds):
    """Every message that arrives within the time, errors included."""
    out = []
    end = time.time() + seconds
    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if msg is not None:
            out.append(msg)
    return out


def loopback_frames():
    return [can.Message(arbitration_id=j1939.make_id(0xF004, 0x00, priority=3),
                        data=bytes([i, 0x5A, 0xA5, i ^ 0xFF, 0x00, 0xFF, i, 0x55]), is_extended_id=True)
            for i in range(LOOPBACK_FRAMES)]


def check_loopback(bus, report):
    if not canbus.set_gs_usb_mode(bus, loopback=True):
        report.line("WARN", "Loopback: the firmware doesn't offer loopback mode, skipped")
        return
    sent = loopback_frames()
    start = time.time()
    for msg in sent:
        bus.send(msg)
    got = drain(bus, 1.0)
    # Only our ID: frames from the bus can still be on their way in from before the switch.
    looped = [m for m in got if m.is_rx and not m.is_error_frame and m.arbitration_id == sent[0].arbitration_id]
    want = [(m.arbitration_id, bytes(m.data)) for m in sent]
    have = [(m.arbitration_id, bytes(m.data)) for m in looped]
    if have == want:
        report.line("PASS", f"Loopback: {len(sent)} frames out and back intact ({(time.time() - start) * 1000:.0f} ms)")
    elif not looped and any(not m.is_rx for m in got):
        report.line("FAIL", "Loopback: the CANable took the frames (echoes came back) but none looped back")
    else:
        missing = len([w for w in want if w not in have])
        report.line("FAIL", f"Loopback: sent {len(sent)}, got {len(looped)} back, {missing} missing or changed")
    for m in got:
        if m.is_error_frame:
            report.line("INFO", f"  error frame in loopback: {canbus.describe_error(m)}")


def check_bus(bus, args, report):
    if args.interface == "gs_usb":
        canbus.set_gs_usb_mode(bus)
    heard = [m for m in drain(bus, LISTEN_SECONDS) if m.is_rx]
    frames = [m for m in heard if not m.is_error_frame]
    if frames:
        sources = Counter(j1939.parse_id(m.arbitration_id)[2] for m in frames if m.is_extended_id)
        report.line("PASS", f"Bus: heard {len(frames)} frames in {LISTEN_SECONDS:.0f} s from source(s) "
                    + ", ".join(f"{sa:02X} ({n})" for sa, n in sorted(sources.items())))
    else:
        report.line("INFO", f"Bus: silent for {LISTEN_SECONDS:.0f} s (expected with the cluster unplugged or off)")

    poke = can.Message(arbitration_id=j1939.make_id(0xEA00, 0xF9), data=[0x00, 0xEE, 0x00], is_extended_id=True)
    bus.send(poke)
    after = drain(bus, 1.0)
    if args.interface == "gs_usb":
        # Stop the controller retrying an unanswered frame forever.
        canbus.set_gs_usb_mode(bus)
    errors = [m for m in heard + after if m.is_error_frame]
    answers = [m for m in after if m.is_rx and not m.is_error_frame and j1939.parse_id(m.arbitration_id)[1] == 0xEE00]
    no_ack = any("no ACK" in canbus.error_classes(m) or "TX passive" in canbus.describe_error(m) for m in errors)

    if frames and not errors:
        report.line("PASS", "Bus: traffic with no error frames, so wiring, termination and bitrate look right")
    elif frames:
        report.line("FAIL", f"Bus: traffic but {len(errors)} error frame(s); check termination (about 60 ohm "
                    "across H/L, power off), H/L swapped, or bitrate")
    elif no_ack:
        report.line("PASS", "Bus: the test frame went out and nobody ACKed it, as expected with nothing "
                    "connected. With the cluster on, this means it isn't on the bus yet")
    elif errors:
        report.line("WARN", "Bus: error frames with a silent bus; check the wiring when the cluster is on")
    elif args.interface == "gs_usb":
        report.line("WARN", "Bus: sent the test frame but the firmware reported nothing back, so the "
                    "ACK can't be checked from here")
    else:
        report.line("INFO", f"Bus: sent the test frame; {args.interface} doesn't report errors, so the ACK can't be checked")
    for m in errors[:5]:
        report.line("INFO", f"  {canbus.describe_error(m)}")
    for m in answers:
        report.line("INFO", f"  claim: {j1939.describe(m.arbitration_id, bytes(m.data))}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    canbus.add_bus_args(ap)
    ap.add_argument("--skip-bus", action="store_true", help="don't send anything in normal mode")
    args = ap.parse_args()
    report = Report()

    try:
        bus = canbus.open_bus(args)
    except Exception as e:  # noqa: BLE001 - any failure here is the answer
        report.line("FAIL", f"Open: {type(e).__name__}: {e}")
        return 1
    report.line("PASS", f"Open: {args.interface} {args.channel} at {args.bitrate} bit/s")
    try:
        if args.interface == "gs_usb":
            check_loopback(bus, report)
        else:
            report.line("INFO", f"Loopback: not available on {args.interface}, skipped")
        if not args.skip_bus:
            check_bus(bus, args, report)
    finally:
        canbus.close_bus(bus)

    print("\nAll good." if not report.failed else "\nSomething failed, see above.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
