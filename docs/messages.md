# Messages

Starting candidates for each gauge. Engine gauges should follow standard SAE
signals, sent on J1939 from the engine (source address 0x00) and on J1587 as
MID 128. Which bus the cluster trusts for each gauge is found by testing.

PGN/SPN and PID numbers are standard SAE assignments from memory, not checked
against the paid SAE documents.

| Gauge or light | J1939 (PGN, SPN) | J1587 PID | In ATS telemetry |
|---|---|---|---|
| Tachometer | EEC1 61444 (0xF004), SPN 190 | 190 | Yes |
| Speedometer | CCVS1 65265 (0xFEF1), SPN 84 | 84 | Yes |
| Coolant temperature | ET1 65262 (0xFEEE), SPN 110 | 110 | Yes |
| Oil pressure | EFL/P1 65263 (0xFEEF), SPN 100 | 100 | Yes |
| Battery voltage | VEP1 65271 (0xFEF7), SPN 168 | 168 | Yes |
| Odometer | VDHR 65217 (0xFEC1), SPN 917 | 245 | Yes |
| DEF level | AT1T1I 65110 (0xFE56), SPN 1761 | n/a | Yes (AdBlue) |
| Parking brake, cruise | CCVS1 65265 | n/a | Yes |
| Check engine / stop lamps | DM1 65226 (0xFECA) lamp bits | n/a | Partly |
| Fuel level, air pressures | Analog on connector B | 96, 117, 118 | Yes |
| Airbag (SRS) lamp | n/a: the SRS module (A15) is on J1587 only | MID 232, PIDs unknown | No |
| Turn signals, high beam, other telltales | Unknown, likely proprietary | Unknown | Yes |

## Open questions

- What must the cluster hear from the VECU to stop showing the J1587
  communication error?
- Turn signals and high beam have no hard-wired input on A/B/C, so they are
  messages (the wiring diagram shows the Light Control Module driving the
  lamps directly). The one open wire is A:21, an LCM output, possibly dimming.
- Does the cluster light the airbag lamp when it hears nothing from the SRS
  module (MID 232)? If so, the bridge has to fake MID 232 as well as the VECU.
- Do fuel and air pressure also accept network values, or only analog?

## Heard from the cluster

2026-10-29, first power-up: key (A:3) and battery (A:1) on, CANable only on
CAN1, 250 kbit/s. The cluster claims source address 0x17 (Address Claimed
NAME: manufacturer 60, function 1, vehicle system 24, industry group 2) and
sends these on its own, about once a second unless noted. It never asked for
any PGN, so nothing here says what it wants to hear.

| PGN | Name | Last payload | Reading |
|---|---|---|---|
| 0xE000 | unknown | `ff ff ff 3f ff f0 ff ff` | Unidentified |
| 0xFE58 | unknown | `ff ff ff ff fe fe fe fe` | Mostly not-available |
| 0xFEAE | AIR1 | `fe ff fe fe ff ff ff ff` | Not available |
| 0xFEC1 | VDHR | `6c 8a b1 0a ff ff ff ff` | 0x0AB18A6C x 5 m, about 897,000 km: the cluster's own odometer |
| 0xFEE6 | TD | `d4 21 11 0a 74 29 5f 7c` | 2026-10-29 17:33:53, the cluster's clock |
| 0xFEF5 | AMB | `ff ff ff fe fe ff ff ff` | Not available |
| 0xFEFC | DD | `ff fe ff ff ff ff ff ff` | Not available (fuel level, SPN 96, is byte 2) |
| 0xFF27 | Proprietary B | `ff ff ff ff ff ff ff ff` | Unused so far |
| 0xFF28 | Proprietary B | `fc f0 fe ff ff ff ff ff` | Every 424 ms, so twice the rate of the rest; the first thing to watch while sweeping |

Sending EEC1 (0xF004, SA 0x00, 1000 rpm) at 100 Hz did not move the tach, so
that alone isn't enough.

### On J1708 (MID 140)

Same session, through the ESP32 bridge. Frames are MID + data + checksum.

