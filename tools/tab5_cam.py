"""The M5Stack Tab5 as the sweep camera: set it up, mark the regions to watch, and log what changes.

The Tab5 (firmware in tab5cam/) does the watching itself: exposure and gain are set once and held, each
region is compared with a baseline, and it reports events over Wi-Fi. This script runs next to
sweep_watch.py (use --camera none there) and lines the events up with the sweep's windows.csv.

    python tab5_cam.py setup --port COM6 --ssid "Shop" --password "..."   # once, over USB
    python tab5_cam.py pick                     # draw the regions on a snapshot
    python tab5_cam.py exposure                 # set exposure for the regions, then hold it
    python tab5_cam.py watch --sweep sweeps/run1    # start before the sweep, Ctrl+C after
    python tab5_cam.py match sweeps/run1        # line up again afterwards (e.g. other --margin)

--host defaults to vnl-cam.local; give the address `setup` printed if that name doesn't resolve.
Live view with scores: http://vnl-cam.local/ in a browser.

Region kinds: needle (counts moved pixels, after removing the region's overall brightness change, so
an even glare doesn't count), lamp (counts changed pixels), ref (a part of the cluster that never
changes; when it does, everything at that moment is treated as glare and not reported as a hit).

watch writes, in the --out folder (default: the --sweep folder):
    cam_events.csv   every event, with PC time (epoch seconds, same clock as windows.csv)
    cam_hits.csv     region changes matched to the candidate being sent
    cam/hit_<id>.jpg baseline | changed frame, region boxed
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

EVENT_FIELDS = ["time", "dev_ms", "ev", "id", "roi", "score", "peak", "dur", "detail"]
HIT_FIELDS = ["id", "time", "roi", "score", "sa", "pgn", "payload", "late_s", "picture"]
KINDS = ("needle", "lamp", "ref")


# ---------- HTTP ----------

def url(host, path):
    return f"http://{host}{path}"


def get(host, path, timeout=10):
    with urllib.request.urlopen(url(host, path), timeout=timeout) as r:
        return r.read()


def get_json(host, path):
    return json.loads(get(host, path))


def post(host, path, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url(host, path), data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"The Tab5 refused it: {e.read().decode(errors='replace')}")


def sse_events(host, since=0, timeout=10):
    """Yield (queue number, event dict) from /events. Raises OSError when the connection drops."""
    path = "/events" + (f"?since={since}" if since else "")
    with urllib.request.urlopen(url(host, path), timeout=timeout) as r:
        yield from parse_sse(r)


def parse_sse(lines):
    n, data = 0, []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n") if isinstance(raw, bytes) else raw.rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield n, json.loads("\n".join(data))
                except ValueError:
                    pass
            n, data = 0, []
        elif line.startswith("id:"):
            n = int(line[3:].strip() or 0)
        elif line.startswith("data:"):
            data.append(line[5:].strip())


class ClockSync:
    """Maps the Tab5's millisecond clock to PC epoch seconds.

    Each event is stamped on arrival; the smallest (arrival - device time) seen is the offset with the
    least network delay in it. Starts over if the device clock goes backwards (it rebooted)."""

    def __init__(self):
        self.offset = None
        self.last = None

    def observe(self, dev_ms, now=None):
        now = time.time() if now is None else now
        if self.last is not None and dev_ms < self.last - 1000:
            self.offset = None
        self.last = dev_ms
        d = now - dev_ms / 1000.0
        if self.offset is None or d < self.offset:
            self.offset = d
        return self.to_pc(dev_ms)

    def to_pc(self, dev_ms):
        return dev_ms / 1000.0 + self.offset


# ---------- matching to windows.csv ----------

def read_windows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [(float(r["start"]), float(r["end"]), r["sa"], r["pgn"], r["payload"]) for r in csv.DictReader(f)]


def match(windows, t, margin=0.5):
    """The window an event at PC time t belongs to: the one it falls in, else the last one that ended
    at most margin seconds before it (the change can show up just after the send stops). Returns
    (window, seconds after its end) or (None, None)."""
    best = None
    for w in windows:
        if w[0] <= t <= w[1]:
            return w, 0.0
        if w[1] < t <= w[1] + margin and (best is None or w[1] > best[1]):
            best = w
    return (best, round(t - best[1], 3)) if best else (None, None)


def append(path, fields, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def read_events(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---------- commands ----------

def cmd_setup(args):
    import serial

    with serial.Serial(args.port, 115200, timeout=0.2) as s:
        s.dtr = False
        s.reset_input_buffer()
        line = "wifi " + json.dumps({"ssid": args.ssid, "pass": args.password}) + "\n"
        s.write(line.encode())
        print(f"Sent the network to the Tab5 on {args.port}, waiting for an address...")
        buf = b""
        end = time.time() + args.wait
        while time.time() < end:
            buf += s.read(512)
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                text = raw.decode("utf-8", "replace").strip()
                if text.startswith(("IP ", "ERROR", "WIFI")) or "Wi-Fi" in text or "C6" in text:
                    print("  " + text)
                if text.startswith("IP "):
                    print(f"Done. Use --host {text.split()[1]} if vnl-cam.local doesn't resolve.")
                    return
        raise SystemExit("No address yet. Check the network name and password, and that it's 2.4 or 5 GHz Wi-Fi "
                         "the Tab5 can reach; `python tab5_cam.py bootlog --port COMx` shows its log.")


def cmd_bootlog(args):
    """Reset the Tab5, capture its log, print a short summary. Exit 0 ok, 2 panic/reboot, 3 no camera."""
    import re
    import serial

    s = serial.Serial(args.port, 115200, timeout=0.2)
    s.dtr = False
    s.reset_input_buffer()
    s.rts = True
    time.sleep(0.1)
    s.rts = False
    data = bytearray()
    end = time.time() + args.seconds
    while time.time() < end:
        data += s.read(4096)
    s.close()
    if args.log:
        with open(args.log, "wb") as f:
            f.write(data)
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", l).rstrip() for l in data.decode("utf-8", "replace").splitlines()]
    panic = re.compile(r"Guru Meditation|abort\(\)|Backtrace:|panic|assert failed|Stack smashing", re.I)
    bad = [l for l in lines if panic.search(l)]
    boots = sum("rst:" in l for l in lines)
    keep = re.compile(r"vnl-tab5cam|CAMERA|Sensor |Exposure|C6 runs|^IP |NO WIFI|Auto exposure|regions|[EW] \(\d+\)")
    shown = [l for l in lines if keep.search(l)]
    cam_ok = any("CAMERA OK" in l for l in lines)
    print(f"boot: {boots} reset(s), camera {'OK' if cam_ok else 'NOT started'}, {len(bad)} panic line(s)")
    for l in (bad + shown)[:14]:
        print("  " + l[:160])
    sys.exit(2 if bad or boots > 1 else 0 if cam_ok else 3)


def cmd_snap(args):
    data = get(args.host, "/snapshot.jpg" + ("?overlay=1" if args.overlay else ""))
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"Saved {args.out}")


def cmd_pick(args):
    import cv2
    import numpy as np

    cfg = get_json(args.host, "/config")
    img = cv2.imdecode(np.frombuffer(get(args.host, "/snapshot.jpg"), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("No picture from the Tab5")
    rois = list(cfg.get("rois", [])) if args.add else []
    scale = args.scale
    print("Drag a box around a gauge or lamp, Enter to keep it, then name it in this window. "
          "Esc in the picture when done. Add one 'ref' box on a part of the cluster that never changes.")
    while True:
        view = img.copy()
        for r in rois:
            color = (0, 0, 255) if r["kind"] == "ref" else (0, 200, 0)
            cv2.rectangle(view, (r["x"], r["y"]), (r["x"] + r["w"], r["y"] + r["h"]), color, 1)
            cv2.putText(view, r["name"], (r["x"], max(10, r["y"] - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        big = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        x, y, w, h = cv2.selectROI("Tab5 regions (Esc = done)", big, showCrosshair=False)
        if w == 0 or h == 0:
            break
        box = dict(x=int(x / scale), y=int(y / scale), w=max(2, int(w / scale)), h=max(2, int(h / scale)))
        name = input("  name (e.g. tach, speedo, lamp_park): ").strip() or f"roi{len(rois) + 1}"
        kind = input("  kind [needle/lamp/ref] (default needle): ").strip().lower() or "needle"
        if kind not in KINDS:
            print("  unknown kind, using needle")
            kind = "needle"
        rois = [r for r in rois if r["name"] != name] + [dict(name=name, kind=kind, **box)]
    cv2.destroyAllWindows()
    if not rois:
        raise SystemExit("No regions, nothing changed")
    post(args.host, "/config", {"rois": rois})
    with open(args.save, "w") as f:
        json.dump({"rois": rois}, f, indent=1)
    print(f"Sent {len(rois)} regions to the Tab5 (kept there across restarts) and saved {args.save}. "
          "Now run: python tab5_cam.py exposure")


def wait_for(host, evs, timeout):
    end = time.time() + timeout
    for _, e in sse_events(host, timeout=timeout):
        if e.get("ev") in evs:
            return e
        if time.time() > end:
            break
    return None


def cmd_exposure(args):
    path = "/autoexp" + (f"?target={args.target}" if args.target else "")
    post(args.host, path)
    print("Setting exposure with the cluster idle (lamps as they normally are)...")
    e = wait_for(args.host, ("autoexp",), 60)
    if not e:
        raise SystemExit("No answer within 60 s; see http://%s/" % args.host)
    print(f"Exposure {e['exposure']}, gain {e['gain']}, mean {e['mean']} (target {e['target']}). "
          "Held from now on, also after a restart. Rerun only if the light or the mounting changes.")


def cmd_baseline(args):
    post(args.host, "/baseline")
    e = wait_for(args.host, ("baseline",), 15)
    print("New baseline taken" if e else "Asked for a new baseline (no confirmation yet)")


def cmd_config(args):
    if args.set:
        body = {}
        for item in args.set:
            k, _, v = item.partition("=")
            body[k] = json.loads(v)
        post(args.host, "/config", body)
    if args.load:
        with open(args.load) as f:
            post(args.host, "/config", json.load(f))
    print(json.dumps(get_json(args.host, "/config"), indent=1))


def describe(w):
    return f"{w[2]} {w[3]} data {w[4]}" if w else "no candidate"


def record_hit(out, sweep, e, t, margin, log=print):
    windows = read_windows(os.path.join(sweep, "windows.csv")) if sweep else []
    w, late = match(windows, t, margin)
    pic = os.path.join("cam", f"hit_{e['id']:04d}.jpg")
    append(os.path.join(out, "cam_hits.csv"), HIT_FIELDS, {
        "id": e["id"], "time": f"{t:.3f}", "roi": e.get("roi", ""), "score": e.get("score", ""),
        "sa": w[2] if w else "", "pgn": w[3] if w else "", "payload": w[4] if w else "",
        "late_s": late if late is not None else "", "picture": pic})
    log(f"CAM HIT #{e['id']} {e.get('roi')} ({e.get('score', 0):.1%}) during {describe(w)}"
        + (f" (+{late:.2f} s after it stopped)" if late else ""))


def cmd_watch(args):
    out = args.out or args.sweep
    if not out:
        raise SystemExit("Give --sweep (the sweep folder) or --out")
    os.makedirs(os.path.join(out, "cam"), exist_ok=True)
    events_path = os.path.join(out, "cam_events.csv")
    sync = ClockSync()
    last_n = 0
    pending = []          # (time, event) waiting for their window to be written to windows.csv
    print(f"Watching http://{args.host}/  ->  {out}   (Ctrl+C to stop)")
    while True:
        try:
            for n, e in sse_events(args.host, since=last_n + 1 if last_n else 0):
                last_n = max(last_n, n)
                ev = e.get("ev")
                if "t" not in e:
                    continue
                t = sync.observe(e["t"])
                if ev == "tick":
                    if pending and time.time() > pending[0][0] + args.margin + args.delay:
                        for pt, pe in pending:
                            record_hit(out, args.sweep, pe, pt, args.margin)
                        pending.clear()
                    continue
                if ev == "hello":
                    continue
                detail = {k: v for k, v in e.items() if k not in ("ev", "t", "id", "roi", "score", "peak", "dur")}
                append(events_path, EVENT_FIELDS, {
                    "time": f"{t:.3f}", "dev_ms": e["t"], "ev": ev, "id": e.get("id", ""), "roi": e.get("roi", ""),
                    "score": e.get("score", ""), "peak": e.get("peak", ""), "dur": e.get("dur", ""),
                    "detail": json.dumps(detail) if detail else ""})
                stamp = time.strftime("%H:%M:%S", time.localtime(t))
                if ev == "start":
                    try:
                        with open(os.path.join(out, "cam", f"hit_{e['id']:04d}.jpg"), "wb") as f:
                            f.write(get(args.host, f"/hit?id={e['id']}"))
                    except OSError:
                        pass
                    pending.append((t, e))
                elif ev == "glare":
                    print(f"{stamp}  glare ({', '.join(e.get('rois', []))}), not counted")
                elif ev == "latched":
                    print(f"{stamp}  #{e['id']} {e.get('roi')} stayed changed; new baseline for it")
                elif ev == "autoexp":
                    print(f"{stamp}  exposure set: {e.get('exposure')} gain {e.get('gain')}")
        except KeyboardInterrupt:
            for pt, pe in pending:
                record_hit(out, args.sweep, pe, pt, args.margin)
            print(f"Stopped. Events in {events_path}")
            return
        except (OSError, ValueError) as err:
            print(f"Lost the Tab5 ({err}); reconnecting...")
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                return


def cmd_match(args):
    events_path = args.events or os.path.join(args.sweep, "cam_events.csv")
    windows = read_windows(os.path.join(args.sweep, "windows.csv"))
    if not windows:
        raise SystemExit(f"No windows.csv in {args.sweep}")
    out_path = os.path.join(args.sweep, "cam_hits.csv")
    if os.path.exists(out_path):
        os.remove(out_path)
    hits = 0
    for r in read_events(events_path):
        if r["ev"] != "start":
            continue
        e = {"id": int(r["id"]), "roi": r["roi"], "score": float(r["score"] or 0)}
        record_hit(args.sweep, args.sweep, e, float(r["time"]), args.margin)
        hits += 1
    print(f"{hits} region changes, written to {out_path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def host(p):
        p.add_argument("--host", default="vnl-cam.local", help="the Tab5's address (default vnl-cam.local)")
        return p

    s = sub.add_parser("setup", help="give the Tab5 the Wi-Fi network, over USB")
    s.add_argument("--port", required=True)
    s.add_argument("--ssid", required=True)
    s.add_argument("--password", default="")
    s.add_argument("--wait", type=float, default=40)
    b = sub.add_parser("bootlog", help="reset the Tab5 over USB and summarize its log")
    b.add_argument("--port", required=True)
    b.add_argument("--seconds", type=float, default=15)
    b.add_argument("--log")
    sn = host(sub.add_parser("snap", help="save a snapshot"))
    sn.add_argument("--out", default="tab5_snapshot.jpg")
    sn.add_argument("--overlay", action="store_true", help="draw the regions in")
    pk = host(sub.add_parser("pick", help="draw the regions to watch"))
    pk.add_argument("--add", action="store_true", help="keep the regions already on the Tab5")
    pk.add_argument("--scale", type=float, default=1.5, help="zoom for drawing (the picture is 800x600)")
    pk.add_argument("--save", default="tab5_rois.json")
    ex = host(sub.add_parser("exposure", help="set exposure and gain once, then hold them"))
    ex.add_argument("--target", type=int, help="mean gray level in the regions (default 90)")
    host(sub.add_parser("baseline", help="take a new baseline now"))
    cf = host(sub.add_parser("config", help="show or change detector settings"))
    cf.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="pix_thr, persist, glare_frac, latch_s, adapt, target, exposure, gain")
    cf.add_argument("--load", help="JSON file to send, e.g. tab5_rois.json from pick")
    w = host(sub.add_parser("watch", help="log events during a sweep and match them to its candidates"))
    w.add_argument("--sweep", help="the sweep's folder (sweep_watch.py --out); its windows.csv is read live")
    w.add_argument("--out", help="where to write (default: the --sweep folder)")
    w.add_argument("--margin", type=float, default=0.5, help="seconds after a window still counted (default 0.5)")
    w.add_argument("--delay", type=float, default=5,
                   help="seconds to wait for the sweep to write the window before matching (more than --dwell + --settle)")
    m = sub.add_parser("match", help="match cam_events.csv to windows.csv again, afterwards")
    m.add_argument("sweep")
    m.add_argument("--events")
    m.add_argument("--margin", type=float, default=0.5)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)   # lines show up at once, also when piped
    except AttributeError:
        pass
    {"setup": cmd_setup, "bootlog": cmd_bootlog, "snap": cmd_snap, "pick": cmd_pick, "exposure": cmd_exposure,
     "baseline": cmd_baseline, "config": cmd_config, "watch": cmd_watch, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    main()
