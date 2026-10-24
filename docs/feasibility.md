# Feasibility

**Short answer: doable.** Getting the main needles moving is the easy part;
warning lights, turn signals and the driver display are the hard part.
Expect a moderate-to-hard hobby project: a weekend to get RPM, speed and
temperature moving, then several weeks of trial and error for the rest.

## Status (October 2026)

All hardware for bench testing is bought or on hand, and the full A/B/C
pinout is known from the wiring diagram. Nothing has been powered up yet.

| Area | Known | Still open |
|---|---|---|
| Power and ground | Battery on A:1 (F20), F15 key-switch circuit on A:3, ground on A:2 (the only cluster ground) | Whether the cluster wakes on A:1 + A:3 alone or needs bus traffic |
| J1939 CAN1 | A:15 (H) and A:16 (L), 250 kbit/s | Cluster's own source address; whether it terminates CAN1 (measure A:15 to A:16) |
| J1708/J1587 | A:17 (A, +) and A:18 (B, −), 9600 baud; cluster is MID 140 | What it needs from the VECU to clear the communication error |
| CAN3 | A:10 (H) and A:22 (L); radio and sleeper climate only; 120 Ω inside the cluster | Not needed for gauges |
| Analog gauges | Fuel sender on B:3; five air pressure gauges each on a supply + signal pair on connector B; sensor ground on A:12 and B:1 | Which pin of each pair is supply; resistance and voltage ranges |
| Telltales and display | Seat belt, diff lock, air filter and fifth wheel are switch inputs on connector C | Turn signals, high beam, warnings and display pages, probably proprietary messages |
| Menu buttons | Stalk buttons on A:4 to A:7 (switch +12 V in); steering wheel buttons on C:22 / C:25 with C:30 return | — |

Full pin tables: [pinout.md](pinout.md). Parts: [parts.md](parts.md).

## Which cluster

"2011–2019 VNL" spans two different trucks. Volvo replaced the VN with an
all-new VNL for the 2019 model year, with a new cluster and electrical
architecture that has almost no public documentation. This project uses the
**old-style VN cluster** (about 2011 to 2017), which keeps the A/B/C connector
layout Volvo has used since the 2004 VN.

The cluster bought for this build is **21844839-P01**, from a 2012 VNL. A
sibling part, 21844834-P01, turns up pulled from 2013 VNLs, so the 218448xx
numbers look like option variants of the same cluster. Its pinout comes from
the 2011–2013 VNL wiring diagram (bridgeplan 21849527, sheets BA and BB).

## What shapes the design

- **Three buses, one that matters most.** The cluster sits on J1939 CAN1
  (250 kbit/s), J1708/J1587 (9600 baud) and a CAN3 subnet. CAN3 only goes to
  the radio and sleeper climate module (LECM), so one CAN channel plus one
  J1708 transceiver covers the gauges.
- **The cluster wants its VECU.** Volvo service bulletins for 2013–2015 and
  2018–2019 trucks describe "J1587 Communication Error with Vehicle ECU" when
  the cluster (MID 140) loses its J1587 partner. The bridge has to fake it.
- **ACC input, no ignition input.** B:23 is fed from the accessory (ACC)
  rail through fuse F02, but no cluster pin connects to the ignition rail.
  A:3 is fed from F15, the battery supply to the key switch, which stays
  live with the key off. The cluster probably learns RUN/START over the data
  links, so it may stay dark until it sees traffic. B:23 goes on its own
  switch on the bench to test this.
- **Some gauges are wired straight to sensors.** Fuel level and the five air
  pressures are analog inputs on connector B, not network messages. They need
  stand-in signals: the decade box first to find ranges, then digital pots or
  a DAC.
