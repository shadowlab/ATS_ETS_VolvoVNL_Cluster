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

## Confirmed on the bench

| Date | Bus | ID / MID-PID | Payload | Effect |
|---|---|---|---|---|
| | | | | |
