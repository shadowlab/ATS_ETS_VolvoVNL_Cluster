"""Unattended sweep over every range in a plan, with the Tab5 camera logging what changes on the cluster.

Start it once and leave it. For each stage of the plan it checks the Tab5, takes a fresh camera baseline,
starts logging the camera's events, runs sweep_watch.py (--camera none) for that stage's range, then stops
logging and matches every camera change to the candidate on the bus at that moment. When all stages are done
it writes review.html: every camera hit with its picture and candidate, and the bus hits.

    python auto_sweep.py plan                        # the stages, their candidates and run time
    python auto_sweep.py run --out sweeps/auto1 --interface gs_usb --channel 0 --port COM11
    python auto_sweep.py report sweeps/auto1         # rebuild review.html (run does this at the end)

Run the same command with the same --out to resume: finished stages are skipped, and sweep_watch.py
resumes a stage where it stopped. --only picks stages by name. --plan plan.json replaces the built-in plan
(write it with `plan --save plan.json` and edit; each stage is a name plus sweep_watch.py sweep arguments).

Lamps on this cluster latch until it is power cycled, so a lamp that latched early can't show again later
in the same run; the camera takes a new baseline for it, so the other regions keep working.
"""

import argparse
import csv
import html
import json
import os
import subprocess
import sys
import threading
import time

import tab5_cam

HERE = os.path.dirname(os.path.abspath(__file__))

# Ranges not yet covered, or worth covering from more senders. FF01/FF02 from SA 00 reboot the cluster,
# so SA 00 skips them. MIDs are hex: 80 engine, 82 transmission, 88 brakes, 90 vehicle ECU.
DEFAULT_PLAN = [
    {"name": "j1587-engine-vecu", "args": ["--transport", "j1708", "--mids", "80,90", "--pids", "00-FD",
                                           "--fills", "00,FF"]},
    {"name": "j1587-trans-brakes", "args": ["--transport", "j1708", "--mids", "82,88", "--pids", "00-FD",
                                            "--fills", "00,FF"]},
    {"name": "can-ffxx", "args": ["--pgns", "FF00-FFFF", "--sources", "03,0B,11,21,27", "--payloads", "00,FF"]},
    {"name": "can-ffxx-sa00", "args": ["--pgns", "FF00,FF03-FFFF", "--sources", "00", "--payloads", "00,FF"]},
    {"name": "can-fexx", "args": ["--pgns", "FE00-FEFF", "--sources", "00,03,0B,11", "--payloads", "00,FF"]},
    {"name": "can-f0-fd", "args": ["--pgns", "F000-FDFF", "--sources", "00,11", "--payloads", "00,FF"]},
]


def parse_hex_list(text):
    """'FF00,FF03-FFFF' -> [0xFF00, 0xFF03, ...]"""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a, 16), int(b, 16) + 1))
        else:
            out.append(int(part, 16))
    return out


