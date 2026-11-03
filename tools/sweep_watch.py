"""Sweep J1939 PGNs or J1587 PIDs while a camera watches the cluster, and log what changes.

Each candidate (source address, PGN, payload; or MID, PID, data) is sent for --dwell seconds,
then watched for --settle seconds more. If the picture differs from the idle
baseline, the script saves before and after pictures and logs the hit, waits
for the cluster to return to idle, and moves on. Hits go to a run folder:

    sweeps/<run>/hits.csv       one row per hit
    sweeps/<run>/tried.csv      every candidate, so a stopped run can resume
    sweeps/<run>/review.html    hits with pictures, to confirm or ignore
    sweeps/<run>/img/           before and after pictures

First, with the cluster powered and idle, mark areas that change on their own
(clock, blinking icons) so they're ignored. Drag a box around each one, press
Enter after each, then Esc when done:

    python sweep_watch.py pick --camera 0

Check the detector with a live view, then sweep the Volvo proprietary range:

    python sweep_watch.py preview --camera 0
    python sweep_watch.py sweep --interface gs_usb --channel 0 --camera 0 --pgns FF00-FFFF --sources 00,03,0B,21,31,17 --payloads 00,FF

Narrow a hit down bit by bit (64 candidates, each with one bit flipped from
the first payload):

    python sweep_watch.py sweep --interface gs_usb --channel 0 --camera 0 --pgns FF23 --sources 21 --payloads 00 --bits

--transport j1708 sweeps J1587 instead: each MID (--mids) sends each PID
(--pids) filled with each byte (--fills), through the ESP32 bridge on --port.
The data is one byte below PID 128, two bytes up to 191, and a length of 2
plus two bytes above that. PIDs 254 and 255 are skipped.

    python sweep_watch.py sweep --transport j1708 --port COM11 --camera 0 --mids 80,90 --pids 00-FD --fills 00,FF

--camera is a camera number (DroidCam over USB shows up as one) or a stream
address. Run the same command with the same --out folder to resume.
"""

import argparse
import csv
import html
import json
import os
import threading
import time
from datetime import datetime

import can
import cv2
import numpy as np

import can_wake
import canbus
import j1587
import j1939

HIT_FIELDS = ["hit", "time", "sa", "pgn", "name", "payload", "change", "latched", "before", "after", "bus"]
TRIED_FIELDS = ["time", "sa", "pgn", "payload", "change", "hit", "bus"]


def parse_hex_list(text):
    """'FF00-FF03,FEF1' -> [0xFF00, 0xFF01, 0xFF02, 0xFF03, 0xFEF1]"""
    values = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x, 16) for x in part.split("-"))
            values.extend(range(lo, hi + 1))
        else:
            values.append(int(part, 16))
    return values


def parse_payloads(text):
    """'00,FF' -> eight-byte fills; a 16-digit hex string is taken as-is."""
    payloads = []
    for part in text.split(","):
        part = part.replace(" ", "")
        if len(part) == 2:
            payloads.append(bytes([int(part, 16)]) * 8)
        elif len(part) == 16:
            payloads.append(bytes.fromhex(part))
        else:
            raise ValueError(f"payload {part!r} must be one byte or eight bytes of hex")
    return payloads


def bit_payloads(base):
    """64 payloads, each with one bit of base flipped (byte 0 bit 0 first)."""
    out = []
    for bit in range(64):
        b = bytearray(base)
        b[bit // 8] ^= 1 << (bit % 8)
        out.append(bytes(b))
    return out


def candidates(pgns, sources, payloads, bits=False):
    if bits:
        payloads = bit_payloads(payloads[0])
    return [(sa, pgn, p) for sa in sources for pgn in pgns for p in payloads]


def key(sa, pgn, payload):
    return f"{sa:02X}:{pgn:05X}:{payload.hex()}"


SKIPPED_PIDS = (254, 255)  # data link escape and page extension: not plain parameters


def pid_data(pid, fill):
    """Data bytes after the PID: 1 below 128, 2 up to 191, then length 2 and 2 bytes."""
    if pid < 128:
        return bytes([fill])
    if pid < 192:
        return bytes([fill]) * 2
    return bytes([2, fill, fill])


def j1708_candidates(mids, pids, fills):
    """(MID, PID, data) for every combination, in the same shape as CAN candidates."""
    return [(mid, pid, pid_data(pid, fill)) for mid in mids for pid in pids if pid not in SKIPPED_PIDS
            for fill in fills]


class CanTransport:
    """Candidates are (source address, PGN, payload), sent periodically on CAN.

    All CAN traffic goes through one can_wake.CanSender thread, which also
    sends any background frames and drains the adapter (a gs_usb CANable that
    is never read stops sending after a while).
    """
    labels = ("SA", "PGN")

    def __init__(self, bus, priority=6, sender=None):
        self.priority = priority
        self.owns_sender = sender is None
        self.sender = sender or can_wake.CanSender(bus)

    def start(self, sa, pgn, payload, period):
        msg = can.Message(arbitration_id=j1939.make_id(pgn, sa, priority=self.priority), data=payload,
                          is_extended_id=True)
        self.sender.set("candidate", period, msg)
        return _Stop(lambda: self.sender.remove("candidate"))

    def name(self, pgn):
        return j1939.pgn_name(pgn)

    def close(self):
        if self.owns_sender:
            self.sender.close()


class _Stop:
    def __init__(self, fn):
        self.stop = fn


class PeriodicWriter:
    """Writes one frame to a serial port now and then every period, until stopped."""

    def __init__(self, port, frame, period):
        self.port = port
        self.frame = frame
        self.period = period
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            self.port.write(self.frame)
            self.stopped.wait(self.period)

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=1)


