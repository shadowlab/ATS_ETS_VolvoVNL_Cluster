# ATS_ETS_VolvoVNL_Cluster
Bridging physical and simulation - Connect a real 2011–2013 Volvo VNL instrument cluster and drive its gauges from American Truck Simulator or Euro Truck Simulator 2.

```
American Truck                       ESP32-S3        J1939 CAN1 250k ─► 2012 Volvo VNL
Simulator   ──────► SimHub     ────► bridge    ────► J1708/J1587 9600 ─► instrument
(telemetry)         (USB serial)                ────► analog stand-ins ─► cluster
                                                       (fuel, air)        21844839-P01
```

> **Status: research and bench prep (October 2026).** The cluster and harness
> are ordered; nothing has been powered up yet. Everything in `docs/` is from
> public Volvo documents and the 2011–2013 VNL wiring diagram, not yet checked
> on real hardware. This is the sequel to
> [ATS_ETS_Mazda3_Cluster](https://github.com/shadowlab/ATS_ETS_Mazda3_Cluster).

## How it differs from the Mazda 3 build

| | Mazda 3 (2005) | Volvo VNL (2012) |
|---|---|---|
| Main bus | HS-CAN 500k, 11-bit IDs | J1939 CAN1 250k, 29-bit IDs |
| Second bus | MS-CAN 125k | J1708/J1587, 9600 baud RS-485-style serial |
| Analog inputs | Fuel sender | Fuel level and air pressures on connector C |
| Known message map | Partly public | Standard J1939/J1587 for engine gauges; telltales are Volvo proprietary |
| Finding unknown messages | Blind 11-bit sweep | Structured sweep (see [sweep plan](docs/sweep_plan.md)) |

The cluster also expects to hear from the truck's vehicle ECU (VECU) on
J1587. Without it, it shows "J1587 Communication Error with Vehicle ECU", so
the bridge has to fake that ECU too.

## Repository layout

| Path | What it is |
|---|---|
| [`docs/plan.md`](docs/plan.md) | The four-step plan from bench to game |
| [`docs/feasibility.md`](docs/feasibility.md) | Can it be done, what it costs, phases and risks |
| [`docs/pinout.md`](docs/pinout.md) | Connector A/B/C pins confirmed so far, and what still needs a meter |
| [`docs/parts.md`](docs/parts.md) | Parts list: bought, on hand, still to buy |
| [`docs/sweep_plan.md`](docs/sweep_plan.md) | How to find the cluster's messages without a real truck |
| [`docs/messages.md`](docs/messages.md) | Candidate J1939 PGNs and J1587 PIDs per gauge, plus a log of what's confirmed |
| `firmware/` | ESP32-S3 bridge firmware (to come) |
| [`tools/`](tools/) | Bus listeners and a frame sender for the bench; sweep scripts to come |

## Hardware

- Volvo VN/VNL instrument cluster, part **21844839-P01** (from a 2012 VNL,
  old-style VN generation), with a matching harness set
- ESP32-S3 dev board: built-in CAN (TWAI) for J1939 CAN1, a UART for J1708
- SN65HVD230 CAN transceiver
- XY-485 style RS-485 module (TVS protected, 4.7 kΩ bias fitted, auto
  direction) for J1708, with its 120 Ω terminator (R9, "121") removed
- CANable 2.0 + SavvyCAN for listening and sweeping from a PC
- Bench power supply, current-limited to about 1 A for the first power-up

Full list with prices in [docs/parts.md](docs/parts.md).

## Plan

The goal is every gauge and telltale the cluster supports working in game.
Details in [docs/plan.md](docs/plan.md).

1. **Bench bring-up and listen:** power the cluster and log what it asks for on CAN1 and J1708.
2. **Sweep and map:** standard J1939 PGNs, Volvo proprietary PGNs and J1587 PIDs, with a camera watching for changes; measure analog gauge ranges.
3. **ESP32-S3 firmware:** CAN1 and J1708 traffic, analog stand-ins, values over USB serial.
4. **Game link:** SimHub or scs-sdk bridge from ATS/ETS2 telemetry.

## License

MIT, see [LICENSE](LICENSE).