def arg(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def count_candidates(args):
    if arg(args, "--transport", "can") == "j1708":
        pids = [p for p in parse_hex_list(arg(args, "--pids", "00-FD")) if p < 254]
        return len(parse_hex_list(arg(args, "--mids", "80,90"))) * len(pids) * len(arg(args, "--fills", "00,FF").split(","))
    n = len(parse_hex_list(arg(args, "--pgns", ""))) * len(parse_hex_list(arg(args, "--sources", "00")))
    return n * (64 if "--bits" in args else len(arg(args, "--payloads", "00,FF").split(",")))


def stage_seconds(args):
    per = float(arg(args, "--dwell", 1.0)) + float(arg(args, "--settle", 0.5))
    return count_candidates(args) * per


def hours(s):
    return f"{s / 3600:.1f} h" if s >= 3600 else f"{s / 60:.0f} min"


def load_plan(path):
    if not path:
        return DEFAULT_PLAN
    with open(path) as f:
        plan = json.load(f)
    for st in plan:
        if not isinstance(st.get("name"), str) or not isinstance(st.get("args"), list):
            raise SystemExit(f"{path}: each stage needs \"name\" and a list of \"args\"")
    return plan


def cmd_plan(args):
    plan = load_plan(args.plan)
    total = 0
    for st in plan:
        s = stage_seconds(st["args"])
        total += s
        print(f"{st['name']:22} {count_candidates(st['args']):6} candidates  ~{hours(s):>7}   {' '.join(st['args'])}")
    print(f"{'total':22} {'':6}              ~{hours(total):>7}  (plus recovery time after bus hits)")
    if args.save:
        with open(args.save, "w") as f:
            json.dump(plan, f, indent=1)
        print(f"Saved to {args.save}; edit it and pass --plan {args.save}")


# ---------- run ----------

class Log:
    def __init__(self, path):
        self.f = open(path, "a", buffering=1)

    def __call__(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")


def camera_ok(host, log, wait_s):
    end = time.time() + wait_s
    while True:
        try:
            st = tab5_cam.get_json(host, "/status")
            if st.get("fps", 0) > 0:
                return st
            log(f"Tab5 answers but the camera reports {st.get('fps')} fps")
        except (OSError, ValueError, SystemExit) as e:
            log(f"Tab5 not reachable at {host} ({e})")
        if time.time() > end:
            return None
        time.sleep(15)


def bus_args(args, transport):
    out = ["--interface", args.interface, "--channel", args.channel]
    if transport == "j1708":
        if not args.port:
            raise SystemExit("J1587 stages need --port (the ESP32 bridge, e.g. COM11)")
        out += ["--port", args.port, "--wake"]   # engine heartbeat on CAN while J1587 is swept
    elif args.port:
        out += ["--j1708-port", args.port]       # also log the cluster's J1708 answers
    return out


def run_stage(args, st, log):
    d = os.path.join(args.out, st["name"])
    os.makedirs(d, exist_ok=True)
    transport = arg(st["args"], "--transport", "can")
    log(f"=== {st['name']}: {count_candidates(st['args'])} candidates, ~{hours(stage_seconds(st['args']))}")

    stop = threading.Event()
    watcher = None
    if not args.no_camera:
        if camera_ok(args.host, log, args.camera_wait) is None:
            log("Going on without the camera for this stage (bus hits are still logged)")
        else:
            try:
                tab5_cam.post(args.host, "/baseline")
            except (OSError, SystemExit) as e:
                log(f"Baseline request failed ({e})")
            time.sleep(3)
            watcher = threading.Thread(target=tab5_cam.watch, daemon=True,
                                       args=(args.host, d, d, args.margin, args.delay, stop, log))
            watcher.start()

    cmd = [sys.executable, os.path.join(HERE, "sweep_watch.py"), "sweep", "--camera", "none", "--out", d] \
        + bus_args(args, transport) + st["args"]
    log("> " + " ".join(cmd[1:]))
    with open(os.path.join(d, "sweep.log"), "a", buffering=1) as f:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=HERE)
        try:
            for line in p.stdout:
                f.write(line)
                print("   " + line.rstrip(), flush=True)
            rc = p.wait()
        except KeyboardInterrupt:
            p.terminate()
            p.wait()
            rc = None
    if watcher:
        if rc is not None:
            time.sleep(args.margin + args.delay)   # let the last changes arrive and match
        stop.set()
        watcher.join(timeout=20)
    return rc


def cmd_run(args):
    plan = load_plan(args.plan)
    if args.only:
        names = args.only.split(",")
        missing = [n for n in names if n not in {s["name"] for s in plan}]
        if missing:
            raise SystemExit(f"No stage named {', '.join(missing)}; see `auto_sweep.py plan`")
        plan = [s for s in plan if s["name"] in names]
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "plan.json"), "w") as f:
        json.dump(plan, f, indent=1)
    log = Log(os.path.join(args.out, "auto_sweep.log"))
    log(f"Run {args.out}: {len(plan)} stages, ~{hours(sum(stage_seconds(s['args']) for s in plan))} of candidates")
    for st in plan:
        done = os.path.join(args.out, st["name"], "stage_done")
        if os.path.exists(done):
            log(f"=== {st['name']}: done earlier, skipped")
            continue
        rc = run_stage(args, st, log)
        if rc is None:
            log("Stopped. Run the same command to resume.")
            break
        if rc != 0:
            log(f"sweep_watch.py stopped with code {rc} in {st['name']} (see {st['name']}/sweep.log). "
                "Fix it and run the same command to resume.")
            break
        with open(done, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
        log(f"=== {st['name']}: finished")
    path = write_report(args.out)
    log(f"Review: {path}")


# ---------- report ----------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(stage_dir):
    """Camera hits grouped by candidate, plus bus hits, for one stage folder."""
    groups = {}
    for h in read_csv(os.path.join(stage_dir, "cam_hits.csv")):
        key = (h.get("sa", ""), h.get("pgn", ""), h.get("payload", ""))
        groups.setdefault(key, []).append(h)
    bus = [h for h in read_csv(os.path.join(stage_dir, "hits.csv")) if h.get("bus")]
    tried = len(read_csv(os.path.join(stage_dir, "tried.csv")))
    return groups, bus, tried


def write_report(out):
    plan = load_plan(os.path.join(out, "plan.json")) if os.path.exists(os.path.join(out, "plan.json")) else DEFAULT_PLAN
    e = html.escape
    parts = [f"<!doctype html><meta charset=utf-8><title>Auto sweep {e(os.path.basename(os.path.abspath(out)))}</title>",
             "<style>body{font-family:system-ui,sans-serif;margin:16px;background:#fff;color:#111}"
             "@media(prefers-color-scheme:dark){body{background:#151515;color:#eee}}"
             "table{border-collapse:collapse;margin:8px 0 24px}td,th{border-bottom:1px solid #8884;padding:4px 8px;"
             "text-align:left;vertical-align:top}img{max-width:420px;display:block}code{font-size:13px}</style>",
             f"<h1>Auto sweep {e(out)}</h1>"]
    for st in plan:
        d = os.path.join(out, st["name"])
        if not os.path.isdir(d):
            continue
        groups, bus, tried = summarize(d)
        state = "finished" if os.path.exists(os.path.join(d, "stage_done")) else "not finished"
        parts.append(f"<h2>{e(st['name'])}</h2><p>{tried} candidates sent, {state}. "
                     f"{len(groups)} candidates moved something on camera, {len(bus)} bus hits. "
                     f"<code>{e(' '.join(st['args']))}</code></p>")
        if groups:
            parts.append("<table><tr><th>Sender</th><th>PGN / PID</th><th>Data</th><th>Regions</th><th>Picture</th></tr>")
            for (sa, pgn, payload), hs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                rois = ", ".join(sorted({h.get("roi", "") for h in hs}))
                pic = hs[0].get("picture", "")
                img = f"<img src='{e(st['name'])}/{e(pic.replace(os.sep, '/'))}' loading=lazy>" if pic else ""
                parts.append(f"<tr><td>{e(sa or 'none')}</td><td>{e(pgn)}</td><td><code>{e(payload)}</code></td>"
                             f"<td>{e(rois)} ({len(hs)})</td><td>{img}</td></tr>")
            parts.append("</table>")
        if bus:
            parts.append("<table><tr><th>Sender</th><th>PGN / PID</th><th>Data</th><th>Bus change</th></tr>")
            for h in bus:
                parts.append(f"<tr><td>{e(h.get('sa', ''))}</td><td>{e(h.get('pgn', ''))}</td>"
                             f"<td><code>{e(h.get('payload', ''))}</code></td><td>{e(h.get('bus', ''))}</td></tr>")
            parts.append("</table>")
    path = os.path.join(out, "review.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


def cmd_report(args):
    print(write_report(args.out))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="list the stages and how long they take")
    p.add_argument("--plan", help="plan JSON instead of the built-in one")
    p.add_argument("--save", help="write the plan to this JSON file for editing")
    r = sub.add_parser("run", help="run every stage, unattended")
    r.add_argument("--out", required=True, help="run folder (reuse it to resume)")
    r.add_argument("--plan", help="plan JSON instead of the built-in one")
    r.add_argument("--only", help="comma-separated stage names")
    r.add_argument("--interface", default="gs_usb")
    r.add_argument("--channel", default="0")
    r.add_argument("--port", help="ESP32 J1708 bridge port, e.g. COM11 (J1587 stages; CAN stages also log J1708)")
    r.add_argument("--host", default="vnl-cam.local", help="the Tab5's address")
    r.add_argument("--no-camera", action="store_true", help="bus only")
    r.add_argument("--camera-wait", type=float, default=300, help="seconds to wait for the Tab5 before a stage")
    r.add_argument("--margin", type=float, default=0.5, help="seconds after a candidate a change still counts")
    r.add_argument("--delay", type=float, default=5, help="seconds to wait for windows.csv before matching")
    rp = sub.add_parser("report", help="rebuild review.html")
    rp.add_argument("out")
    args = ap.parse_args(argv)
    {"plan": cmd_plan, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
