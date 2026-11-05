# Stalk controls and cluster diagnostics

The display stalk on the right of the steering column drives the cluster's
Driver Information Display (DID): menus, cluster self test, fault codes and
part numbers.

## Known

| Fact | Source |
|---|---|
| Stalk buttons are four discrete wires, one per button: A:4 Enter (Select), A:5 Esc, A:6 Up, A:7 Down, each switching +12 V into the cluster | Wiring diagram 21849527 sheet BA ([pinout.md](pinout.md)); harness has all four ([harness.md](harness.md)) |
| Not a resistor ladder and not a data message: the cluster logs a fault per button | MID 140 DTC guide: PSID 47 Escape, 48 Enter, 49 Up, 50 Down |
| Holding any stalk button for more than 60 s stores PSID 47-50 FMI 12 | MID 140 DTC guide |
| Steering wheel buttons (YES, NO, left, right, volume +/−) come in on C:22 / C:25 / C:30 through the contact reel. The cluster powers them and reads a PWM signal | Diagram sheet BB; DTC guide PSID 39-44 (buttons) and PSID 45 FMI 3/5/6/9 (supply > 16 V, PWM low/high/missing) |
| Stalk buttons: Esc = back/cancel, Enter = open menu/select, Up/Down = scroll or change a value | DID manual PV776-21012748 (07.2009) |
| Diagnostics live in the Non-Driving (stationary) menu, which opens only when the truck is "parked" | DID manual p. 16 |
| Diagnostics menu: **Fault Diagnostics** (list of control units, pick one for its faults), **Cluster Self Test** (Telltales, Gauge, Display, Speaker), **Part Number** (part number per control unit) | DID manual p. 71-75 |
| Self tests: Telltales light about 5 s; Gauge test drives every needle between its end stops; Display test lights the whole LCD until Esc; Speaker test plays each sound and names it. Esc cancels any test | DID manual p. 73 |
| Password-protected items (key symbol): Fleet Limits, Fleet ID, Reset. 4 digits, default 0000, entered with Up/Down + Enter | DID manual |
| Driving menu "Vehicle Messages" shows active faults | DID manual |

## Inferred, not yet checked

- Stalk inputs are active-high with a pull-down inside the cluster, so an
  idle pin reads near 0 V. Check with a meter before wiring buttons.
- "Parked" most likely means road speed 0 plus parking brake set, from
  CCVS1 (VECU, SA 0x27 on our bench) or J1587 PID 70 from MID 144
  (`90 46 80`). `can_wake.py` sends parking brake *released* by default;
  `--park` sets it.
- Gauge test should move the speedometer even though no speed message has
  moved it yet, which would show whether the motor and pointer work.
- Older VNL clusters drop the engine data link during self test and need a
  key cycle (A:3) afterwards for the needles to follow data again.
- The steering wheel buttons may instead go through the steering wheel
  module (MID 250, CAN3) on this build. Not needed for the stalk.

## Bench test

Cluster on A:1, A:2, A:3, B:23; CAN1 and J1708 connected.

1. Meter, cluster on, nothing on A:4-A:7: measure each pin to A:2. Near 0 V:
   go on. Near 12 V: stop, the inputs are active-low; report the reading.
2. Wire four momentary push buttons, each from the +12 V rail through a
   1 kΩ resistor to A:4 (Enter), A:5 (Esc), A:6 (Up), A:7 (Down). Never hold
   one longer than 60 s.
3. Start the heartbeat, parked, and log J1708 in a second window:
   ```
   python can_wake.py --interface gs_usb --channel 0 --park
   python j1708_listen.py --port COM11 --log stalk.csv
   ```
4. Press Enter. Expect the menu list. Scroll with Up/Down to the
   Non-Driving menu (or the stationary menus) and open Diagnostics.
   - No Non-Driving menu: add `--send "90 46 80" --send-period 1` to the
     `j1708_listen.py` command and try again. Still nothing: try without
     `--park`. Note which combination opened it.
5. Cluster Self Test, one test at a time: Gauge, Telltales, Display,
   Speaker. Note which needles move (speedometer especially), which lamps
   light, and the sound names. Cycle A:3 afterwards if the needles stop
   following the heartbeat.
6. Fault Diagnostics: note the list of control units, then open Instrument
   Cluster (and Engine, Vehicle ECU) and write down every code shown.
   Compare with the codes the cluster sends on J1708 (PSID 200, 201, 235).
7. Part Number: note the list and the cluster's own part and software
   numbers.