- **No existing project.** No published project drives this cluster from ATS.
  Closest relatives: [CarCluster](https://github.com/jakka351/carcluster)
  (ESP32 + CAN + SimHub), and a VW Constellation truck cluster on xsimulator
  that drives the steppers directly.
- **No real truck to capture from.** Messages are found by listening first,
  then sweeping. See [sweep_plan.md](sweep_plan.md).

## Hardware

| Role | Part |
|---|---|
| Bridge | ESP32-S3: CAN1 on the built-in TWAI controller, J1708 on a UART |
| CAN1 transceiver | SN65HVD230 module |
| J1708 transceiver | XY-485 (auto direction, 4.7 kΩ bias already fitted; remove R9) |
| Sniffing | CANable 2.0 with SavvyCAN; 8-channel logic analyzer for J1708 |
| Spare | ESP32-CAN-X2, for CAN3 or as a second sniffer |
| Analog stand-ins | Decade box now; digital pots or DAC once ranges are known |

**Cost so far:** about $241 for the cluster and harness, plus about $60 for
the transceivers, CANable and logic analyzer. Only the digital pots or DAC
(about $10) remain, so the whole build lands around **$310 to $320**, well
under the original $360 to $650 estimate.

## ATS side

Solved. SCS's Telemetry SDK feeds either **SimHub** (least PC-side code,
reused from the Mazda build) or the **scs-sdk-plugin** (RenCloud fork) shared
memory, readable from Python with `truck-telemetry`. Between them they expose
RPM, speed, odometer, cruise, parking brake, fuel, AdBlue, oil pressure and
temperature, coolant, air pressure and warning, battery voltage, blinkers,
beams and backlight.

## Phases

| Phase | Goal | Difficulty | Rough time |
|---|---|---|---|
| 1. Bench power | Cluster wakes; learn whether A:3 or bus traffic wakes it | Easy | 1 evening |
| 2. Listen | Log what the cluster sends on CAN1 and J1708, including requests | Easy | 1 evening |
| 3. Standard J1939 | Tach, speedo, coolant, oil, voltage move | Moderate | A weekend |
| 4. J1587 and VECU | Communication error pop-up cleared | Moderate to hard | 1–3 weeks |
| 5. Analog gauges | Fuel and air pressure needles | Moderate | A weekend |
| 6. Telltales and display | Blinkers, high beam, warnings, display pages | Hard | Weeks, open-ended |
| 7. ATS link | SimHub feeding everything | Easy | 1–2 evenings |

Phase 1 is shorter than first estimated because the power, ground and data
pins are now known from the diagram rather than found by probing.

## Risks and fallbacks

- **The cluster may not wake on power alone.** If it needs a VECU on the
  bus, phase 4 work moves ahead of phase 3.
- **Proprietary messages for lights and the display may not crack by
  sweeping.** Fallback: drive those LEDs and the clip-in stepper motors
  directly and accept a dark display.
- **Used clusters age badly** (cracked solder joints, dead steppers). The 10
  steppers clip in and cost about $14 each.
- **The XY-485 runs a MAX485 below its rated 5 V.** Common at 9600 baud; if
  J1708 reception is flaky, swap in a 3.3 V MAX3485-class transceiver.

## Sources

- [Volvo 2013 VN wiring diagram (body builder), bridgeplan 21849527](https://www.volvotrucks.us/media/vtna/files/shared/body-builder/wiring-diagrams/april-2017-2013-obd-bridgeplan-21849527-09-1.pdf)
- [Volvo Group 37 schematic index, VN/VHD from 2003](https://class8truckparts.com/media/file/PV776-20014399.pdf)
- [Service bulletin, 2013–2015 VN: J1587 communication error](https://static.nhtsa.gov/odi/tsbs/2015/SB-10058902-3344.pdf)
- [Service bulletin, MY2018–2019: cluster MID 140 watchdog timeout](https://static.nhtsa.gov/odi/tsbs/2019/MC-10161913-9999.pdf)
- [SCS Telemetry SDK](https://modding.scssoft.com/wiki/Documentation/Engine/SDK/Telemetry), [scs-sdk-plugin](https://github.com/Spedcord/scs-sdk-plugin), [truck-telemetry](https://pypi.org/project/truck-telemetry)
- [TruckersReport: VNL cluster has 10 clip-in steppers](https://www.thetruckersreport.com/truckingindustryforum/goto/post?id=5813032)
