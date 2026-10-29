"""Check the CANable and the PC side, with or without the cluster.

    python can_selftest.py --interface gs_usb --channel 0
    python can_selftest.py --interface slcan --channel COM5

1. Open: finds the adapter and sets 250k (driver, libusb, firmware, timing).
2. Loopback (candleLight only): the CANable sends frames to itself with
   nothing wired, and each must come back intact. Proves USB both ways and
   the CAN controller. The CANable 2.0's candleLight build lists loopback
   but doesn't do it; there only the echoes come back, which still proves
   USB and the firmware.
3. Bus: listens for two seconds, then sends one Request for Address Claimed
   from F9. With nothing connected nobody can ACK it, and the CANable should
   report "no ACK"; that's a pass, it shows the transmitter is driving (the
   CANable 2.0's candleLight build reports no bus errors, so it can't tell).
   With the cluster powered on CAN1, traffic and an answer to the request
   prove the wiring, termination and bitrate.
4. ACK (candleLight only): sends a burst of the same request and counts the
   echoes. candleLight echoes a frame once the controller takes it, and the
   controller only holds a few frames it can't get ACKed. So every echo back
   means some node on the bus is ACKing, which a silent cluster still does,
   and only a few back means nobody is. That works even on the CANable 2.0,
   which reports no bus errors. Run it once with CAN H/L unplugged to see the
   "nobody" count on your adapter.

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
ACK_FRAMES = 12  # well above the controller's TX buffer, well below the firmware's frame pool


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
        # candleLight for the CANable 2.0 (STM32G4) lists loopback but its
        # driver always starts the controller in normal mode.
        report.line("WARN", "Loopback: the firmware took the frames (echoes came back) but didn't "
                    "loop them back. The CANable 2.0's candleLight build doesn't do loopback, so USB and "
                    "firmware are fine; the CAN side needs the cluster to test")
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

    if frames and errors:
        report.line("FAIL", f"Bus: traffic but {len(errors)} error frame(s); check termination (about 60 ohm "
                    "across H/L, power off), H/L swapped, or bitrate")
    elif frames and answers:
        report.line("PASS", "Bus: the cluster answered our request, so wiring, termination and bitrate "
                    "work both ways")
    elif frames:
        report.line("PASS", "Bus: receiving works. Nothing answered the Address Claimed request, so sending "
                    "isn't proven yet; try can_listen.py --poke")
    elif no_ack:
        report.line("PASS", "Bus: the test frame went out and nobody ACKed it, as expected with nothing "
                    "connected. With the cluster on, this means it isn't on the bus yet")
    elif errors:
        report.line("WARN", "Bus: error frames with a silent bus; check the wiring when the cluster is on")
    elif args.interface == "gs_usb":
        report.line("INFO", "Bus: sent the test frame, but the firmware doesn't report bus errors (the "
                    "CANable 2.0's candleLight build doesn't), so the ACK can't be checked without the cluster")
    else:
        report.line("INFO", f"Bus: sent the test frame; {args.interface} doesn't report errors, so the ACK can't be checked")
    for m in errors[:5]:
        report.line("INFO", f"  {canbus.describe_error(m)}")
    for m in answers:
        report.line("INFO", f"  claim: {j1939.describe(m.arbitration_id, bytes(m.data))}")


def check_ack(bus, report):
    """Count echoes of a burst: all back means a node ACKs, a few means none does."""
    canbus.set_gs_usb_mode(bus)
    drain(bus, 0.2)
    poke = can.Message(arbitration_id=j1939.make_id(0xEA00, 0xF9), data=[0x00, 0xEE, 0x00], is_extended_id=True)
    for _ in range(ACK_FRAMES):
        bus.send(poke)
        time.sleep(0.005)
    got = drain(bus, 1.0)
    echoes = sum(1 for m in got if not m.is_rx and m.arbitration_id == poke.arbitration_id)
    # Stop the controller retrying unanswered frames forever.
    canbus.set_gs_usb_mode(bus)
    if echoes >= ACK_FRAMES:
        report.line("PASS", f"ACK: all {ACK_FRAMES} test frames went out, so something on CAN1 is ACKing. "
                    "With only the cluster on the bus, it's connected, powered and at the right bitrate")
    elif echoes:
        report.line("WARN", f"ACK: only {echoes} of {ACK_FRAMES} test frames went out, so nobody ACKed. "
                    "Expected with nothing connected; with the cluster on, check it's powered (A:1, A:3), "
                    "H/L not swapped, the 60-70 ohm across H/L, and the bitrate")
    else:
        report.line("FAIL", "ACK: no echoes at all; the firmware didn't take the frames")


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
            if args.interface == "gs_usb":
                check_ack(bus, report)
    finally:
        canbus.close_bus(bus)

    print("\nAll good." if not report.failed else "\nSomething failed, see above.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
