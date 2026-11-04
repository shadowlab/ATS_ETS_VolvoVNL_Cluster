# Analog gauges

Inputs for DEF level, fuel level and the two brake air gauges. Pins are from
[pinout.md](pinout.md); drive values are still to be measured.

## Known

| Gauge | Input | Pins (connector B) | Sensor type | Cluster fault when unwired |
|---|---|---|---|---|
| DEF level | **No wire.** J1939 AT1T1I, PGN 65110 (0xFE56), SPN 1761, % | none | Network | none seen |
| Fuel level | Sender B07 | B:3 signal, B:1 (or A:12) sensor ground | Resistive, to sensor ground | PID 96 FMI 5 (open circuit) |
| Front air (secondary, B-tank, front brakes) | Sensor B06A | B:13 signal, B:20 supply, B:1 ground | 3-wire, voltage out | PID 118 FMI 4 (voltage low) |
| Rear air (primary, A-tank, rear brakes) | Sensor B06B | B:14 signal, B:21 supply, B:1 ground | 3-wire, voltage out | PID 117 FMI 4 (voltage low) |

- The cluster sends AIR1 (PGN 65198) itself, so air comes from the wired
  sensors, not the bus.
- FMI 4 with nothing on the pin means the air signal input is pulled low.
  FMI 5 on fuel means B:3 is pulled up and reads "open" with no sender.
- Air supply pins measured: B:20 (front) and B:21 (rear); signals B:13 and
  B:14. Supply voltage not yet recorded.
- Expected but unconfirmed: air sensors are ratiometric 0.5 to 4.5 V on a 5 V
  supply, 0 to 150 psi. Fuel sender range is unknown (common heavy-truck
  ranges are 240-33 Ω and 0-90 Ω); measure it.
- The LCD shows "Low System Air Pressure" until both air inputs read above
  the warning threshold.

## Drive hardware

DEF needs no channel. That leaves three analog channels, so one MCP4728
covers them with one spare (brake application or a suspension gauge).

| Channel | Gauge | Drive |
|---|---|---|
| A | Front air, B:13 | DAC voltage straight to the signal pin |
| B | Rear air, B:14 | DAC voltage straight to the signal pin |
| C | Fuel, B:3 | DAC voltage, through a sink buffer if direct drive fails |
| D | Spare | |

**Air: MCP4728 direct.** The inputs read a voltage, which is what the DAC
outputs.

**Fuel: a voltage source can stand in for the sender.** The cluster pulls B:3
up and reads the voltage at the pin; a sender to ground just sets that
voltage. If the DAC holds B:3 at the voltage the sender would make, the
cluster sees the same reading. Two cases break this:

- B:3 open-circuit voltage above 5 V (pull-up to 12 V or 8 V). The DAC can't
  reach the "empty" end, and the pin would push current into the DAC.
- A pulsed pull-up (some clusters only power the sender while sampling). A
  static voltage then reads as a fault.

If either shows up, use a DAC-driven sink: op-amp (MCP6002 or LM358) with
the DAC on +in, B:3 on −in, output to the gate of a 2N7002 whose drain is on
B:3 and source on sensor ground. It only sinks, so it is safe at any pull-up
voltage, and it holds B:3 at the DAC voltage.

**Digital pots are a poor fit for fuel.** MCP4261/AD5254 have 75 to 150 Ω of
wiper resistance and a few mA of current, so they can't reach the low end
of a 33 to 240 Ω or 0 to 90 Ω sender, and their pins must stay inside their
5 V supply. X9C parts have the same problem. They only make sense if the
sender range turns out to be in the kΩ.

**Switched resistor ladder** (relays or MOSFETs across a few fixed
resistors) works at any voltage and range but gives only a few needle
positions. Keep it as a fallback.

### MCP4728 wiring

- VDD = 5 V. At 3.3 V the outputs top out at 3.3 V, about 105 psi on a
  0.5 to 4.5 V sensor, below normal tank pressure.
- I2C: at VDD 5 V the MCP4728 wants SDA/SCL high above 0.7 × VDD = 3.5 V,
  so the ESP32-S3's 3.3 V I2C needs a level shifter (BSS138 module), with
  pull-ups to 5 V on the DAC side.
- Reference: VDD (ratiometric) if powered from the cluster's 5 V sensor
  supply, or the internal 4.096 V reference (gain 2) on a separate 5 V
  supply. The internal reference caps output at 4.096 V, below the top of a
  0.5 to 4.5 V sensor, so use VDD as reference for the full 180 psi face.
- If 180 psi needs more than about 4.9 V: rail-to-rail op-amp gain stage
  (MCP6002) on 12 V after the DAC, or a 0 to 10 V DAC module (GP8403). Check
  first where a voltage-high fault (FMI 3) sets; it may cap the needle.
- DAC ground to sensor ground (B:1). Tie ESP32 ground to cluster ground
  (A:2) as on the bench now.
- 1 kΩ in series with each air output protects it. Leave the fuel output
  without one, or calibrate with it in place.

## Bench procedure

Cluster powered (A:1, A:3, B:23) with `can_wake.py` running so the needles
are live. Meter negative on B:1. Watch the needles and the cluster's fault
list (PID 194) with `j1708_listen.py`.

1. **Air supply pin.** Done: B:20 / B:21 supply, B:13 / B:14 signal. Still
   to record: the exact supply voltage.