| Rate | Frame | Reading |
|---|---|---|
| ~1.1 s | PID 192 twice: `c2 10 15 75 a4 03 ab a5 03 54 a9 03 76 a4 03 b1 a9` and `c2 11 03 60 a5 03 4d a5 03` | Multisection (sections 1 and 2) carrying PID 194, the cluster's own diagnostic codes. Decoded below. It never changed while we sent engine data. |
| 10 s | PID 245 `2b 0d 55 00` | Odometer, 0x00550D2B, about 557,000 mi |
| 10 s | PID 251 and 252 | Clock and date |
| 10 s | PID 128 request: MID 128, PID 250 | Asks the engine for total fuel used |
| key-on | PID 128 request: MID 128, PID 98 | Asks the engine for oil level, seven times, then stops |
| 10 s | PID 254 to MID 128: `d4 80 69 80`, `d4 80 6b 80`, `d4 80 6c 80` | Proprietary polls of the engine with a changing third byte |
| 10 s | PID 254 to MID 127, 15 bytes: `d4 c2 0c c8 b9 04 16 89 03 c9 bc 01 eb bc 01` | Carries PID 194: the cluster's data link faults. Decoded below |
| once | PID 254 to MID 142: `d4 d9 05 65 6e 67 47 42` | Ends in ASCII "engGB", maybe the display language |

PID 254 frames are `fe`, destination MID, length, data, in the frames seen so far.

### The cluster's fault codes, decoded

PID 194 codes are an id byte, a code byte, and an occurrence count. Code byte
bits: 8 count included, 7 inactive, 6 standard (clear means a page 2 PID),
5 SID rather than PID, 4-1 FMI. SIDs 200-250 are Volvo data link codes from
bulletin 371-44, see [data_links.md](data_links.md). All are active.

PID 192 list (sensors and data the cluster is missing):

| Code | Meaning |
|---|---|
| PID 117 FMI 4 | Primary air pressure, voltage low (sensor not wired) |
| PID 171 FMI 5 | Ambient air temperature, open circuit |
| PID 84 FMI 9 | Road speed, abnormal update rate: expected over a data link |
| PID 118 FMI 4 | Secondary air pressure, voltage low |
| PID 177 FMI 9 | Transmission oil temperature, abnormal update rate |
| PID 96 FMI 5 | Fuel level, open circuit |
| PID 77 FMI 5 | Forward rear drive axle temperature, open circuit |

PID 254 to MID 127 (data link faults):

| Code | Meaning |
|---|---|
| SID 200 FMI 9, x4 | Engine ECU (MID 128): heard, but needed data missing |
| PID 278 (page 2 PID 22) FMI 9, x3 | Unknown, probably Volvo proprietary |
| SID 201 FMI 12, x1 | Vehicle ECU (MID 144): not heard at all |
| SID 235 FMI 12, x1 | Unknown module lost; not in the bulletin |

So the cluster sees something from the engine, but nothing from the VECU.
The bulletin says the cluster uses the VECU's J1939 messages, and on a Volvo
the key switch goes to the VECU, so the parked needles may be waiting for
it.

### Next test: find the VECU's address

Pass/fail signal: the cluster's PID 254 frame to MID 127, which
`j1708_listen.py` now decodes. If SID 201 changes from FMI 12 (device lost)
to FMI 9 (heard, data missing) or goes inactive, the cluster has heard its
VECU. It repeats every 10 s, so give each candidate about 30 s.

Lab found notes (untested, source unknown) that agree with this and give
candidates:

- The VECU ("VMCU") sends from SA 0x21 or 0x27, and streaming CCVS1
  (PGN 65265, 0xFEF1) from it tells the cluster the truck is on.
- A transmission heartbeat, ETC1 (PGN 61442, 0xF002, id 0x0CF00203 =
  priority 3, SA 0x03), settles transmission faults. SID 235 FMI 12 might be
  that.
- EEC1 (0xF004) every 10-20 ms; slower data such as ET1 (0xFEEE, 65262,
  engine temperature) every 100-500 ms. The J1939 standard rate for EEC1 is
  20 ms (we used 10 ms), and 1 s for ET1.

Runs, one at a time, with `j1708_listen.py` logging alongside. CCVS1 here is
50 km/h (bytes 2-3, 1/256 km/h per bit); the rest is FF.

| Run | Command (`can_send.py --channel COM5 ...`) | Watch for |
|---|---|---|
| 1 | `--pgn FEF1 --sa 27 --data "FF 00 32" --period 0.1` | SID 201 leaves FMI 12; any needle or speedo move |
| 2 | Same with `--sa 21` | Same |
| 3 | Same with `--sa 31` | Same |
| 4 | `--pgn F002 --sa 03 --priority 3 --period 0.01` | SID 235 or PID 177 changes |

