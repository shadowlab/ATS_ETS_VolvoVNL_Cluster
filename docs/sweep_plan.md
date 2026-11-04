# Sweep plan

No real VN truck to record from, so messages are found by sweeping. J1939 uses
29-bit IDs, so sweep by structure, not blindly. Results go in
[messages.md](messages.md#confirmed-on-the-bench).

Tools: CANable 2.0 (python-can), the ESP32 J1708 bridge on COM11, the logic
analyzer, and `tools/sweep_watch.py` ([runbook](../tools/README.md#camera-sweep)).
Sweeps run the engine heartbeat first (on by default), run a bus check before
starting, and take `--announce` and the GPIO41 button for lamp changes
(`buttons.csv`).

## Status (2026-11-04)

| Step | State |
|---|---|
| 1. Listen | Done. Cluster is SA 0x17 / MID 140; both buses work both ways |
| 2. Standard J1939 PGNs | Done. The can_wake heartbeat moves tach, oil pressure and coolant and clears check engine. Speedo and odometer still dead |
| 3. CAN proprietary 0xFF00-0xFFFF | Not run |
| 4. J1708/J1587 | Partly done. Found the parking brake lamp and the "i" lamp (MID 144 PIDs 70, 71) |
| 5. CAN3 | Not needed so far |

## 3. CAN proprietary range (to run)

0xFF00-0xFFFF is 256 PGNs per source address. Run with the heartbeat, for the
speedo and any lamps:

    python sweep_watch.py sweep --interface gs_usb --channel 0 --camera none --announce --pgns FF00-FFFF --sources 27,00 --payloads 40

About 13 min. Then sources 03, 0B, 21, 31; avoid 17 (the cluster itself).
Also try all-FF and single-bit payloads.

## 4. J1708/J1587

Done, MID 128 (`80`) and 144 (`90`), data 00 and FF, PIDs 00-FD: only the
lamps above. MID 144 PIDs 00-7F with single-bit data 01-80: only PIDs 70-72
changed a lamp.

Left:

- MID 144 PIDs 80-BF (two-byte data) and C0-FD, single-bit fills.
- Which lamp each of `46 80`, `47 01/02/04/08`, `48 40/80` lights; what
  clears a latched lamp.
- Other MIDs: 0x82 transmission, 0x88 brakes, 0x8E, 0xD8 lighting, 0xE8 airbag.

    python sweep_watch.py sweep --transport j1708 --port COM11 --wake --interface gs_usb --channel 0 --camera none --announce --mids 90 --pids 80-BF --fills 01,02,04,08,10,20,40,80

MIDs and PIDs are hex. Lamps latch, so power-cycle the cluster between tests.
Check candidates from `buttons.csv` by sending them alone:
`python j1708_listen.py --port COM11 --send "90 46 FF" --send-period 0.1`.

## 5. CAN3

CAN3 goes to the radio and LECM. Skip unless something on the display needs
it; then find its bitrate (250 or 500 kbit/s) and sweep it the same way.

## Checking without a camera

- `la_capture.py capture` alongside, then `sweep_crosscheck.py --la <csv>`
  lists candidates that changed the cluster's own J1708 output.
- The sweep's own bus watch flags fault-report changes. Hits on PIDs 0x2C,
  0x2F, 0x36 and 0xC3 so far were the cluster's own fault counts and
  diagnostic replies, not controls.