2. **Air mapping.** 10 kΩ pot across supply and B:1, wiper to the front
   signal pin through 1 kΩ. Step 0 to supply in 0.25 V steps. Record needle
   psi, the voltage where PID 118 FMI 4 clears, where any high fault (FMI 3)
   sets, and where "Low System Air Pressure" clears. Repeat on the rear
   pair (PID 117).
   Decade box (0 to 9999 Ω) instead of the pot: 1 kΩ from supply to signal,
   box from signal to B:1; V = Vs × R / (1k + R) (111 Ω ≈ 0.5 V, 1 kΩ ≈
   2.5 V, 9 kΩ ≈ 4.5 V at 5 V). For 4.5 V to supply, swap: 1 kΩ from signal
   to B:1, box from supply to signal (111 Ω ≈ 4.5 V, 263 Ω ≈ 4.75 V).
   Record the meter voltage, not the box setting.
3. **Fuel pull-up.** Measure B:3 open: V_open. Then put 100 Ω from B:3 to
   B:1 and measure V_100. Pull-up R = 100 × (V_open − V_100) / V_100.
   Check for pulsing with the meter's min/max mode or the logic analyzer.
4. **Fuel mapping.** Decade box from B:3 to B:1. Step 0, 10, 20, 33, 50, 70,
   90, 120, 160, 200, 240, 300, 500, 1 kΩ, then open. Record needle (E, ¼,
   ½, ¾, F), the voltage on B:3 at each step, and the fault (PID 96 FMI 5
   open, FMI 6 or 4 short). That gives the sender range and direction.
5. **Voltage drive check.** Replace the decade box with the pot (step 2
   setup, no series resistor) set to the B:3 voltage recorded at ½ tank.
   The needle should read ½ with no PID 96 fault. If it does, the MCP4728
   drives fuel directly; if not, build the sink buffer.

## Tried

- 2026-11-04: air supply/signal pins identified (table above).
- 2026-11-04, front air gauge (B:13), decade box divider (1 kΩ from supply to
  signal). Supply open-circuit 4.889 V.

  | Box | V signal | Needle (psi) |
  |---|---|---|
  | 240 Ω | 0.938 | 0 (true zero) |
  | 250 Ω | 0.973 | ~0 |
  | 500 Ω | 1.610 | ~50 |
  | 670 Ω | 1.933 | low-air lamp off |
  | 750 Ω | 2.07 | ~70 |
  | 1 kΩ | 2.412 | ~80 |
  | 2 kΩ | 3.214 | ~115 |
  | 4 kΩ | 3.863 | 140 |
  | 9999 Ω | 4.402 | ~160 |
  | open | 4.889 | 180 (full scale) |

  From 50 to 180 psi: about 40 psi/V, psi ≈ 40.5 × (V − 0.43), within a
  few psi. The low-air lamp clears at 1.933 V (≈ 61 psi on that line). At
  the low end the scale is steeper: 0 psi at 0.938 V to 50 psi at 1.610 V
  (~75 psi/V). Two segments: firmware should use this table as a lookup
  with interpolation, not the formula. No high fault seen at 4.889 V.
- 2026-11-04, rear air gauge (B:14), same divider: 1 kΩ 2.432 V ~80 psi,
  4 kΩ 3.861 V ~140 psi. Same curve as front; one table serves both.
- 2026-11-04, fuel B:3 to B:1: open 4.545 V; with 100 Ω, 1.459 V. Pull-up
  ≈ 212 Ω to 4.545 V, so up to ~21 mA into a short. Too stiff for the
  MCP4728 output to hold a voltage, so fuel needs the op-amp + 2N7002 sink
  buffer. 240-33 Ω sender would give about 2.42 V to 0.61 V.
- 2026-11-04, fuel sweep, box from B:3 to B:1:

  | Box | V B:3 |
  |---|---|
  | 0 Ω | white "I" lamp on (short) |
  | 33 Ω | 0.653 |
  | 60 Ω | 1.031 |
  | 90 Ω | 1.373 |
  | 120 Ω | 1.649 |
  | 160 Ω | 1.942 |
  | 200 Ω | 2.178 |
  | 240 Ω | 2.368 |
  | 300 Ω | 2.656 |
  | 500 Ω | 3.069 |

  Fuel needle did not move at any step. Apparent pull-up rises from ~197 Ω
  (33 Ω) to ~240 Ω (500 Ω), so it isn't a plain resistor; drive by voltage
  from this table.
- Needle responded after the heartbeat/settle/key-cycle steps: 120 Ω
  (1.649 V) ≈ 5/8 from empty. Fits a 240 Ω empty, 33 Ω full sender
  ((240 − 120) / 207 = 0.58).

## Left

- Steps 1 to 5 above; fill in the tables below.
- DEF: send AT1T1I with SPN 1761 (byte 1, 0.4 %/bit) from source 0x3D
  (aftertreatment), then 0x00, and watch the DEF gauge.

| Air (front / rear) | Supply pin | Signal pin | V at 0 psi | V at 60 psi | V at 120 psi | V at 180 psi | Low-air clears at | FMI 3 sets at |
|---|---|---|---|---|---|---|---|---|
| Front | B:20 | B:13 | 0.938 | ~1.9 | ~3.1 | 4.89 | 1.933 V | none at 4.89 |
| Rear | B:21 | B:14 | same as front | | | | | |

| Fuel | V_open | Pull-up R | Ω at E | Ω at ½ | Ω at F | Pulsed? |
|---|---|---|---|---|---|---|
| B:3 | 4.545 V | ~212 Ω | | | | |