8. Stop the logger, keep `stalk.csv`; note the time you opened Fault
   Diagnostics and Part Number so the requests can be found in the log.

## Tried

- 2026-11-05: pressing Enter (heartbeat with `--park`, no J1708 sent by us)
  shows "J1587 Communication Error with Vehicle ECU". Enter/Esc dismisses it
  and the menus, including Diagnostics and Cluster Self Test, open. J1708
  log over two presses (enter.csv): the cluster sends nothing new, only its
  usual 1 s / 10 s frames. So the pop-up comes from the stored SID 201 FMI 12
  (VECU lost), not from an unanswered request. It goes when SID 201 clears.

- 2026-11-05, Cluster Self Test: Gauge test moves every needle, speedometer
  included, so the speedo motor works and only its message is missing.
  Telltales test lights every lamp, including a DEF lamp
  ([photo](images/telltale_test.png)).
- Fault Diagnostics queries the other control units; with none on the bench,
  each check fails. No part or software number found in the menus.

- 2026-11-05, Fault Diagnostics request log (faults.csv, faults_can.csv).
  Per module the cluster sends a J1587 PID 128 request for PID 194
  (diagnostic codes), `8C 80 C2 <MID>`, every ~1.1 s, 10 times, then gives
  up. Engine faults come from J1939 instead: one Request (0xEA00, SA 17 to
  SA 00) for DM1 (0xFECA).

  | Module | Bus | Request |
  |---|---|---|
  | 128 engine | J1939 | `18EA0017 CA FE 00` |
  | 130 transmission | J1587 | `8C 80 C2 82` |
  | 136 ABS | J1587 | `8C 80 C2 88` |
  | 144 vehicle ECU | J1587 | `8C 80 C2 90` |
  | 146 climate | J1587 | `8C 80 C2 92` |
  | 216 lighting (LCM) | J1587 | `8C 80 C2 D8` |
  | 232 airbag | J1587 | `8C 80 C2 E8` |
  | 223 | J1587 | `8C 80 C2 DF` |

- 2026-11-05, answering as MID 144. The exchange has two steps: plain
  request `8C 80 C2 90`, then a Volvo-wrapped request
  `8C FE 90 04 D4 80 C2 90` (PID 254 to MID 144, `D4`, then the J1587 body).
  The cluster sends the wrapped one only after it hears a plain PID 194 from
  MID 144.

  | Sent (every 0.5 s) | Result |
  |---|---|
  | `90 C2 00` | Wrapped request follows, then "Operation Failed" |
  | `90 FE 8C 03 D4 C2 00` | Plain request x10, "Operation Failed" |
  | `90 FE 8C 05 D4 C2 02 E7 1C` | Plain request x10, "Operation Failed" |
  | `90 C2 00` + `90 FE 8C 03 D4 C2 00` | One plain + one wrapped request, **"No Faults"** |

  The wrapped reply uses the same form as the cluster's own fault list to
  MID 127 (`8C FE 7F 0C D4 C2 09 <codes>`).

## Left

- Send a code in the wrapped reply (`90 C2 00` + `90 FE 8C 05 D4 C2 02 E7 1C`,
  SID 231 FMI 12) to confirm codes show on screen.
- Same two replies for the other modules (MIDs 130, 136, 146, 216, 223, 232).
- Whether answering clears SID 201 or the menu pop-up (not yet: SID 201 is
  "device lost" on J1939 per bulletin 371-44).

- Speedometer: find the message that drives it (motor proven by Gauge test).
- DEF lamp: find what lights it (low-DEF threshold or a DM1 lamp).
- Map the lamps in the telltale photo to messages ([messages.md](messages.md)).
- Steering wheel buttons on C:22 / C:25 / C:30: PWM coding unknown; low
  priority.

## Sources

- Volvo Driver Information Display manual, PV776-21012748 (07.2009):
  <https://www.truckspares365.co.uk/hub/wp-content/uploads/2022/08/Volvo-driver-information-display-manual.pdf>
- Volvo Instrument Cluster MID 140 DTC guide (from build date 1.2007):
  <https://www.truckspares365.co.uk/hub/wp-content/uploads/2022/08/Volvo-Instrument-Cluster-Control-Module-MID-140-Diagnostic-Trouble-Code-DTC-Guide.pdf>
- Older VNL service manual, Group 38, cluster self-test (Mode/Set buttons,
  pre-DID cluster): <https://www.manualslib.com/manual/903599/Volvo-Vnl.html?page=27>
