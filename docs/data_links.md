# Data links (Volvo service bulletin 371-44)

Notes from Volvo Trucks North America service bulletin 371-44, "Data Links,
Fault Tracing" (12.2004, VN/VHD version 2 from build date 11.2002). Lab
found it; the PDF is not in the repo. It is older than our 2012 cluster, so
later trucks may have more modules, but the cluster's role on the links is
the same.

## Topology

The bulletin's diagram (page 5) shows three CAN segments and J1708:

| Link | Modules (MID) | Notes |
|---|---|---|
| J1939-1, main backbone (CAN1) | 128 engine, 130 transmission, 140 cluster, 144 vehicle ECU, 216 lighting control, 249 body builder | "Control link": engine speed and other control data |
| J1939-3, under the cluster (CAN3, A:10/A:22) | 140 cluster, 206 radio, 250 steering wheel module | The cluster ends this segment |
| J1939-4, under the body builder module | 249 only | Not relevant |
| J1708/J1587 | 128, 136 ABS, 140, 142 satellite comms, 146 climate, 216, 232 airbag, 249, diagnostic connector | "Information link": fault codes, warnings, and a backup for J1939 in some cases. VCADS (MID 172) talks only here |

So the steering wheel module (MID 250) shares CAN3 with the radio. Gauges
still don't need CAN3, but the steering wheel buttons for the cluster
display probably arrive there.

## MIDs

| MID | Module |
|---|---|
| 128 | Engine ECU |
| 130 | Transmission |
| 136 | ABS |
| 140 | Instrument cluster |
| 142 | Satellite communications |
| 144 | Vehicle ECU (VECU) |
| 146 | Climate control |
| 172 | Test tool (VCADS Pro) |
| 206 | Radio |
| 216 | Lighting control module |
| 219 | VORAD / adaptive cruise |
| 232 | Airbag |
| 249 | Body builder module |
| 250 | Steering wheel module |

The bulletin gives no J1939 source addresses.

## The cluster depends on the VECU

The bulletin says the instrument cluster and ABS "use the messages from the
vehicle ECU" on J1939. With the VECU cut off, the cluster stores
MID 140 PSID 201 FMI 9.

Data link fault codes (Volvo SIDs) say which module is missing:

| SID | Meaning |
|---|---|
| 200 | Data link, engine ECU (MID 128) |
| 201 | Data link, vehicle ECU (MID 144) |
| 202 | Data link, instrument cluster (MID 140) |
| 204 | Data link, ABS (MID 136) |
| 210 | Data link, lighting control module (MID 216) |
| 214 | Data link, body builder module (MID 249) |
| 231 | J1939 data link fault |
| 250 | J1587/J1708 data link fault |

FMIs that matter for data links:

| FMI | Meaning in the bulletin |
|---|---|
| 2 | Erratic: this ECU sees no J1939 at all (its own wiring) |
| 9 | Abnormal update rate: messages from that ECU arrive, but needed data is missing |
| 11 | Internal fault |
| 12 | Loss of device: messages from that ECU are expected and absent |

FMI 9 vs FMI 12 is a useful test signal: if the cluster's SID 201 moves from
FMI 12 to FMI 9, it has started hearing the VECU, even if the data isn't
right yet. `tools/j1587.py` decodes these codes from the cluster's frames.

## Electrical

- J1939: wire 406 CAN_H yellow, 407 CAN_L green, twisted. Key on, CAN_H 2-5 V
  and CAN_L 0-3 V to ground.
- Two 120 Ω terminators, 60 Ω between H and L with the key off (50-70 Ω is
  fine; ~120 Ω means one is missing; ~40 Ω means three). One sits in the
  fuse/relay center near the VECU, the other inside a Volvo engine ECU. The
  cluster is a stub, so it should not terminate CAN1. Our bench reading of
  ~68 Ω on A:15/A:16 with nothing else attached doesn't fit that and is
  worth re-measuring with the cluster unpowered.
- J1708: wires 400 (A) and 401 (B). Key on, each 0-5 V to ground and 2-5 V
  between A and B, varying with traffic.
- 9-pin diagnostic connector: A ground, B battery, C CAN_H (406), D CAN_L
  (407), F J1708 A (400), G J1708 B (401), J ignition (196), E and H unused.
