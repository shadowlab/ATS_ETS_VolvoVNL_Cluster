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

## Confirmed on the bench

| Date | Bus | Message | Effect |
|---|---|---|---|
| 2026-10-29 | J1708 | `80 80 F5 8C` and `80 00 F5` (MID 128 asks MID 140 for PID 245) | Cluster answers PID 245 every ~2.1 s instead of 10 s: sending on J1708 works |
| 2026-11-03 | CAN | `can_wake.py` heartbeat: EEC1 (0xF004, SA 0x00, prio 3, 20 ms), ET1, EFL/P1, DM1 (lamps off) from SA 0x00, VEP1 + CCVS1 from SA 0x27 | Tach, oil pressure and coolant gauges move, check engine lamp goes out. Tach follows EEC1 at 500, 1000, 1500, 2000, 3000 rpm. Speedo stays at 0 |
| 2026-11-03 | J1708 | `90 46 FF` (MID 144, PID 70, data FF) | Parking brake lamp on |
| 2026-11-03 | J1708 | `90 47 FF` (MID 144, PID 71, data FF) | White "i" lamp on |

Lamps stay on after the send stops. Send with the heartbeat running, every
0.1 s. Bit mapping for PIDs 70, 71 and 72 is still open: the single-bit sweep
showed lamp changes at `46 80`, `47 01`, `47 02`, `47 08` and `48 80` (which
lamp each: untested).

## Tried without effect

- CAN CCVS1 (0xFEF1) from every SA except 0x00 and 0x17, and from SA 0x00: speedo and odometer unchanged.
- CAN TCO1 (0xFE6C, SA 0xEE), EBC2 (0xFEBF, SA 0x0B), ETC1 (0xF002, SA 0x03), J1708 PID 84 from MID 128 and 144: speedo unchanged.
- VECU address sweep, SA 0x01-0xFD with `can_wake.py --vecu sweep`: SID 201 never changed.
- J1708 PID 195 clear-all `AC C3 03 8C 00 C0`: no response, codes unchanged. PID 195 with data `FF FF` from MID 128 or 144 gets a PID 196 reply (`<MID> 01 e2`); it does not clear the codes.
- J1708 sweeps, MID 128 and 144, PIDs 00-FD, data 00 and FF: only the lamps above. No needle response reported.
- J1708 sweep, MID 144, PIDs 00-7F, single-bit data 01-80: only PIDs 70-72 changed a lamp.
- Bus-watch hits on PIDs 0x2C, 0x2F, 0x36 and 0xC3 were the cluster's own fault counts and diagnostic replies.

## To try

- J1708 MID 144, PIDs 80-BF (two-byte data) and C0-FD, single-bit fills.
- Map which lamp each of `46 80`, `47 01/02/04/08` and `48 40/80` lights, and what clears a latched lamp (`00`?).
- Other MIDs, sent like MID 144: 0x82 transmission, 0x88 brakes, 0x8E, 0xD8 lighting, 0xE8 airbag.
- CAN proprietary sweep for the speedo, with the heartbeat running: `sweep_watch.py sweep --pgns FF00-FFFF --sources 27,00 --payloads 40` (~13 min).
- DM1 lamp bits from SA 0x00: byte 1 bits 8-7 MIL, 6-5 red stop, 4-3 amber, 2-1 protect (00 off, 01 on).
- Airbag lamp: MID 232 messages, if the lamp needs the SRS module.
- Ask the cluster for Component ID (PGN 0xFEEB) and Software ID (0xFEDA): `can_send.py --pgn EA00 --sa F9 --da 17 --data "EB FE 00" --period 0`.

## J1939 notes (from memory, not the paid spec)

| Item | Detail |
|---|---|
| Parking brake | CCVS1 (0xFEF1) SPN 70, byte 1 bits 4-3 (01 set). Not PGN 0xFE6E (wheel speed) |
| Road speed | CCVS1 bytes 2-3, 1/256 km/h |
| Key / battery | VEP1 (0xFEF7): SPN 168 bytes 5-6, SPN 158 keyswitch bytes 7-8, 0.05 V/bit (13.8 V = `14 01`) |
| Air pressure | AIR1 (0xFEAE) SPN 1087/1088, 8 kPa/bit; the cluster sends it itself because its air sensors are analog |
| Oil pressure | EFL/P1 (0xFEEF) byte 4, 4 kPa/bit |
| Extended frames | All CAN here is 29-bit; ESP32 CAN firmware must set `.extd = 1` |

From the Volvo P2 car cluster project (VolvoDIM): other CAN IDs do not carry
over, but sending every partner frame continuously and power-cycling the
cluster between tries do.
