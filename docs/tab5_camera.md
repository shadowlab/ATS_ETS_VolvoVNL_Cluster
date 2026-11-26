# Tab5 sweep camera

The M5Stack Tab5 watches the cluster during sweeps and reports changes over Wi-Fi. It replaces the
DroidCam camera; sweeps run with `sweep_watch.py --camera none`, and `tools/tab5_cam.py watch` logs the
camera's hits next to the bus watch.

## Why on-device detection

The Tab5 compares each frame with a baseline itself and sends small events (plus one picture per hit),
instead of streaming video for the PC to diff. Events don't depend on Wi-Fi throughput, survive dropouts
(the PC reconnects and the Tab5 resends what was missed), and keep the PC free. MJPEG is still served for
aiming the camera.

## Against glare

- **Exposure and gain are fixed.** No auto exposure or white balance runs. `tab5_cam.py exposure` sets them
  once for the regions and they are kept across restarts. The lens is fixed focus.
- **Only the regions count.** Everything outside them is ignored.
- **Needle regions** remove the region's overall brightness change before counting moved pixels, so an
  even reflection or room light change doesn't count. **Lamp regions** count changed pixels.
- **A ref region** on a part of the cluster that never changes: if it changes, that moment is glare and
  nothing is reported as a hit (logged as `glare`). Half or more of the regions changing together is
  treated the same way (`glare_frac`).
- **Persistence:** a region must stay changed for 3 frames (~0.2 s) in a row.
- **Drift:** quiet regions follow slow light changes (`adapt`). A change that lasts 3 s (`latch_s`) is
  logged as `latched` and becomes that region's new baseline, since lamps here latch.

## Setup

1. The C6 must run ESP-Hosted 3.0.9 (done once with tab5-p4-c6-sdio-ota, as for RetroESP).
2. Flash from CI: `tools\flash_tab5.ps1 -Port COM6` (newest `cam-build` release) or `-Pr <n>`.
   The summary shows `camera OK` and exits 0.
3. Wi-Fi (2.4 GHz only): on the Tab5 screen tap **Wi-Fi**, pick the network, type the password. Or over
   USB: `python tab5_cam.py setup --port COM6 --ssid "<network>" --password "<password>"`.
   The screen shows the address. The Tab5 is also `vnl-cam.local`.
4. Aim it with the preview on the Tab5 screen, or at `http://vnl-cam.local/` (live picture, region scores,
   events).
5. `python tab5_cam.py pick`: drag a box per gauge or lamp, name it, pick its kind; add one `ref` box.
6. With the cluster on and idle: `python tab5_cam.py exposure`.

## Screen

Live preview with the regions (green needle, cyan lamp, blue ref, red changed, yellow glare), Wi-Fi
state and address, fps, exposure, gain, event count. Buttons: Wi-Fi, New baseline, Auto exposure, Screen
off. The backlight goes off 2 minutes after the last touch, so it doesn't light the cluster, and the
baseline is retaken 2 s later. A touch turns it back on (that touch presses nothing). Set the regions with
the screen off, or after it goes off.

Mounting: fixed, no movement between the Tab5 and the cluster; shade the cluster face from windows and
overhead lights where possible. Keep the Tab5 on USB power.

## During a sweep

```
python tab5_cam.py watch --sweep sweeps/run1
python sweep_watch.py sweep --out sweeps/run1 --camera none ...   # second window
```

`watch` prints `CAM HIT #n <region> during <MID/SA> <PID/PGN> data ..` and writes `cam_events.csv`,
`cam_hits.csv` and `cam/hit_<n>.jpg` (baseline | changed frame) in the sweep folder. Times are PC epoch
seconds, the same clock as `windows.csv`: the Tab5's clock is mapped with the lowest-latency event seen.
A change can show just after a window ends; `--margin` (0.5 s) still credits that window.
`python tab5_cam.py match sweeps/run1 --margin 1` redoes the matching afterwards.

## Unattended sweep of everything

```
python auto_sweep.py plan                    # stages, candidates, time (~9 h plus recovery)
python auto_sweep.py run --out sweeps/auto1 --interface gs_usb --channel 0 --port COM11
```

Per stage: checks the Tab5 (waits up to 5 min, else goes on bus-only), new baseline, logs camera events,
runs `sweep_watch.py sweep --camera none` for the range, matches the changes to candidates. Ends with
`sweeps/auto1/review.html`: per stage, candidates that changed something (regions, picture) and bus hits.
Same command resumes; `--only` picks stages; `plan --save plan.json` + `--plan plan.json` to edit ranges.
Built-in plan: J1587 MIDs 80,90 and 82,88 PIDs 00-FD; CAN FF00-FFFF (SA 03,0B,11,21,27; SA 00 without
FF01/FF02, which reboot the cluster), FE00-FEFF (00,03,0B,11), F000-FDFF (00,11).

Lamps latch until power cycle. `--relay COMx`: LCUS-1 USB relay (CH340, 9600 baud), NC contact in the PSU
+12 V line ahead of the A:1 and A:3 fuses (relay idle = cluster on). The runner power cycles the cluster at
each stage start and when a region stays changed: sweep stopped, 3 s off (`--relay-off`), 20 s boot
(`--relay-boot`), new baseline, sweep resumed. Camera changes during a cycle are left out of the review.
Without the relay, a lamp lit early in a run can't show again later in it.

## Settings

`python tab5_cam.py config` shows them; `--set key=value` changes one (kept on the Tab5).

| Key | Default | Meaning |
|---|---|---|
| `pix_thr` | 20 | gray levels a pixel must change by |
| `persist` | 3 | frames in a row |
| `glare_frac` | 0.5 | share of regions changing together that counts as glare (3+ regions) |
| `latch_s` | 3 | seconds until a change becomes the new baseline |
| `adapt` | 0.02 | baseline drift per frame in quiet regions (0 = off) |
| `target` | 90 | mean gray level for `exposure` |
| per region `thr` | needle 0.03, lamp 0.10, ref 0.05 | share of the region's pixels that must change |

## Firmware

`tab5cam/`, ESP-IDF 5.5.2. Screen and touch: `tab5cam/components/tab5_board` (from RetroESP's Tab5
port, detects the panel revision), LVGL 9.2. SC202CS (SC2356) on MIPI-CSI at its full 1600x1200 (the widest view the fixed lens gives), processed as
800x600 gray.
HTTP API is listed at the top of `tab5cam/main/web.c`. CI: `.github/workflows/tab5cam.yml`; merges publish
`cam-build-<n>` releases with `vnl-tab5cam.bin` (write at 0x0). They are not marked latest, so the bridge's
`releases/latest` link is unchanged.