If a run changes SID 201, keep that source address and add EEC1 from SA
0x00 at the same time to see if the tach moves. `can_send.py` sends one PGN
per run, and only one program can open the CANable, so that step needs
several PGNs from one program (a small tool change, or the ESP32). If no run
changes SID 201, the camera sweep of FF00-FFFF from these addresses is next,
watching SID 201 as well as the camera.

### Lamp tests, once the VECU address is known

More of Lab's untested notes, checked against the J1939 layouts as we know
them (from memory, not the paid spec):

- **DM1 (PGN 65226, 0xFECA) drives the MIL and warning lamps.** Plausible:
  byte 1 is the lamp status, two bits each: bits 8-7 MIL, 6-5 red stop,
  4-3 amber warning, 2-1 protect (00 off, 01 on). Byte 2 is the matching
  flash bits (FF = not available). Bytes 3-6 hold one fault code. Every
  module sends its own DM1, so the engine (SA 0x00) is the first source to
  try.
- **Parking brake comes from CCVS1 SPN 70.** The notes say PGN 65134,
  byte 1 bits 5-6. Corrected: CCVS1 is PGN 65265 (0xFEF1); 65134 (0xFE6E) is
  high resolution wheel speed. SPN 70 is byte 1 bits 4-3 (00 released,
  01 set); bits 6-5 are cruise pause. The notes and J1939 agree that 01 means
  set.

The cluster already shows STOP, check engine and battery lamps with nothing
sent, so first try turning lamps off, then on.

| Lamp | Command (`can_send.py --channel COM5 ...`) | Then |
|---|---|---|
| All engine lamps off | `--pgn FECA --sa 00 --data "00 FF 00 00 00 00 FF FF" --period 1` | STOP and check engine go out? |
| MIL on | Same with `--data "40 FF 00 00 00 00 FF FF"` | Check engine lamp |
| Amber warning on | `"04 FF ..."` | |
| Red stop on | `"10 FF ..."` | |
| Parking brake set | `--pgn FEF1 --sa <VECU> --data "F7 00 32" --period 0.1` | Parking brake lamp on |
| Parking brake released | Same with `"F3 00 32"` | Lamp off |

`F7`: SPN 70 = 01, the other byte 1 fields not available. `00 32` is still
50 km/h.

### Power-up sequence and sweep IDs (Lab's notes, checked)

A third set of untested notes describes a wake-up sequence and sweep IDs.
Checked against the bench and the J1939 layouts (from memory):

| Claim in the notes | Check |
|---|---|
| With battery only, CAN is silent until the VMCU wakes the cluster | Not on our bench: with A:1 + A:3 the cluster talks on CAN1 and J1708 on its own |
| VEP1 (65271, 0xFEF7) from SA 0x21, id 0x18FEF721, byte 1 carries ignition state | PGN and id are right. Byte 1 is net battery current. The useful field is SPN 158, keyswitch battery potential, bytes 7-8 (0.05 V/bit); battery potential SPN 168 is bytes 5-6. Worth sending: it may be how the cluster learns the key is on |
| Start engine and VMCU frames within 50 ms or the cluster aborts with a red datalink fault | No source, and we have never seen such a screen. Treat as unverified |
| Tach: EEC1, id 0x0CF00400, bytes 4-5, 0.125 rpm/bit, every 20 ms | Matches what we sent, except we sent priority 6 (id 0x18F00400) every 10 ms. Priority 3 at 20 ms is the standard and cheap to try |
| Speed: PGN 65134, id 0x0CFE6E21, bytes 2-3, 1/256 km/h | 65134 (0xFE6E) is high resolution wheel speed. Road speed is CCVS1 (65265, 0xFEF1) bytes 2-3, 1/256 km/h, normally id 0x18FEF1xx. 40000 raw is 156 km/h (97 mph); the notes say MPH but the unit is km/h |
| Air tanks: PGN 65252, id 0x0CFEE40B, bytes 1-2, 8 kPa/bit, 0 to 250 | 65252 (0xFEE4) is Shutdown, not air. Air is AIR1 (65198, 0xFEAE): SPN 1087/1088 service brake circuits 1 and 2 in bytes 3-4, 8 kPa/bit. 250 raw is 2000 kPa, far over a real tank (about 0-150 psi, raw 0-130). Also, the cluster sends AIR1 itself (seen above) because the air sensors are wired to connector B, so it probably won't take air pressure from the bus |
| Use 29-bit extended frames | Right. Every tool here sends extended frames (`is_extended_id=True`); the ESP32 firmware doesn't send CAN yet, so stage 2 must set `.extd = 1` |

