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
it. Next test: send from a candidate VECU source address and watch SID 201
change from FMI 12 to FMI 9 or go inactive. 0x27 is a guess for the VECU's
J1939 address (not from the bulletin); the sweep's 0x21 and 0x31 are others.

## Confirmed on the bench

| Date | Bus | ID / MID-PID | Payload | Effect |
|---|---|---|---|---|
| 2026-10-29 | J1708 | MID 128 request for PID 245 from MID 140: `80 80 F5 8C`, and `80 00 F5` | request | The cluster answers with PID 245 about every 2.1 s instead of every 10 s, so sending on J1708 works end to end |

### Tried without effect

- CAN: EEC1 (0xF004, SA 0x00), 1000 rpm, 100 Hz. Tach and other needles stayed at rest, also across a key off/on.
- J1708: MID 128 PID 190 engine speed, PID 84 road speed, PID 98 oil level, PID 250 total fuel, and MID 144 PID 84, together with the CAN frame above. No visible change and no change in the cluster's fault list.
