"""Play a simulated truck to the cluster: key-on, then engine and VECU heartbeats.

    python can_wake.py --interface gs_usb --channel 0 --vecu 27,21,31 --dwell 30 --rpm 1000 --kmh 50

Starts with the key off (nothing sent) for --off seconds, then sends all the
frames below together, at their usual J1939 rates. With several --vecu
addresses it tries each for --dwell seconds and loops, so one run with
j1708_listen.py logging alongside shows which address clears the cluster's
SID 201 (VECU lost). --vecu sweep tries every address, 16 at a time
(--group), so a hit narrows the search to one group; then try that group's
range in halves, e.g. --vecu 41-48,49-50. Only one program can open the CANable, which is why this
sends everything from one place, in one loop that also reads (and so drains)
the adapter. A status line every --status seconds shows frames sent, send
errors and frames heard from the cluster. Ctrl+C stops.

Which frames the cluster needs is not known yet; this is a test, see
docs/messages.md.
"""

import argparse
import threading
import time

import can

import can_listen
import can_send
import canbus
import j1939
from can_send import build_message

CLUSTER_SA = 0x17


# Never sweep the engine (00, sent separately), the cluster itself, or the null/global addresses.
SKIP_SA = {0x00, CLUSTER_SA, 0xFE, 0xFF}


def parse_vecu(text, group=16):
    """--vecu into a list of turns, each a list of source addresses sent together.

    "27,21,31" is three turns of one address. "01-3F" is one turn with every
    address in that range. "sweep" is 01-FD in groups of `group`.
    """
    turns = []
    for item in text.replace(" ", "").split(","):
        if item.lower() == "sweep":
            pool = [sa for sa in range(0x01, 0xFE) if sa not in SKIP_SA]
            turns += [pool[i:i + group] for i in range(0, len(pool), group)]
        elif "-" in item:
            lo, hi = (int(x, 16) for x in item.split("-"))
            turns.append([sa for sa in range(lo, hi + 1) if sa not in SKIP_SA])
        else:
            turns.append([int(item, 16)])
    if not all(turns):
        raise ValueError(f"no usable address in --vecu {text!r}")
    return turns


def parse_extra(text):
    """--extra "PGN:SA:DATA" into frames. SA may be a range, "00-FD", to send the
    same frame from every address in it at once (skipping the cluster's own)."""
    try:
        pgn, sa, data = text.split(":", 2)
        if "-" in sa:
            lo, hi = (int(x, 16) for x in sa.split("-"))
            sas = [a for a in range(lo, hi + 1) if a not in (CLUSTER_SA, 0xFE, 0xFF)]
        else:
            sas = [int(sa, 16)]
        payload = can_send.parse_hex(data)
        return [can_send.build_message(int(pgn, 16), a, payload) for a in sas]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected PGN:SA:DATA in hex (SA may be a range like 00-FD), got {text!r}")


def describe_turn(sas):
    if len(sas) == 1:
        return f"SA 0x{sas[0]:02X}"
    return f"{len(sas)} SAs 0x{sas[0]:02X}-0x{sas[-1]:02X}"


