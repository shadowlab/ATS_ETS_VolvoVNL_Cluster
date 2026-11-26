# VECU bench

Goal: log what a real VECU (P/N 21720493-P02, SA 0x11 / MID 144) sends to the
cluster while its speed-sensor input sees a square wave, then replay those
frames without it. Pins are from the Volvo VN/VHD US07 wiring diagram
(doc 20758328), VECU = A17, sheets AE-AH. Each wire carries its printed
circuit code; match the pigtail wires by that code.

## VECU pins

| VECU pin | Wire code | Function | Bench |
|---|---|---|---|
| A:13 | F71A2 | +12 V battery (fuse F71, 10 A) | bench +12 V through a 10 A fuse |
| A:12 | X03EA28 | Ground | bench ground (same as cluster A:2) |
| A:7 | F15F1 | Key-switch feed (F15) | key +12 V, same switch as cluster A:3 |
| A:14 | F15D2 | Key-switch feed (F15) | key +12 V, same switch as cluster A:3 |
| C:4 | J1939HN1 | J1939 CAN-H | cluster A:15 (CAN1 H) |
| C:5 | J1939LN1 | J1939 CAN-L | cluster A:16 (CAN1 L) |
| C:2 | J1587HN7 | J1587 + | cluster A:17 (J1708 +) |
| C:1 | J1587LN7 | J1587 − | cluster A:18 (J1708 −) |
| B:6 | CB6A1 | Vehicle speed sensor + | ESP32 GPIO7 through 1 kΩ + 1 µF (below) |
| B:20 | CB20A1 | Vehicle speed sensor − | bench ground |
| B:15 | MB15A1 | EMS relay coil output | leave open |
| A:6 | F15E4 | Key feed for Allison/I-Shift option | leave open |

Connector C is the small middle plug (the yellow/green pairs). The CANable and
the ESP32 bridge stay on the same CAN1 and J1708 lines as now, and the CAN1
bus keeps its two 120 Ω terminations.

## Speed-sensor drive

The input is a magnetic-pickup pair (B:6/B:20, twisted). The ESP32 bridge puts
a 3.3 V, 50% square wave on GPIO7 when the PC asks (firmware `FE 7E` command).

    GPIO7 ── 1 kΩ ── 1 µF ── VECU B:6
    ESP32 GND ─────────────── VECU B:20 (and bench ground)

Rough scale: ~27,000 pulses per mile (16-tooth tone wheel, 3.36 axle,
500 rev/mile tires), so ~370 Hz at 50 mph. The VECU's own pulses-per-mile
setting decides the real scale. If nothing reacts, swap B:6 and B:20.

## Logging run

Flash the bridge with this PR's build first (`.\flash_ci.ps1 -Port COM11 -Pr 18`).
Two windows, both logging with wall-clock times so the CSVs line up:

    python can_wake.py --interface gs_usb --channel 0 --vecu none --rpm 700 --log vecu_can.csv
    python j1708_listen.py --port COM11 --log vecu_j1708.csv --vss "0,50,100,200,400,600,800,0" --vss-hold 20

`--vecu none` keeps only the engine frames (SA 00), so the real VECU is the
only one sending VECU messages. Press the GPIO41 button when the speedo moves;
the press lands in vecu_j1708.csv. Repeat with `--rpm 0` to see whether the
VECU only reports speed with the engine running.

## After the log

- Frames from SA 11 / MID 144 that only appear, or change, after the VSS step
  are the speed candidates.
- Replay them with the VECU unplugged: `can_wake.py --vecu none --extra PGN:11:DATA`
  and `j1708_listen.py --send "90 ..."`.