#### Tests that don't need the VECU address

| Test | Command (`can_send.py --channel COM5 ...`) |
|---|---|
| EEC1 at standard priority and rate, 1000 rpm | `--pgn F004 --sa 00 --priority 3 --data "FF FF FF 40 1F" --period 0.02` |
| Engine lamps off (DM1) | see the lamp table above |

The VEP1 key-on test needs a source address, so it joins the VECU search:
`--pgn FEF7 --sa 27 --data "FF FF FF FF 14 01 14 01" --period 0.1` (13.8 V
battery and keyswitch), then `--sa 21`, watching SID 201 as before.

### Boot handshake notes (Lab's notes, checked)

Another untested set describes a three-phase boot. Most of it repeats the
claims above; what's new:

| Frame in the notes | Check |
|---|---|
| VEP1 0x18FEF721, `FF FF FF FF FF FF 5C 1D` | Right PGN and field (keyswitch potential, bytes 7-8), wrong value: 0x1D5C x 0.05 V = 376 V. 13.8 V is `14 01` |
| Request 0x18EAEC21 for PGN 65024 (`00 FE 00`) to check the cluster is alive | 0xEC is not the cluster, which is SA 0x17 (0x8C is its J1587 MID 140). 65024 (0xFE00) is not Component ID; that is 65259 (0xFEEB). Asking the cluster for 0xFEEB and Software ID 0xFEDA is a harmless test that may give its part number and software version |
| EEC1 0x0CF00400, `F0 7D 83 00 00 00 F0 00` | Realistic: torque mode, 0 % driver demand (0x7D), 6 % actual torque, 0 rpm, engine as controlling source. We sent FF in those fields, so retrying 1000 rpm with them filled is worth it |
| Speed on 0x0CFE6E21 (PGN 65134), all 00 except byte 5 = 0x3F | Same PGN mistake as before; CCVS1 (0xFEF1) instead. All 00 in CCVS1 would mean speed 0, parking brake released |
| Air on 65252, oil on 65262, red screen if missing in 100 ms | 65252 is Shutdown and 65262 is ET1 (engine temperature). Oil pressure is EFL/P1 (65263, 0xFEEF) byte 4, 4 kPa/bit. Air is wired to the cluster. No support for the 100 ms claim |
| DM1 0x18FECA00, `00 FF 00 00 00 00 00 00` every 1 s | Right: byte 1 = 00 is all lamps off, byte 2 FF. A no-fault DM1 normally ends in `FF FF`. Same as the lamp-off test above |

#### Scripted wake: `can_wake.py`

`tools/can_wake.py` turns this into one test: a few seconds of silence, then
everything at once at the usual J1939 rates: VEP1 (13.8 V battery and
keyswitch) and CCVS1 from the VECU address, EEC1 (priority 3, 20 ms, torque
fields filled), ET1, EFL/P1 and a lamps-off DM1 from the engine. With several
VECU addresses it tries each in turn:

    python can_wake.py --channel COM5 --vecu 27,21,31 --dwell 30 --rpm 1000 --kmh 50

Run `j1708_listen.py` alongside and watch SID 201, the needles and the lamps.
Requesting Component ID is a separate one-off:

    python can_send.py --channel COM5 --pgn EA00 --sa F9 --da 17 --data "EB FE 00" --period 0

## Confirmed on the bench

| Date | Bus | ID / MID-PID | Payload | Effect |
|---|---|---|---|---|
| 2026-10-29 | J1708 | MID 128 request for PID 245 from MID 140: `80 80 F5 8C`, and `80 00 F5` | request | The cluster answers with PID 245 about every 2.1 s instead of every 10 s, so sending on J1708 works end to end |

### Tried without effect

- CAN: EEC1 (0xF004, SA 0x00), 1000 rpm, 100 Hz. Tach and other needles stayed at rest, also across a key off/on.
- J1708: MID 128 PID 190 engine speed, PID 84 road speed, PID 98 oil level, PID 250 total fuel, and MID 144 PID 84, together with the CAN frame above. No visible change and no change in the cluster's fault list.