def heartbeats(vecu, rpm, kmh, volts, park=False):
    """(name, period in s, message) for every frame of one simulated truck.

    park sets the parking brake in CCVS1 (SPN 70 = 01); otherwise it is released.

    vecu is one source address or a list of them; the VECU frames go out from each.
    """
    vecus = [vecu] if isinstance(vecu, int) else list(vecu)
    rpm_raw = round(rpm / 0.125)
    speed_raw = round(kmh * 256)
    volts_raw = round(volts / 0.05)
    v = volts_raw.to_bytes(2, "little")
    tag = (lambda sa: "") if len(vecus) == 1 else (lambda sa: f" {sa:02X}")
    vecu_frames = []
    for sa in vecus:
        vecu_frames += [
            # VEP1: battery potential (SPN 168) and keyswitch potential (SPN 158).
            ("VEP1" + tag(sa), 0.1, build_message(0xFEF7, sa, bytes([0xFF] * 4) + v + v)),
            # CCVS1: parking brake (SPN 70, byte 1 bits 4-3: 00 released, 01 set), wheel-based speed.
            ("CCVS1" + tag(sa), 0.1, build_message(0xFEF1, sa, bytes([0xF7 if park else 0xF3])
                                                   + speed_raw.to_bytes(2, "little"))),
        ]
    return vecu_frames + [
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


class Scheduler:
    """Sends a set of periodic frames from one loop, with no background threads.

    Swapping the frame set (a new VECU address) keeps each frame's timing, so
    the engine frames never pause.
    """

    def __init__(self, frames, now):
        self.entries = []
        self.swap(frames, now)

    def swap(self, frames, now):
        due = {name: next_time for name, next_time, _, _ in self.entries}
        self.entries = [[name, due.get(name, now), period, msg] for name, period, msg in frames]

    def due(self, now):
        """Frames to send now. A frame that fell behind skips ahead instead of bursting."""
        out = []
        for entry in self.entries:
            if now >= entry[1]:
                out.append(entry[3])
                entry[1] += entry[2]
                if entry[1] <= now:
                    entry[1] = now + entry[2]
        return out

    def wait(self, now):
        return max(0.0, min(entry[1] for entry in self.entries) - now)


class CanSender:
    """Owns all CAN traffic for a program that has other work to do (like sweep_watch).

    A background thread sends a named set of periodic frames through a
    Scheduler and reads (drains) the adapter between sends. Frames can be added
    and removed while it runs.
    """

    def __init__(self, bus, frames=(), on_rx=None):
        self.bus = bus
        self.on_rx = on_rx
        self.frames = {name: (period, msg) for name, period, msg in frames}
        self.lock = threading.Lock()
        self.sched = Scheduler(self._list(), time.monotonic())
        self.sent = self.errors = self.heard = 0
        self.last_error = ""
        self.oneshot = []
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _list(self):
        return [(name, period, msg) for name, (period, msg) in self.frames.items()]

    def set(self, name, period, msg):
        with self.lock:
            self.frames.pop(name, None)
            self.sched.swap(self._list(), time.monotonic())   # forget the old timing: send the new frame now
            self.frames[name] = (period, msg)
            self.sched.swap(self._list(), time.monotonic())

    def send_once(self, msg):
        with self.lock:
            self.oneshot.append(msg)

    def remove(self, name):
        with self.lock:
            if self.frames.pop(name, None) is not None:
                self.sched.swap(self._list(), time.monotonic())

    def _run(self):
        while self.running:
            with self.lock:
                now = time.monotonic()
                due = self.oneshot + self.sched.due(now)
                self.oneshot = []
                wait = self.sched.wait(now) if self.sched.entries else 0.05
            for msg in due:
                try:
                    self.bus.send(msg)
                    self.sent += 1
                except can.CanError as exc:
                    self.errors += 1
                    self.last_error = str(exc)
            try:
                msg = self.bus.recv(timeout=max(0.001, min(wait, 0.05)))
                for _ in range(200):
                    if msg is None:
                        break
                    if msg.is_rx and not msg.is_error_frame:
                        if msg.arbitration_id & 0xFF == CLUSTER_SA:
                            self.heard += 1
                        if self.on_rx:
                            self.on_rx(msg)
                    msg = self.bus.recv(timeout=0.001)
            except Exception:
                if not self.running:
                    break
                raise

    def stats(self):
        """(sent, send errors, cluster frames heard, last error) since the last call."""
        out = (self.sent, self.errors, self.heard, self.last_error)
        self.sent = self.errors = self.heard = 0
        self.last_error = ""
        return out

    def close(self):
        self.running = False
        self.thread.join(timeout=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    canbus.add_bus_args(ap)
    ap.add_argument("--vecu", default="27",
                    help="VECU source addresses in hex, comma separated, tried in turn (default 27). "
                         "A range like 01-0F sends from all of them at once; 'sweep' covers 01-FD in groups")
    ap.add_argument("--group", type=int, default=16, help="addresses per turn for --vecu sweep (default 16)")
    ap.add_argument("--dwell", type=float, default=30, help="seconds per VECU address when several are given")
    ap.add_argument("--off", type=float, default=2, help="seconds of silence before key-on")
    ap.add_argument("--rpm", type=float, default=0)
    ap.add_argument("--kmh", type=float, default=0)
    ap.add_argument("--volts", type=float, default=13.8)
    ap.add_argument("--park", action="store_true", help="parking brake set in CCVS1 (default released)")
    ap.add_argument("--extra", action="append", default=[], metavar="PGN:SA:DATA",
                    help="also send this frame (hex, as in can_listen --send) every --extra-period s; repeatable. "
                         "SA may be a range, e.g. FE56:00-FD:7D, to send it from every address at once")
    ap.add_argument("--extra-period", type=float, default=0.1, help="seconds between --extra frames (default 0.1)")
    ap.add_argument("--status", type=float, default=5, help="seconds between status lines (default 5)")
    args = ap.parse_args()
    try:
        turns = parse_vecu(args.vecu, args.group)
        extras = [(f"extra {i + 1}", args.extra_period, msg)
                  for i, text in enumerate(args.extra) for msg in parse_extra(text)]
    except (ValueError, argparse.ArgumentTypeError) as exc:
        ap.error(str(exc))
    if len(turns) > 1:
        print(f"{len(turns)} turns of {args.dwell:g} s, about {len(turns) * args.dwell / 60:.0f} min per pass")

    bus = canbus.open_bus(args)
    sent = errors = heard = 0
    last_error = ""
    try:
        time.sleep(args.off)
        now = time.monotonic()
        sched = None
        turn = -1
        next_switch = now
        next_status = now + args.status
        while True:
            now = time.monotonic()
            if now >= next_switch:
                turn += 1
                vecus = turns[turn % len(turns)]
                frames = heartbeats(vecus, args.rpm, args.kmh, args.volts, park=args.park) + extras
                print(time.strftime("%H:%M:%S"), f"turn {turn % len(turns) + 1}/{len(turns)}: VECU at {describe_turn(vecus)}")
                shown = set()
                for name, period, msg in frames:
                    if len(vecus) > 1 and name[:4] in ("VEP1", "CCVS"):
                        continue
                    if name.startswith("extra") and name in shown:
                        continue
                    shown.add(name)
                    count = sum(1 for other, _, _ in frames if other == name)
                    if count > 1:
                        print(f"  {name:7} every {period * 1000:g} ms  from {count} SAs  {bytes(msg.data).hex(' ')}"
                              f"  {j1939.describe(msg.arbitration_id, bytes(msg.data))}")
                        continue
                    print(f"  {name:7} every {period * 1000:g} ms  {msg.arbitration_id:08X}  {bytes(msg.data).hex(' ')}"
                          f"  {j1939.describe(msg.arbitration_id, bytes(msg.data))}")
                if sched is None:
                    sched = Scheduler(frames, now)
                else:
                    sched.swap(frames, now)
                if len(vecus) > 1:
                    print(f"  VEP1 and CCVS1 every 100 ms from each of {' '.join(f'{sa:02X}' for sa in vecus)}")
                next_switch = now + args.dwell if len(turns) > 1 else float("inf")
            for msg in sched.due(now):
                try:
                    bus.send(msg)
                    sent += 1
                except can.CanError as exc:
                    errors += 1
                    last_error = str(exc)
            if now >= next_status:
                print(time.strftime("%H:%M:%S"), f"  sent {sent}, send errors {errors}, cluster frames heard {heard}"
                      + (f"  (last error: {last_error})" if last_error else ""))
                if heard == 0:
                    print("  WARNING: nothing heard from the cluster; is it powered? Replug the CANable if this stays")
                sent = errors = heard = 0
                last_error = ""
                next_status = now + args.status
            # Read everything waiting, including the adapter's echoes of our own frames:
            # a candleLight adapter whose echoes are never read runs out of buffers and stops sending.
            # Reading also does the waiting until the next frame is due.
            msg = bus.recv(timeout=max(0.001, min(sched.wait(time.monotonic()), 0.05)))
            for _ in range(200):
                if msg is None:
                    break
                if msg.is_rx and not msg.is_error_frame and msg.arbitration_id & 0xFF == CLUSTER_SA:
                    heard += 1
                msg = bus.recv(timeout=0.001)
    except KeyboardInterrupt:
        pass
    finally:
        canbus.close_bus(bus)


if __name__ == "__main__":
    main()