class J1708Transport:
    """Candidates are (MID, PID, data); each is one J1587 frame written to the bridge's serial port."""
    labels = ("MID", "PID")

    def __init__(self, port):
        self.port = port

    def start(self, mid, pid, data, period):
        return PeriodicWriter(self.port, j1587.make_frame(mid, bytes([pid]) + data), period)

    def name(self, pid):
        return j1587.PID_NAMES.get(pid, "")


BACKENDS = {
    "auto": cv2.CAP_ANY,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "v4l2": cv2.CAP_V4L2,
    "ffmpeg": cv2.CAP_FFMPEG,
}


def open_capture(source, width=None, height=None, backend="auto"):
    if str(source).isdigit():
        cap = cv2.VideoCapture(int(source), BACKENDS[backend])
    else:
        cap = cv2.VideoCapture(source)
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        raise SystemExit(f"Can't open camera {source}")
    return cap


def warm_up(cap, seconds=2.0):
    """Read and drop frames for a while; many cameras start with blank or
    placeholder frames. Returns the last good frame, or None."""
    frame = None
    end = time.time() + seconds
    while time.time() < end:
        ok, f = cap.read()
        if ok:
            frame = f
        else:
            time.sleep(0.05)
    return frame


ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rotate(frame, degrees):
    """Turn a frame clockwise by 0, 90, 180 or 270 degrees."""
    code = ROTATIONS[degrees]
    return frame if code is None else cv2.rotate(frame, code)


def looks_blank(frame):
    """True for a picture that's nearly one solid color, like a placeholder."""
    return float(frame.std()) < 8


class Camera:
    """Reads frames on a thread so frame() always returns a fresh picture."""

    def __init__(self, source, width=None, height=None, backend="auto", warmup=2.0, rotation=0):
        self.cap = open_capture(source, width, height, backend)
        self.rotation = rotation
        self.lock = threading.Condition()
        self.latest = None
        self.count = 0
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        time.sleep(warmup)

    def _run(self):
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame = rotate(frame, self.rotation)
            with self.lock:
                self.latest = frame
                self.count += 1
                self.lock.notify_all()

    def frame(self, timeout=2.0):
        """The next frame captured after this call."""
        with self.lock:
            seen = self.count
            if not self.lock.wait_for(lambda: self.count > seen, timeout):
                raise RuntimeError("camera stopped sending frames")
            return self.latest.copy()

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()


