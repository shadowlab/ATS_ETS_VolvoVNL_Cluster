# Parts

## Bought

| Part | Cost (USD) | Notes |
|---|---|---|
| Volvo VNL cluster 21844839-P01 (2012) | 144 | Arrived 2026-10-29 |
| Matching harness set | ~97 | Separate source |
| SN65HVD230 CAN transceiver module | ~5 | The S3 has a CAN controller but no transceiver |
| XY-485 RS-485 module (TVS, 3–30 V, auto direction) | ~8 | J1708. Remove R9 ("121", 120 Ω). Bias R1/R2 ("472", 4.7 kΩ) are already right for J1708 |
| CANable 2.0 | ~35 | candleLight firmware, WinUSB. Its 120 Ω jumper must be fitted. Used with python-can |
| 8-channel logic analyzer | ~12 | Lonely Binary, sigrok. Captures J1708 and the sweep marker |

## On hand

| Part | Use |
|---|---|
| ESP32-S3 dev board | Bridge: CAN1 on the built-in TWAI controller, J1708 on a UART |
| Autosport Labs ESP32-CAN-X2 | Spare; two CAN channels if CAN3 ever matters, or as a GVRET sniffer |
| Bench power supply | Cluster and board power |
| 120 Ω resistors | CAN1 termination (see [pinout](pinout.md#termination)) |
| Resistance decade box | Find the range each analog gauge wants |
| Inline fuses | Fused 12 V to the cluster |
| Push button on GPIO41 | Marks a lamp or needle change during sweeps |

## Still to buy

| Part | Approx. cost | Use |
|---|---|---|
| MCP4728 DAC + BSS138 I2C level shifter | $10 | Fuel and both brake air gauges ([analog.md](analog.md)); MCP6002 + 2N7002 if fuel needs a sink buffer |
| Toggle switches, twisted pair wire (if not on hand) | $5–10 | Separate switches for A:1 and A:3, bus wiring |

## Notes on the J1708 transceiver

J1708 is RS-485-style but not plain RS-485: no 120 Ω terminator, bias
resistors hold the idle state, and a sender drives only the active level.
The XY-485's CD4069UB auto-direction inverter does exactly that from TX at 9600
baud. Its transceiver is a 5 V MAX485 (rated 4.75–5.25 V) running from 3.3 V
on the bench (pin 8 measured 3.294 V). Receive and send both work at 9600
baud. Swapping A and B makes every frame bad.
