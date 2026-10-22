# Cluster pinout

From the 2011–2013 VNL wiring diagram (bridgeplan 21849527). The text of the
PDF lost which wire lands on which pin inside the drawings, so these come from
cross-references on other sheets. **Check every pin with a meter on your own
cluster and harness before powering it.**

The cluster has three connectors: **A03A**, **A03B** and **A03C** (written
A, B, C below).

## Confirmed from the diagram

| Pin | Wire | Function | Bench |
|---|---|---|---|
| A:3 | F15D10 | Key-switch feed from F15 (20 A) | Ignition switch to +12 V |
| A:10, A:22 | DL3H17 / DL3L17 | J1939 CAN3: radio and LECM only | Leave open (has its own 120 Ω inside the cluster) |
| A:15, A:16 | DL1 pair | J1939 CAN1 backbone, 250 kbit/s | ESP32 CAN via SN65HVD230. H vs L order: **check with a meter** |
| A:17 | DL4HN15 | J1708/J1587 + (A) | RS-485 module A |
| A:18 | DL4LN15 | J1708/J1587 − (B) | RS-485 module B |
| A:21 | PD21A1 | Unconfirmed | Leave open |
| B (two pins) | F20A1, F20A4 | Battery feed from F20 (15 A, shared with radio) | Constant +12 V. **Pin numbers still unknown** |
| B:28, C:8 | F63 circuit | Lift axle / fifth wheel indicator input | |
| C:3 | HC3A1 | Seat belt switch | |
| C:10 | F27 circuit | Interaxle / interwheel diff lock indicator | |
| C:29 | HC29A1 | Wiper circuit (F26) | |

## Still unknown

- **Grounds.** They run to cab ground studs (wires X03DA17 on A/B; X03DB4,
  XC1F13, XC1F21, X01DA5A3 on C), but the pin numbers are only in the drawing.
- **Which two B pins carry F20.**
- **Analog sensor inputs** on C: fuel level (B07) and air pressures (B06A,
  B06B, B06AP, B56, B58R), plus ambient and oil temperature. Also whether each
  expects a variable resistance or 0–5 V.

Next step: read these off sheets BA and BB of the PDF drawing, or trace them
with a meter once the harness arrives.

## Termination

Measure CAN-H to CAN-L at the cluster with power off. About 120 Ω means the
cluster terminates one end, so add one 120 Ω at the ESP32 end. Open circuit
means add 120 Ω at both ends. J1708 uses **no** terminator.

## First power-up

Bench supply at 12–13.5 V with the current limit around 1 A. Battery feed
and ground first, then switch the key feed (A:3).