class ChangeDetector:
    """Compares frames against an idle baseline, ignoring masked areas.

    A pixel counts as changed when its brightness moves by more than
    pixel_threshold (0-255); a frame counts as changed when more than
    min_area of the watched pixels changed.
    """

    def __init__(self, ignore=(), pixel_threshold=30, min_area=0.002, blur=5):
        self.ignore = [tuple(r) for r in ignore]
        self.pixel_threshold = pixel_threshold
        self.min_area = min_area
        self.blur = blur | 1
        self.baseline = None
        self.watch = None

    def prep(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        return cv2.GaussianBlur(gray, (self.blur, self.blur), 0).astype(np.float32)

    def set_baseline(self, frames):
        self.baseline = np.median(np.stack([self.prep(f) for f in frames]), axis=0).astype(np.float32)
        self.watch = np.ones(self.baseline.shape, bool)
        pad = self.blur // 2 + 1  # blur spreads a masked change a little past its box
        for x, y, w, h in self.ignore:
            self.watch[max(y - pad, 0):y + h + pad, max(x - pad, 0):x + w + pad] = False

    def adapt(self, frame, rate=0.05):
        """Follow slow lighting drift using a frame known to be idle."""
        cv2.accumulateWeighted(self.prep(frame), self.baseline, rate)

    def diff(self, frame):
        """(fraction of watched pixels changed, changed-pixel mask)."""
        mask = (np.abs(self.prep(frame) - self.baseline) > self.pixel_threshold) & self.watch
        return mask.sum() / max(self.watch.sum(), 1), mask

    def changed(self, frame):
        return self.diff(frame)[0] >= self.min_area


def boxes(mask, min_size=4):
    m = cv2.dilate(mask.astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [r for r in (cv2.boundingRect(c) for c in contours) if r[2] >= min_size and r[3] >= min_size]


def annotate(frame, mask, ignore=()):
    out = frame.copy()
    for x, y, w, h in ignore:
        cv2.rectangle(out, (x, y), (x + w, y + h), (128, 128, 128), 1)
    for x, y, w, h in boxes(mask):
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 2)
    return out


def rebaseline(camera, detector, n=5):
    detector.set_baseline([camera.frame() for _ in range(n)])


def wait_idle(camera, detector, timeout):
    """Wait for the picture to match the baseline. True if it did."""
    end = time.time() + timeout
    while time.time() < end:
        if not detector.changed(camera.frame()):
            return True
    return False


def append_row(path, fields, row):
    new = not os.path.exists(path)
    if not new:
        with open(path, newline="") as f:
            fields = next(csv.reader(f), None) or fields   # a run folder from an older version keeps its columns
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class BusWatch:
    """Notices when the cluster's own output changes, as a second way to spot a hit.

    Feed it every frame the cluster sends (CAN from SA 17 through can_wake's
    CanSender, J1708 through J1708Reader). Each output is keyed by what it is
    (a PGN, or a MID/PID pair) and remembered by its latest data. learn() finds
    the outputs that change by themselves (clock, counters) and ignores them.
    """

    def __init__(self, noisy_after=3):
        self.lock = threading.Lock()
        self.latest = {}
        self.window = None          # outputs as they were at mark()
        self.window_changes = {}    # first value that differed from the mark, per output
        self.noisy = set()
        self.trigger_counts = {}
        self.noisy_after = noisy_after

    def observe(self, key, data):
        data = bytes(data)
        with self.lock:
            if self.window is not None and key not in self.window_changes and self.window.get(key) != data:
                self.window_changes[key] = data
            self.latest[key] = data

    def observe_can(self, msg):
        _, pgn, sa, _ = j1939.parse_id(msg.arbitration_id)
        if sa == can_wake.CLUSTER_SA:
            self.observe(("CAN", pgn), msg.data)

    def observe_j1708(self, frame):
        mid, body = frame[0], frame[1:-1]
        if not body:
            return
        pid = body[0]
        # PID 254 frames are addressed: key them by destination. PID 192 multisection frames by section.
        sub = None
        if pid == 254 and len(body) > 1:
            sub = body[1]
        elif pid == 192 and len(body) > 2:
            sub = body[2]
        self.observe(("J1708", mid, pid, sub), body)

    def forget(self, key):
        with self.lock:
            self.latest.pop(key, None)

    def has_j1708(self):
        with self.lock:
            return any(k[0] == "J1708" for k in self.latest)

    def wait_for(self, key, timeout):
        """Wait until `key` changes from its value at mark() (or appears). True if it did."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if key in self.window_changes:
                    return True
            time.sleep(0.1)
        return False

    def mark(self):
        """Start a window: changes() then lists every output that differed at any point since."""
        with self.lock:
            self.window = dict(self.latest)
            self.window_changes = {}
            return self.window

    def changes(self, snapshot=None):
        """Outputs that changed (or appeared) since mark(), leaving out the noisy ones."""
        with self.lock:
            return sorted((k for k in self.window_changes if k not in self.noisy), key=str)

    def learn(self, seconds, log=print):
        """Watch for `seconds` with nothing extra sent; whatever changes is noise."""
        end = time.time() + seconds
        before = self.mark()
        while time.time() < end:
            time.sleep(0.1)
            with self.lock:
                # changed after we first saw it (a key that only just appeared isn't noise yet)
                self.noisy |= {k for k in self.window_changes if k in before}
            before = self.mark()
        log(f"bus watch: {len(self.latest)} cluster outputs seen, ignoring {len(self.noisy)} that change by themselves"
            + (f": {', '.join(describe_key(k) for k in sorted(self.noisy, key=str))}" if self.noisy else ""))

    def blame(self, keys, log=print):
        """Count hits per output; one that keeps triggering is noise after all."""
        for k in keys:
            self.trigger_counts[k] = self.trigger_counts.get(k, 0) + 1
            if self.trigger_counts[k] >= self.noisy_after and k not in self.noisy:
                with self.lock:
                    self.noisy.add(k)
                log(f"  bus watch: {describe_key(k)} keeps changing, ignoring it from now on")


def describe_key(k):
    if k[0] == "CAN":
        name = j1939.pgn_name(k[1])
        return f"CAN PGN {k[1]:05X}" + (f" {name}" if name else "")
    _, mid, pid, sub = k
    return f"J1708 MID {mid} PID {pid}" + (f"/{sub}" if sub is not None else "")


def describe_changes(watch, keys, snapshot):
    parts = []
    for k in keys:
        old = snapshot.get(k)
        new = watch.window_changes.get(k, b"")
        parts.append(f"{describe_key(k)}: {old.hex(' ') if old is not None else 'new'} -> {new.hex(' ')}")
    return "; ".join(parts)


class J1708Reader:
    """Reads the cluster's J1708 output from the ESP32 bridge into a BusWatch."""

    def __init__(self, port, watch, gap=0.004):
        self.port = port
        self.watch = watch
        self.gap = gap
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        chunk = bytearray()
        last = time.time()
        while self.running:
            data = self.port.read(256)
            if data:
                chunk += data
                last = time.time()
                continue
            if chunk and time.time() - last >= self.gap:
                for frame in j1587.split_frames(bytes(chunk)) or []:
                    self.watch.observe_j1708(frame)
                chunk.clear()
            time.sleep(0.001)

    def close(self):
        self.running = False
        self.thread.join(timeout=1)


class NullCamera:
    """Stands in for the camera when a sweep relies on the bus watch alone."""

    def frame(self, timeout=2.0):
        time.sleep(0.05)
        return np.zeros((8, 8, 3), np.uint8)

    def close(self):
        pass


FAULT_REPORT = ("J1708", 140, 254, 127)   # the cluster's PID 254 fault report to MID 127, about every 15 s
ENGINE_FRAMES = ("EEC1", "ET1", "EFL/P1", "DM1")


def bus_check(sender, watch, wake, wake_vecu=0x27, wake_rpm=1000, log=print, fault_timeout=35):
    """Known cluster reactions the bus watch must see. True if all it could test passed."""
    ok = True
    claim = ("CAN", 0xEE00)
    watch.forget(claim)
    watch.mark()
    sender.send_once(can.Message(arbitration_id=j1939.make_id(0xEA00, 0xF9), data=[0x00, 0xEE, 0x00],
                                 is_extended_id=True))
    if watch.wait_for(claim, 2):
        log("CHECK: bus watch saw the cluster answer a request for its address claim (CAN path works)")
    else:
        ok = False
        log("CHECK: the cluster did NOT answer a request for its address claim on CAN")

    if not watch.has_j1708():
        log("CHECK: no J1708 heard, skipping the fault test (add --j1708-port COM11 to watch J1708)")
        return ok
    if not wake:
        log("CHECK: skipping the fault test (it needs --wake)")
        return ok
    log(f"CHECK: pausing the engine frames until the cluster reports SID 200 (up to {fault_timeout} s)...")
    watch.mark()
    for name in ENGINE_FRAMES:
        sender.remove(name)
    seen = watch.wait_for(FAULT_REPORT, fault_timeout)
    for name, period, msg in can_wake.heartbeats(wake_vecu, wake_rpm, 0, 13.8):
        if name in ENGINE_FRAMES:
            sender.set(name, period, msg)
    if not seen:
        log("CHECK: the fault report did NOT change with the engine gone (J1708 watch or engine frames not working)")
        return False
    log("CHECK: bus watch saw the fault report change with the engine gone (J1708 path works)")
    log(f"CHECK: engine back on, waiting for the fault to clear (up to {fault_timeout} s)...")
    watch.mark()
    if watch.wait_for(FAULT_REPORT, fault_timeout):
        log("CHECK: fault report changed back")
    else:
        log("CHECK: fault report didn't change back yet; the first sweep steps may show it")
    return ok


def run_check(sender, camera, detector, watch, wake, seconds, rpm, wake_rpm=1000, recover=5.0, log=print,
              wake_vecu=0x27):
    """Send something known to move the cluster and check the sweep sees it. True if it did.

    With --wake running, the known change is the tach: EEC1 at `rpm` instead of
    the wake's speed. Without it, it's the whole can_wake engine heartbeat,
    which moves the tach and clears the cluster's SID 200 fault (seen by the
    bus watch on J1708, which reports faults about every 15 s).
    """
    sent, errors, heard, last_error = sender.stats()
    if heard == 0:
        log("CHECK: nothing heard from the cluster on CAN; is it powered, is CAN wired, is the 120 ohm jumper fitted?")
    if wake:
        frames = [f for f in can_wake.heartbeats(0x27, rpm, 0, 13.8) if f[0] == "EEC1"]
        what = f"tach to {rpm:g} rpm (from {wake_rpm:g})"
    else:
        frames = can_wake.heartbeats(0x27, rpm, 0, 13.8)
        what = f"engine heartbeat at {rpm:g} rpm"
    log(f"CHECK: sending the {what} for {seconds:g} s")
    if not isinstance(camera, NullCamera):
        rebaseline(camera, detector)
    snapshot = watch.mark() if watch else None
    for name, period, msg in frames:
        sender.set(name, period, msg)
    best = 0.0
    end = time.time() + seconds
    while time.time() < end:
        score, _ = detector.diff(camera.frame())
        best = max(best, score)
    if wake:
        for name, period, msg in can_wake.heartbeats(0x27, wake_rpm, 0, 13.8):
            if name == "EEC1":
                sender.set(name, period, msg)
    else:
        for name, _, _ in frames:
            sender.remove(name)
    sent, errors, heard, last_error = sender.stats()
    log(f"CHECK: sent {sent} frames, {errors} send errors, heard {heard} cluster frames"
        + (f" (last error: {last_error})" if last_error else ""))
    camera_ok = isinstance(camera, NullCamera) or best >= detector.min_area
    if not isinstance(camera, NullCamera):
        log(f"CHECK: camera {'saw' if camera_ok else 'did NOT see'} it (change {best:.2%}, needs {detector.min_area:.2%}). "
            + ("" if camera_ok else "Is the tach in view and not masked by the ignore areas?"))
    bus_ok = True
    if watch:
        changed = watch.changes()
        if changed:
            log(f"CHECK: bus watch saw: {describe_changes(watch, changed, snapshot)}")
        elif not wake:
            bus_ok = False
            log("CHECK: bus watch saw no change (with --j1708-port, SID 200 should clear within ~15 s)")
        else:
            log("CHECK: bus watch saw no change (expected: the cluster doesn't report rpm)")
    if watch:
        bus_ok = bus_check(sender, watch, wake, wake_vecu, wake_rpm, log) and bus_ok
    ok = camera_ok and bus_ok and errors == 0 and heard > 0
    log(f"CHECK: {'PASS' if ok else 'FAIL'}")
    time.sleep(recover)   # let the needle settle back before the sweep's baseline
    return ok


def run_sweep(bus, camera, detector, cands, out_dir, dwell=1.0, settle=0.5, recover=5.0,
              period=0.1, priority=6, log=print, transport=None, watch=None):
    """Send each candidate and record the ones that change the picture or the cluster's output.

    bus is the CAN bus, or None when a transport is given. watch is an
    optional BusWatch; camera may be a NullCamera to rely on it alone."""
    if transport is None:
        transport = CanTransport(bus, priority)
        try:
            return run_sweep(bus, camera, detector, cands, out_dir, dwell, settle, recover, period, priority, log,
                             transport, watch)
        finally:
            transport.close()
    img_dir = os.path.join(out_dir, "img")
    os.makedirs(img_dir, exist_ok=True)
    tried_path = os.path.join(out_dir, "tried.csv")
    hits_path = os.path.join(out_dir, "hits.csv")
    done = {key(int(r["sa"], 16), int(r["pgn"], 16), bytes.fromhex(r["payload"])) for r in read_rows(tried_path)}
    hit_count = len(read_rows(hits_path))
    todo = [c for c in cands if key(*c) not in done]
    log(f"{len(cands)} candidates, {len(cands) - len(todo)} already tried, {len(todo)} to go "
        f"(about {len(todo) * (dwell + settle) / 60:.0f} min plus recovery after hits)")

    rebaseline(camera, detector)
    start = time.time()
    for i, (sa, pgn, payload) in enumerate(todo):
        idle = camera.frame()
        if detector.changed(idle):
            if not wait_idle(camera, detector, recover):
                log("  picture changed with nothing sent; taking a new baseline")
                rebaseline(camera, detector)
            idle = camera.frame()
        detector.adapt(idle)

        snapshot = watch.mark() if watch else None
        task = transport.start(sa, pgn, payload, period)
        best, best_frame, best_mask = 0.0, None, None
        stop_at = time.time() + dwell
        end = stop_at + settle
        while time.time() < end:
            if task and time.time() >= stop_at:
                task.stop()
                task = None
            frame = camera.frame()
            score, mask = detector.diff(frame)
            if score > best:
                best, best_frame, best_mask = score, frame, mask
        if task:
            task.stop()

        camera_hit = best >= detector.min_area
        changed = watch.changes(snapshot) if watch else []
        bus_text = describe_changes(watch, changed, snapshot) if changed else ""
        hit = camera_hit or bool(changed)
        now = datetime.now().isoformat(timespec="seconds")
        if hit:
            hit_count += 1
            if changed:
                watch.blame(changed, log)
            if camera_hit:
                latched = not wait_idle(camera, detector, recover)
                if latched:
                    rebaseline(camera, detector)
            else:
                latched = False
                time.sleep(recover)   # let the cluster's output settle back before the next candidate
            stem = f"hit_{hit_count:04d}_{sa:02X}_{pgn:05X}_{payload.hex()}"
            if not isinstance(camera, NullCamera):
                cv2.imwrite(os.path.join(img_dir, stem + "_before.jpg"), idle)
                frame = best_frame if best_frame is not None else idle
                mask = best_mask if best_mask is not None else np.zeros(idle.shape[:2], np.uint8)
                cv2.imwrite(os.path.join(img_dir, stem + "_after.jpg"), annotate(frame, mask, detector.ignore))
            append_row(hits_path, HIT_FIELDS, {
                "hit": hit_count, "time": now, "sa": f"{sa:02X}", "pgn": f"{pgn:05X}", "name": transport.name(pgn),
                "payload": payload.hex(" "), "change": f"{best:.4f}", "latched": "yes" if latched else "",
                "before": f"img/{stem}_before.jpg", "after": f"img/{stem}_after.jpg", "bus": bus_text})
            write_review(out_dir, transport.labels)
            log(f"HIT {hit_count}: {transport.labels[0]} {sa:02X} {transport.labels[1]} {pgn:05X} {payload.hex(' ')}  change {best:.2%}"
                + ("  (stayed on, new baseline taken)" if latched else ""))
            if bus_text:
                log(f"  bus: {bus_text}")
        append_row(tried_path, TRIED_FIELDS, {"time": now, "sa": f"{sa:02X}", "pgn": f"{pgn:05X}",
                                              "payload": payload.hex(), "change": f"{best:.4f}", "hit": int(hit),
                                              "bus": bus_text})
        if (i + 1) % 25 == 0 or i + 1 == len(todo):
            rate = (time.time() - start) / (i + 1)
            log(f"  {i + 1}/{len(todo)}  hits {hit_count}  about {(len(todo) - i - 1) * rate / 60:.0f} min left")
    write_review(out_dir, transport.labels)
    return hit_count


REVIEW_HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>Sweep hits</title>
<style>
body{font-family:system-ui,sans-serif;margin:16px;background:#fff;color:#222}
table{border-collapse:collapse}td,th{border-bottom:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}
img{max-width:320px;display:block}tr.ok td{background:#d4edd4}tr.no{opacity:.4}
code{font-size:13px}textarea{width:180px;height:60px}
</style></head><body>
<h1>Sweep hits</h1>
<p>Mark each hit confirmed or ignored and add a note. Marks are saved in this browser;
<button onclick="exportCsv()">Export CSV</button> to keep them.</p>
<table><tr><th>#</th><th>Sent</th><th>Before</th><th>After (changes boxed)</th><th>Verdict</th></tr>
"""

REVIEW_TAIL = """</table>
<script>
const store = (() => { try { return window.localStorage; } catch (e) { return null; } })();
const id = location.pathname;
let marks = {};
try { marks = JSON.parse(store.getItem(id) || "{}"); } catch (e) {}
function save() { try { store.setItem(id, JSON.stringify(marks)); } catch (e) {} }
function mark(n, v) { marks[n] = Object.assign(marks[n] || {}, {v}); save(); paint(); }
function note(n, t) { marks[n] = Object.assign(marks[n] || {}, {t}); save(); }
function paint() {
  document.querySelectorAll("tr[data-hit]").forEach(tr => {
    const m = marks[tr.dataset.hit] || {};
    tr.className = m.v || "";
    tr.querySelector("textarea").value = m.t || "";
  });
}
function exportCsv() {
  const rows = [["hit","sa","pgn","payload","verdict","note"]];
  document.querySelectorAll("tr[data-hit]").forEach(tr => {
    const m = marks[tr.dataset.hit] || {};
    rows.push([tr.dataset.hit, tr.dataset.sa, tr.dataset.pgn, tr.dataset.payload, m.v || "", m.t || ""]);
  });
  const csv = rows.map(r => r.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(",")).join("\\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type: "text/csv"}));
  a.download = "review.csv";
  a.click();
}
paint();
</script></body></html>
"""


def write_review(out_dir, labels=("SA", "PGN")):
    rows = []
    for r in read_rows(os.path.join(out_dir, "hits.csv")):
        e = {k: html.escape(v or "") for k, v in r.items()}
        rows.append(
            f'<tr data-hit="{e["hit"]}" data-sa="{e["sa"]}" data-pgn="{e["pgn"]}" data-payload="{e["payload"]}">'
            f'<td>{e["hit"]}</td>'
            f'<td><code>{labels[0]} {e["sa"]}<br>{labels[1]} {e["pgn"]} {e["name"]}<br>{e["payload"]}</code>'
            f'<br>change {float(r["change"]):.2%}{"<br>stayed on" if r["latched"] else ""}<br>{e["time"]}'
            f'{"<br><b>bus:</b> " + e.get("bus", "") if e.get("bus") else ""}</td>'
            f'<td><img src="{e["before"]}" loading="lazy"></td><td><img src="{e["after"]}" loading="lazy"></td>'
            f'<td><button onclick="mark(\'{e["hit"]}\',\'ok\')">Confirm</button> '
            f'<button onclick="mark(\'{e["hit"]}\',\'no\')">Ignore</button><br>'
            f'<textarea placeholder="What changed?" oninput="note(\'{e["hit"]}\',this.value)"></textarea></td></tr>\n')
    with open(os.path.join(out_dir, "review.html"), "w") as f:
        f.write(REVIEW_HEAD + "".join(rows) + REVIEW_TAIL)


def load_ignore(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def cmd_pick(args):
    cap = open_capture(args.camera, args.width, args.height, args.backend)
    frame = warm_up(cap)
    cap.release()
    if frame is None:
        raise SystemExit("No picture from the camera")
    frame = rotate(frame, args.rotate)
    if looks_blank(frame):
        print("Warning: the picture is one solid color. This may be the wrong camera or a placeholder; "
              "try 'python sweep_watch.py cameras'.")
    rects = cv2.selectROIs("Drag a box around each area to ignore, Enter after each, Esc when done", frame)
    cv2.destroyAllWindows()
    rects = [list(map(int, r)) for r in rects]
    with open(args.ignore, "w") as f:
        json.dump(rects, f)
    print(f"Saved {len(rects)} ignore areas to {args.ignore}")


def cmd_cameras(args):
    """Save a snapshot from each camera number so you can see which is which."""
    os.makedirs("cameras", exist_ok=True)
    backends = list(BACKENDS) if args.backend == "auto" else [args.backend]
    found = 0
    for index in range(args.max):
        for backend in backends:
            if backend in ("v4l2", "ffmpeg") and os.name == "nt":
                continue
            cap = cv2.VideoCapture(index, BACKENDS[backend])
            if not cap.isOpened():
                cap.release()
                continue
            frame = warm_up(cap, 1.5)
            cap.release()
            if frame is None:
                print(f"camera {index} ({backend}): opens but sends no picture")
                continue
            path = os.path.join("cameras", f"camera_{index}_{backend}.jpg")
            cv2.imwrite(path, frame)
            h, w = frame.shape[:2]
            note = "  solid color, likely a placeholder" if looks_blank(frame) else ""
            print(f"camera {index} ({backend}): {w}x{h}, saved {path}{note}")
            found += 1
    if not found:
        print("No cameras found. Check that the DroidCam PC client is connected to the phone.")


def cmd_preview(args):
    camera = Camera(args.camera, args.width, args.height, args.backend, rotation=args.rotate)
    detector = ChangeDetector(load_ignore(args.ignore), args.pixel_threshold, args.min_area)
    rebaseline(camera, detector)
    print("Live view: changed areas are boxed in red. b = new baseline, q = quit")
    try:
        while True:
            frame = camera.frame()
            score, mask = detector.diff(frame)
            view = annotate(frame, mask, detector.ignore)
            color = (0, 0, 255) if score >= detector.min_area else (0, 160, 0)
            cv2.putText(view, f"change {score:.2%} (hit at {detector.min_area:.2%})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
            cv2.imshow("sweep_watch preview", view)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            if k == ord("b"):
                rebaseline(camera, detector)
    finally:
        camera.close()
        cv2.destroyAllWindows()


def cmd_sweep(args):
    if args.transport == "j1708":
        if not args.port:
            raise SystemExit("--transport j1708 needs --port, the ESP32 bridge's COM port")
        if args.bits or args.background or args.wake:
            raise SystemExit("--bits, --background and --wake only work on CAN")
        cands = j1708_candidates(parse_hex_list(args.mids), parse_hex_list(args.pids), parse_hex_list(args.fills))
    else:
        if not args.channel or not (args.pgns or args.check_only):
            raise SystemExit("A CAN sweep needs --channel and --pgns")
        cands = candidates(parse_hex_list(args.pgns), parse_hex_list(args.sources), parse_payloads(args.payloads),
                           args.bits) if args.pgns else []
    out_dir = args.out or os.path.join("sweeps", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    print(f"Writing to {out_dir}")

    bus = port = transport = reader = None
    background = []
    watch = None if args.no_bus_watch else BusWatch()
    if args.camera == "none" and not watch:
        raise SystemExit("--camera none needs the bus watch; leave out --no-bus-watch")
    if args.transport == "j1708":
        import serial

        port = serial.Serial(args.port, args.baud, timeout=0)
        transport = J1708Transport(port)
        if watch:
            reader = J1708Reader(port, watch)
    else:
        bus = canbus.open_bus(args)
        for spec in args.background:
            pgn, sa, data = spec.split(":")
            payload = bytes.fromhex(data.replace(" ", "")).ljust(8, b"\xFF")
            msg = can.Message(arbitration_id=j1939.make_id(int(pgn, 16), int(sa, 16)), data=payload,
                              is_extended_id=True)
            background.append((f"background {len(background) + 1}", 0.1, msg))
        if args.wake:
            background += can_wake.heartbeats(int(args.wake_vecu, 16), args.wake_rpm, 0, 13.8)
            print(f"Running the can_wake engine heartbeat underneath ({args.wake_rpm:g} rpm)")
        transport = CanTransport(bus, sender=can_wake.CanSender(bus, background,
                                                                on_rx=watch.observe_can if watch else None))
        if watch and args.j1708_port:
            import serial

            port = serial.Serial(args.j1708_port, args.baud, timeout=0)
            reader = J1708Reader(port, watch)
    if args.camera == "none":
        camera, detector = NullCamera(), ChangeDetector()
    else:
        camera = Camera(args.camera, args.width, args.height, args.backend, rotation=args.rotate)
        detector = ChangeDetector(load_ignore(args.ignore), args.pixel_threshold, args.min_area)
    try:
        if background:
            time.sleep(2)
        if watch:
            print(f"Learning the cluster's normal output for {args.learn:g} s...")
            watch.learn(args.learn)
            if not watch.latest:
                print("WARNING: heard nothing from the cluster; the bus watch can't find hits")
        if args.check or args.check_only:
            if not isinstance(transport, CanTransport):
                raise SystemExit("--check works on CAN sweeps only")
            seconds = args.check_seconds or (6 if args.wake else 20)
            ok = run_check(transport.sender, camera, detector, watch, args.wake, seconds, args.check_rpm,
                           args.wake_rpm, args.recover, wake_vecu=int(args.wake_vecu, 16))
            if args.check_only:
                return
            if not ok and input("Check failed. Start the sweep anyway? [y/N] ").strip().lower() != "y":
                return
        hits = run_sweep(bus, camera, detector, cands, out_dir, args.dwell, args.settle, args.recover,
                         transport=transport, watch=watch)
        print(f"Done: {hits} hits. Open {os.path.join(out_dir, 'review.html')} to review them.")
    except KeyboardInterrupt:
        write_review(out_dir, transport.labels)
        print(f"Stopped. Run the same command with --out {out_dir} to resume.")
    finally:
        if reader:
            reader.close()
        if isinstance(transport, CanTransport):
            transport.sender.close()
        camera.close()
        if bus:
            canbus.close_bus(bus)
        if port:
            port.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def camera_args(p):
        p.add_argument("--camera", default="0", help="camera number or stream address; sweep: none to use only the bus watch")
        p.add_argument("--width", type=int)
        p.add_argument("--height", type=int)
        p.add_argument("--backend", choices=list(BACKENDS), default="auto",
                       help="camera driver for camera numbers; on Windows try dshow or msmf")
        p.add_argument("--rotate", type=int, choices=list(ROTATIONS), default=0,
                       help="turn the picture clockwise by this many degrees")
        p.add_argument("--ignore", default="ignore.json", help="file of areas to ignore (from pick)")
        p.add_argument("--pixel-threshold", type=int, default=30, help="brightness change that counts (0-255)")
        p.add_argument("--min-area", type=float, default=0.002, help="fraction of pixels that must change")

    cams = sub.add_parser("cameras", help="save a snapshot from each camera number")
    cams.add_argument("--max", type=int, default=6, help="camera numbers to try, from 0")
    cams.add_argument("--backend", choices=list(BACKENDS), default="auto")
    camera_args(sub.add_parser("pick", help="mark areas of the picture to ignore"))
    camera_args(sub.add_parser("preview", help="live view of what the detector sees"))
    sw = sub.add_parser("sweep", help="run a sweep")
    camera_args(sw)
    sw.add_argument("--transport", choices=["can", "j1708"], default="can", help="sweep J1939 PGNs or J1587 PIDs")
    canbus.add_bus_args(sw, channel_required=False)
    sw.add_argument("--port", help="j1708: the ESP32 bridge's serial port, e.g. COM11")
    sw.add_argument("--baud", type=int, default=9600, help="j1708: serial speed (the bridge ignores it)")
    sw.add_argument("--mids", default="80,90", help="j1708: hex MIDs to send as (128 engine, 144 likely VECU)")
    sw.add_argument("--pids", default="00-FD", help="j1708: hex PIDs and ranges to send")
    sw.add_argument("--fills", default="00,FF", help="j1708: hex fill bytes for the data")
    sw.add_argument("--pgns", help="hex PGNs and ranges, e.g. FF00-FFFF,FEF1")
    sw.add_argument("--sources", default="00", help="hex source addresses, e.g. 00,03,21")
    sw.add_argument("--payloads", default="00,FF", help="fill bytes or 8-byte hex payloads")
    sw.add_argument("--bits", action="store_true", help="flip one bit at a time from the first payload")
    sw.add_argument("--background", action="append", default=[], metavar="PGN:SA:DATA",
                    help="also send this every 100 ms for the whole run, e.g. F004:00:FFFFFF401FFFFFFF")
    sw.add_argument("--no-bus-watch", action="store_true",
                    help="don't count changes in the cluster's own CAN/J1708 output as hits")
    sw.add_argument("--j1708-port", help="CAN sweep: also watch the cluster's J1708 output on this bridge port")
    sw.add_argument("--learn", type=float, default=30,
                    help="seconds to watch the cluster's output before the sweep, to learn what changes by itself")
    sw.add_argument("--check", action="store_true",
                    help="CAN: before the sweep, send a known change (the tach) and check the camera/bus watch see it")
    sw.add_argument("--check-only", action="store_true", help="CAN: run --check and stop")
    sw.add_argument("--check-rpm", type=float, default=2000, help="engine speed for --check (default 2000)")
    sw.add_argument("--check-seconds", type=float, help="how long --check sends (default 6 with --wake, else 20)")
    sw.add_argument("--wake", action="store_true",
                    help="CAN: run can_wake's engine heartbeat (EEC1, ET1, EFL/P1, DM1, VEP1, CCVS1) the whole time")
    sw.add_argument("--wake-rpm", type=float, default=1000, help="engine speed for --wake (default 1000)")
    sw.add_argument("--wake-vecu", default="27", help="source address for --wake's VEP1/CCVS1 (default 27)")
    sw.add_argument("--dwell", type=float, default=1.0, help="seconds to send each candidate")
    sw.add_argument("--settle", type=float, default=0.5, help="seconds to keep watching after it stops")
    sw.add_argument("--recover", type=float, default=5.0, help="seconds to wait for idle after a hit")
    sw.add_argument("--out", help="run folder (reuse one to resume)")
    args = ap.parse_args()
    {"cameras": cmd_cameras, "pick": cmd_pick, "preview": cmd_preview, "sweep": cmd_sweep}[args.cmd](args)


if __name__ == "__main__":
    main()
