# Analog gauges

Inputs for DEF level, fuel level and the two brake air gauges. Pins are from
[pinout.md](pinout.md); drive values are still to be measured.

## Known

| Gauge | Input | Pins (connector B) | Sensor type | Cluster fault when unwired |
|---|---|---|---|---|
| DEF level | **No wire.** J1939 AT1T1I, PGN 65110 (0xFE56), SPN 1761, % | none | Network | none seen |
| Fuel level | Sender B07 | B:3 signal, B:1 (or A:12) sensor ground | Resistive, to sensor ground | PID 96 FMI 5 (open circuit) |
| Front air (secondary, B-tank, front brakes) | Sensor B06A | B:13 + B:20, B:1 ground | 3-wire, voltage out | PID 118 FMI 4 (voltage low) |
| Rear air (primary, A-tank, rear brakes) | Sensor B06B | B:14 + B:21, B:1 ground | 3-wire, voltage out | PID 117 FMI 4 (voltage low) |

- The cluster sends AIR1 (PGN 65198) itself, so air comes from the wired
  sensors, not the bus.
- FMI 4 with nothing on the pin means the air signal input is pulled low.
  FMI 5 on fuel means B:3 is pulled up and reads "open" with no sender.
- Which pin of each air pair is the 5 V supply is not known (one pair measured
  tells all; see pinout.md).
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
| A | Front air, B:20 or B:13 (signal pin) | DAC voltage straight to the signal pin |
| B | Rear air, B:21 or B:14 (signal pin) | DAC voltage straight to the signal pin |
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

1. **Air supply pin.** With nothing on the air pins, measure B:13, B:20,
   B:14, B:21. The one at about 5 V in each pair is the supply; the other is
   the signal. Write down the exact supply voltage.
2. **Air mapping.** 10 kΩ pot across supply and B:1, wiper to the front
   signal pin through 1 kΩ. Step 0 to supply in 0.25 V steps. Record needle
   psi, the voltage where PID 118 FMI 4 clears, where any high fault (FMI 3)
   sets, and where "Low System Air Pressure" clears. Repeat on the rear
   pair (PID 117).
   Decade box instead of the pot: 10 kΩ from supply to signal, box from
   signal to B:1; V = Vs × R / (10k + R) (1.1 kΩ ≈ 0.5 V, 90 kΩ ≈ 4.5 V at
   5 V). Record the meter voltage, not the box setting.
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

Nothing yet.

## Left

- Steps 1 to 5 above; fill in the tables below.
- DEF: send AT1T1I with SPN 1761 (byte 1, 0.4 %/bit) from source 0x3D
  (aftertreatment), then 0x00, and watch the DEF gauge.

| Air (front / rear) | Supply pin | Signal pin | V at 0 psi | V at 60 psi | V at 120 psi | V at 180 psi | Low-air clears at | FMI 3 sets at |
|---|---|---|---|---|---|---|---|---|
| Front | | | | | | | | |
| Rear | | | | | | | | |

| Fuel | V_open | Pull-up R | Ω at E | Ω at ½ | Ω at F | Pulsed? |
|---|---|---|---|---|---|---|
| B:3 | | | | | | |
