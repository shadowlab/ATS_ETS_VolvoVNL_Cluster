"""Sweep J1939 PGNs while a camera watches the cluster, and log what changes.

Each candidate (source address, PGN, payload) is sent for --dwell seconds,
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
    python sweep_watch.py sweep --channel COM5 --camera 0 --pgns FF00-FFFF --sources 00,03,0B,21,31,17 --payloads 00,FF

Narrow a hit down bit by bit (64 candidates, each with one bit flipped from
the first payload):

    python sweep_watch.py sweep --channel COM5 --camera 0 --pgns FF23 --sources 21 --payloads 00 --bits

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

import canbus
import j1939

HIT_FIELDS = ["hit", "time", "sa", "pgn", "name", "payload", "change", "latched", "before", "after"]
TRIED_FIELDS = ["time", "sa", "pgn", "payload", "change", "hit"]


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
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def run_sweep(bus, camera, detector, cands, out_dir, dwell=1.0, settle=0.5, recover=5.0,
              period=0.1, priority=6, log=print):
    """Send each candidate and record the ones that change the picture."""
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

        msg = can.Message(arbitration_id=j1939.make_id(pgn, sa, priority=priority), data=payload, is_extended_id=True)
        task = bus.send_periodic(msg, period)
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

        hit = best >= detector.min_area
        now = datetime.now().isoformat(timespec="seconds")
        if hit:
            hit_count += 1
            latched = not wait_idle(camera, detector, recover)
            if latched:
                rebaseline(camera, detector)
            stem = f"hit_{hit_count:04d}_{sa:02X}_{pgn:05X}_{payload.hex()}"
            cv2.imwrite(os.path.join(img_dir, stem + "_before.jpg"), idle)
            cv2.imwrite(os.path.join(img_dir, stem + "_after.jpg"), annotate(best_frame, best_mask, detector.ignore))
            append_row(hits_path, HIT_FIELDS, {
                "hit": hit_count, "time": now, "sa": f"{sa:02X}", "pgn": f"{pgn:05X}", "name": j1939.pgn_name(pgn),
                "payload": payload.hex(" "), "change": f"{best:.4f}", "latched": "yes" if latched else "",
                "before": f"img/{stem}_before.jpg", "after": f"img/{stem}_after.jpg"})
            write_review(out_dir)
            log(f"HIT {hit_count}: SA {sa:02X} PGN {pgn:05X} {payload.hex(' ')}  change {best:.2%}"
                + ("  (stayed on, new baseline taken)" if latched else ""))
        append_row(tried_path, TRIED_FIELDS, {"time": now, "sa": f"{sa:02X}", "pgn": f"{pgn:05X}",
                                              "payload": payload.hex(), "change": f"{best:.4f}", "hit": int(hit)})
        if (i + 1) % 25 == 0 or i + 1 == len(todo):
            rate = (time.time() - start) / (i + 1)
            log(f"  {i + 1}/{len(todo)}  hits {hit_count}  about {(len(todo) - i - 1) * rate / 60:.0f} min left")
    write_review(out_dir)
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


def write_review(out_dir):
    rows = []
    for r in read_rows(os.path.join(out_dir, "hits.csv")):
        e = {k: html.escape(v or "") for k, v in r.items()}
        rows.append(
            f'<tr data-hit="{e["hit"]}" data-sa="{e["sa"]}" data-pgn="{e["pgn"]}" data-payload="{e["payload"]}">'
            f'<td>{e["hit"]}</td>'
            f'<td><code>SA {e["sa"]}<br>PGN {e["pgn"]} {e["name"]}<br>{e["payload"]}</code>'
            f'<br>change {float(r["change"]):.2%}{"<br>stayed on" if r["latched"] else ""}<br>{e["time"]}</td>'
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
    cands = candidates(parse_hex_list(args.pgns), parse_hex_list(args.sources), parse_payloads(args.payloads), args.bits)
    out_dir = args.out or os.path.join("sweeps", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    print(f"Writing to {out_dir}")

    bus = canbus.open_bus(args)
    background = []
    for spec in args.background:
        pgn, sa, data = spec.split(":")
        payload = bytes.fromhex(data.replace(" ", "")).ljust(8, b"\xFF")
        msg = can.Message(arbitration_id=j1939.make_id(int(pgn, 16), int(sa, 16)), data=payload, is_extended_id=True)
        background.append(bus.send_periodic(msg, 0.1))
    camera = Camera(args.camera, args.width, args.height, args.backend, rotation=args.rotate)
    detector = ChangeDetector(load_ignore(args.ignore), args.pixel_threshold, args.min_area)
    try:
        if background:
            time.sleep(2)
        hits = run_sweep(bus, camera, detector, cands, out_dir, args.dwell, args.settle, args.recover)
        print(f"Done: {hits} hits. Open {os.path.join(out_dir, 'review.html')} to review them.")
    except KeyboardInterrupt:
        write_review(out_dir)
        print(f"Stopped. Run the same command with --out {out_dir} to resume.")
    finally:
        for task in background:
            task.stop()
        camera.close()
        canbus.close_bus(bus)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def camera_args(p):
        p.add_argument("--camera", default="0", help="camera number or stream address")
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
    canbus.add_bus_args(sw)
    sw.add_argument("--pgns", required=True, help="hex PGNs and ranges, e.g. FF00-FFFF,FEF1")
    sw.add_argument("--sources", default="00", help="hex source addresses, e.g. 00,03,21")
    sw.add_argument("--payloads", default="00,FF", help="fill bytes or 8-byte hex payloads")
    sw.add_argument("--bits", action="store_true", help="flip one bit at a time from the first payload")
    sw.add_argument("--background", action="append", default=[], metavar="PGN:SA:DATA",
                    help="also send this every 100 ms for the whole run, e.g. F004:00:FFFFFF401FFFFFFF")
    sw.add_argument("--dwell", type=float, default=1.0, help="seconds to send each candidate")
    sw.add_argument("--settle", type=float, default=0.5, help="seconds to keep watching after it stops")
    sw.add_argument("--recover", type=float, default=5.0, help="seconds to wait for idle after a hit")
    sw.add_argument("--out", help="run folder (reuse one to resume)")
    args = ap.parse_args()
    {"cameras": cmd_cameras, "pick": cmd_pick, "preview": cmd_preview, "sweep": cmd_sweep}[args.cmd](args)


if __name__ == "__main__":
    main()
