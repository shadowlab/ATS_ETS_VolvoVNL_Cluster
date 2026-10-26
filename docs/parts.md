# Parts

## Bought

| Part | Cost (USD) | Notes |
|---|---|---|
| Volvo VNL cluster 21844839-P01 (2012) | 144 | Ordered October 2026 |
| Matching harness set | ~97 | Separate source |
| SN65HVD230 CAN transceiver module | ~5 | Ordered October 2026. The S3 has a CAN controller but no transceiver |
| XY-485 RS-485 module (TVS, 3–30 V, auto direction) | ~8 | Ordered October 2026. J1708. Remove R9 ("121", 120 Ω). Bias R1/R2 ("472", 4.7 kΩ) are already right for J1708 |
| CANable 2.0 | ~35 | Ordered October 2026. USB-CAN sniffer with SavvyCAN or python-can |
| 8-channel logic analyzer | ~12 | Ordered October 2026. Decoding J1708 in PulseView |

## On hand

| Part | Use |
|---|---|
| ESP32-S3 dev board | Bridge: CAN1 on the built-in TWAI controller, J1708 on a UART |
| Autosport Labs ESP32-CAN-X2 | Spare; two CAN channels if CAN3 ever matters, or as a GVRET sniffer |
| Bench power supply | Cluster and board power |
| 120 Ω resistors | CAN1 termination (see [pinout](pinout.md#termination)) |
| Resistance decade box | Find the range each analog gauge wants |
| Inline fuses | Fused 12 V to the cluster |

## Still to buy

| Part | Approx. cost | Use |
|---|---|---|
| X9C102/103 digital pots or MCP4728 DAC | $10 | Analog gauge stand-ins, chosen after measuring ranges with the decade box |
| Toggle switches, twisted pair wire (if not on hand) | $5–10 | Separate switches for A:1 and A:3, bus wiring |

## Notes on the J1708 transceiver

J1708 is RS-485-style but not plain RS-485: no 120 Ω terminator, bias
resistors hold the idle state, and a sender drives only the active level.
The XY-485's 74HC04 auto-direction stage does exactly that from TX at 9600
baud. Its transceiver runs from the board's 3.3 V regulator, so it should be
a 3.3 V part (SP3485/MAX3485 class; check the marking). If it turns out to be
a 5 V MAX485, it's running below spec, which is common at low speed, but
revisit it if reception is flaky. While
sending, you probably won't read back your own bytes, which is fine with only
the ESP32 and the cluster on the bus.
