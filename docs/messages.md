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
| Fuel level, air pressures | Analog on connector C | 96, 117, 118 | Yes |
| Turn signals, high beam, other telltales | Unknown, likely proprietary | Unknown | Yes |

## Open questions

- What must the cluster hear from the VECU to stop showing the J1587
  communication error?
- Are turn signals and high beam hard-wired inputs on A/C, or messages?
- Do fuel and air pressure also accept network values, or only analog?

## Confirmed on the bench

| Date | Bus | ID / MID-PID | Payload | Effect |
|---|---|---|---|---|
| | | | | |
