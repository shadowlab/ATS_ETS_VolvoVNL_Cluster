# Analog gauges

Fuel, front air, rear air and DEF. Measured on the bench 2026-11-04 with
`can_wake.py` running (heartbeat). Pins from [pinout.md](pinout.md).

## Inputs

| Gauge | Input | Type | Fault when unwired |
|---|---|---|---|
| Front air (secondary, B-tank) | B:13 signal, B:20 supply, B:1 ground | Voltage in | PID 118 FMI 4 |
| Rear air (primary, A-tank) | B:14 signal, B:21 supply, B:1 ground | Voltage in | PID 117 FMI 4 |
| Fuel | B:3 to B:1 | Resistive sender, pulled up in the cluster | PID 96 FMI 5 |
| DEF | **No wire.** J1939 AT1T1I 0xFE56 | Network | none |

- Air sensor supply (B:20 / B:21): 4.889 V. Other air inputs follow the same
  pattern: B:17–B:21 supply, B:2 / B:6 / B:7 / B:13 / B:14 signal.
- The cluster sends AIR1 (0xFEAE) itself; air comes only from the wires.
- No low-fuel or low-DEF lamp on this cluster. Low air: "Low System Air
  Pressure" on the LCD plus the buzzer.

## Air (front and rear, same curve)

| V on signal | Needle (psi) |
|---|---|
| 0.938 | 0 |
| 1.610 | ~50 |
| 1.933 | low-air warning clears (~61) |
| 2.07 | ~70 |
| 2.412 | ~80 |
| 3.214 | ~115 |
| 3.863 | 140 |
| 4.402 | ~160 |
| 4.889 (supply) | 180, full scale |

- Two slopes: ~75 psi/V from 0 to 50 psi, ~40 psi/V from 50 to 180 psi
  (psi ≈ 40.5 × (V − 0.43)). Firmware: lookup table with interpolation.
- Rear checked at 2.432 V ~80 psi and 3.861 V ~140 psi: matches front.
- No high-voltage fault (FMI 3) at 4.889 V.

## Fuel

| Sender Ω (B:3 to B:1) | V on B:3 | Needle |
|---|---|---|
| 0 | 0 | short: white "i" lamp |
| 33 | 0.653 | F |
| 85 | ~1.32 | ½ |
| 135 | ~1.76 | just over ¼ |
| 190 | ~2.12 | entering red |
| 225 | ~2.29 | E |
| 240 | 2.368 | past E |

- Open circuit 4.545 V. Pull-up behaves like ~200 to 240 Ω (not a plain
  resistor), so up to ~21 mA into a short.
- Further sweep voltages: 60 Ω 1.031, 90 Ω 1.373, 120 Ω 1.649, 160 Ω 1.942,
  200 Ω 2.178, 300 Ω 2.656, 500 Ω 3.069 V.
- Sender 33 Ω full, ~225 Ω empty; not linear in ohms (½ at 85 Ω).
- **Key-on fast fill:** the needle jumps to the sender value only when A:3
  is cycled with the heartbeat running. While running it tracks slowly
  (33 Ω after key-on at 120 Ω: moved to ½ in 10 min). In-game burn shows
  without a key cycle; a refuel or a game load needs one (or waits).

## DEF

- AT1T1I (0xFE56) byte 1 = level, 0.4 %/bit; byte 2 = tank temp (41 =
  25 °C). Checked: 7D ½, 5E ~⅜, 9C ~⅝, DB ~⅞.
- Needs FE56 from **SA 00** (the value shown) **and any frame from SA 01**
  (FE56 or ET1 0xFEEE both work). Either alone: no reading, needle sits in
  the red. Needle updates within seconds, no key cycle.
- No effect: FE56 from 3D alone, 00 alone; J1708 page-2 PID 22 from MID 128
  and 144. Different FE56 values from many SAs at once: no reading.
- Bench: `can_wake.py ... --extra "FE56:00:7D 41" --extra "FEEE:01:7D"`.

## Drive hardware

| MCP4728 channel | Gauge | Drive |
|---|---|---|
| A | Front air, B:13 | DAC direct, 1 kΩ series |
| B | Rear air, B:14 | DAC direct, 1 kΩ series |
| C | Fuel, B:3 | DAC into a sink buffer |
| D | Spare | |

- MCP4728 at VDD 5 V, VREF = VDD, so it reaches 4.89 V (180 psi). The
  internal 4.096 V reference would stop near 150 psi.
- I2C: BSS138 level shifter (MCP4728 wants > 3.5 V high at VDD 5 V).
- Fuel sink buffer: MCP6002 with DAC on +in and B:3 on −in, output to the
  gate of a 2N7002, drain on B:3, source on B:1. Holds B:3 at the DAC
  voltage against the stiff pull-up; MCP4728 alone can't sink ~20 mA.
- Digital pots ruled out for fuel (wiper resistance 75–150 Ω, few mA).
- Grounds: DAC to B:1; ESP32 to A:2.

## Left

- Build and check: MCP4728 at 4.89 V on the air pins; sink buffer holding
  B:3 at the fuel voltages.
- Firmware: FE56 from 00 plus one frame from 01; air and fuel lookup
  tables; decide how to handle fuel fast fill (relay on A:3, or accept the
  slow track).
