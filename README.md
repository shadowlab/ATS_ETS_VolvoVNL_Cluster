# ATS_ETS_VolvoVNL_Cluster
Bridging physical and simulation - Connect a real 2011–2013 Volvo VNL instrument cluster and drive its gauges from American Truck Simulator or Euro Truck Simulator 2.

```
American Truck                       ESP32-S3        J1939 CAN1 250k ─► 2012 Volvo VNL
Simulator   ──────► SimHub     ────► bridge    ────► J1708/J1587 9600 ─► instrument
(telemetry)         (USB serial)                ────► analog stand-ins ─► cluster
                                                       (fuel, air)        21844839-P01
```

> **Status: bench testing (November 2026).** The cluster is powered on the
> bench and both buses work in both directions. A scripted engine heartbeat
> (`tools/can_wake.py`) moves the tach, oil pressure and coolant gauges; the
> parking brake and "i" lamps are found. The speedo, odometer and the other
> lamps are still open. See [messages.md](docs/messages.md) for what is
> confirmed.

## The vehicle ECU

The cluster also expects to hear from the truck's vehicle ECU (VECU, MID 144).
On the bench it logs the VECU as lost (SID 201), so the bridge may have to
fake that ECU too. Volvo bulletins describe a "J1587 Communication Error"
pop-up for this; it has not appeared on the bench.

## Repository layout

| Path | What it is |
|---|---|
| [`docs/plan.md`](docs/plan.md) | The four-step plan from bench to game |
| [`docs/feasibility.md`](docs/feasibility.md) | Can it be done, what it costs, phases and risks |
| [`docs/pinout.md`](docs/pinout.md) | Connector A/B/C pins confirmed so far, and what still needs a meter |
| [`docs/harness.md`](docs/harness.md) | Annotated photos of the harness connectors, with wire colors and codes per cavity |
| [`docs/parts.md`](docs/parts.md) | Parts list: bought, on hand, still to buy |
| [`docs/sweep_plan.md`](docs/sweep_plan.md) | How to find the cluster's messages without a real truck |
| [`docs/messages.md`](docs/messages.md) | Candidate J1939 PGNs and J1587 PIDs per gauge, plus a log of what's confirmed |
| [`docs/data_links.md`](docs/data_links.md) | Notes from Volvo bulletin 371-44: bus topology, MIDs, data link fault codes, voltages, termination |
| [`docs/analog.md`](docs/analog.md) | Fuel, air and DEF gauge inputs, measured needle maps, MCP4728 drive |
| [`firmware/`](firmware/README.md) | ESP32-S3 firmware: J1708 to USB bridge and a bench button on GPIO41 (stage 1 only) |
| [`tools/`](tools/) | Bus listeners and senders, the engine heartbeat, the sweep (camera, bus watch, button), logic analyzer capture and cross-check |

## Hardware

- Volvo VN/VNL instrument cluster, part **21844839-P01** (from a 2012 VNL,
  old-style VN generation), with a matching harness set
- ESP32-S3 dev board: built-in CAN (TWAI) for J1939 CAN1, a UART for J1708
- SN65HVD230 CAN transceiver
- XY-485 style RS-485 module (TVS protected, 4.7 kΩ bias fitted, auto
  direction) for J1708, with its 120 Ω terminator (R9, "121") removed
- CANable 2.0 (120 Ω jumper fitted) with python-can or SavvyCAN for listening and sweeping from a PC
- 8-channel logic analyzer, and a push button on GPIO41 to mark changes during sweeps
- Bench power supply, current-limited to about 1 A for the first power-up

Full list with prices in [docs/parts.md](docs/parts.md).

## Plan

The goal is every gauge and telltale the cluster supports working in game.
Details in [docs/plan.md](docs/plan.md).

1. **Bench bring-up and listen:** done.
2. **Sweep and map:** in progress. Standard PGNs done; J1587 PIDs partly swept; analog gauges (fuel, air) and DEF mapped; proprietary CAN range still to do.
3. **ESP32-S3 firmware:** CAN1 and J1708 traffic, analog stand-ins, values over USB serial.
4. **Game link:** SimHub or scs-sdk bridge from ATS/ETS2 telemetry.

## License

MIT, see [LICENSE](LICENSE).
