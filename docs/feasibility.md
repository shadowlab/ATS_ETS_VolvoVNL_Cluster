# Feasibility

**Short answer: doable.** Getting the main needles moving is the easy part;
warning lights, turn signals and the driver display are the hard part.
Expect a moderate-to-hard hobby project: a weekend to get RPM, speed and
temperature moving, then several weeks of trial and error for the rest.

## Which cluster

"2011–2019 VNL" spans two different trucks. Volvo replaced the VN with an
all-new VNL for the 2019 model year, with a new cluster and electrical
architecture that has almost no public documentation. This project uses the
**old-style VN cluster** (about 2011 to 2017), which keeps the A/B/C connector
layout Volvo has used since the 2004 VN.

The cluster bought for this build is **21844839-P01**, from a 2012 VNL. A
sibling part, 21844834-P01, turns up pulled from 2013 VNLs, so the 218448xx
numbers look like option variants of the same cluster. No public pinout or
spec sheet for 21844839 itself was found.

## What shapes the design

- **Two networks, not one.** The cluster sits on J1939 CAN1 (250 kbit/s),
  J1708/J1587 (9600 baud) and a CAN3 subnet. CAN3 only goes to the radio and
  sleeper climate module (LECM), so it can be ignored for the gauges.
- **The cluster wants its VECU.** Volvo service bulletins for 2013–2015 and
  2018–2019 trucks describe "J1587 Communication Error with Vehicle ECU" when
  the cluster (MID 140) loses its J1587 partner. The bridge has to fake it.
- **Some gauges are wired straight to sensors.** Fuel level and the air
  pressures appear to come in as analog sensor wires on connector C, not as
  network messages. They need stand-in signals (pots, digital pots or a DAC).
- **No existing project.** No published project drives this cluster from ATS.
  Closest relatives: [CarCluster](https://github.com/jakka351/carcluster)
  (ESP32 + CAN + SimHub), and a VW Constellation truck cluster on xsimulator
  that drives the steppers directly.
- **No real truck to capture from.** Messages are found by sweeping, as in the
  Mazda 3 build. See [sweep_plan.md](sweep_plan.md).

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
| 1. Bench power | Power, ignition, ground found; cluster wakes | Easy to moderate | 1–2 evenings |
| 2. Standard J1939 | Tach, speedo, coolant, oil, voltage move | Moderate | A weekend |
| 3. J1587 and VECU | Communication error pop-up cleared | Moderate to hard | 1–3 weeks |
| 4. Analog gauges | Fuel and air pressure needles | Moderate | A weekend |
| 5. Telltales and display | Blinkers, high beam, warnings, display pages | Hard | Weeks, open-ended |
| 6. ATS link | SimHub feeding everything | Easy | 1–2 evenings |

## Risks and fallbacks

- **The cluster may not wake on power alone.** No pin connects to the ignition
  rail, so it probably learns key position from bus traffic. Power, ground and
  data pins are now known (see [pinout.md](pinout.md)); connector C is still
  pending.
- **Proprietary messages for lights and the display may not crack by
  sweeping.** Fallback: drive those LEDs and the clip-in stepper motors
  directly and accept a dark display.
- **Used clusters age badly** (cracked solder joints, dead steppers). The 10
  steppers clip in and cost about $14 each.

## Sources

- [Volvo 2013 VN wiring diagram (body builder), bridgeplan 21849527](https://www.volvotrucks.us/media/vtna/files/shared/body-builder/wiring-diagrams/april-2017-2013-obd-bridgeplan-21849527-09-1.pdf)
- [Volvo Group 37 schematic index, VN/VHD from 2003](https://class8truckparts.com/media/file/PV776-20014399.pdf)
- [Service bulletin, 2013–2015 VN: J1587 communication error](https://static.nhtsa.gov/odi/tsbs/2015/SB-10058902-3344.pdf)
- [Service bulletin, MY2018–2019: cluster MID 140 watchdog timeout](https://static.nhtsa.gov/odi/tsbs/2019/MC-10161913-9999.pdf)
- [SCS Telemetry SDK](https://modding.scssoft.com/wiki/Documentation/Engine/SDK/Telemetry), [scs-sdk-plugin](https://github.com/Spedcord/scs-sdk-plugin), [truck-telemetry](https://pypi.org/project/truck-telemetry)
- [TruckersReport: VNL cluster has 10 clip-in steppers](https://www.thetruckersreport.com/truckingindustryforum/goto/post?id=5813032)
