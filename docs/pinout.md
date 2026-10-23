# Cluster pinout

From the 2011–2013 VNL wiring diagram (bridgeplan 21849527), read off the
drawings on sheets BA (connectors A and B) and BB (connector C). **Check every pin with a meter on your own cluster and harness
before powering it.**

The cluster has three connectors: **A03A**, **A03B** and **A03C** (written
A, B, C below).

## Connector A

| Pin | Wire | Function | Bench hookup |
|---|---|---|---|
| A:1 | F20A3 | Battery +12 V (F20, 15 A) | +12 V |
| A:2 | X03DA17 | Ground (cab ground stud) | Ground |
| A:3 | F15D10 | F15 key-switch battery circuit | +12 V |
| A:4 / A:5 / A:6 / A:7 | HA4A1 / HA5A1 / HA6A1 / HA7A1 | Stalk buttons Select / Esc / Up / Down, each switching +12 V in | Push buttons to +12 V |
| A:10 / A:22 | DL3HB17 / DL3LB17 | CAN3 H / L: radio and sleeper climate (LECM) bus; 120 Ω inside the cluster | Leave open |
| A:12 | HA12A1 | Sensor ground (common return for analog sensors) | Return for the sensor stand-ins |
| A:15 | DL1HN3 | J1939 CAN1 H | CAN transceiver CANH |
| A:16 | DL1LN3 | J1939 CAN1 L | CAN transceiver CANL |
| A:17 | DL4HN15 | J1708/J1587 A (+) | XY-485 A |
| A:18 | DL4LN15 | J1708/J1587 B (−) | XY-485 B |
| A:21 | PD21A1 | Goes to module A27, pin D:21 | Leave open |

## Connector B

| Pin | Wire | Function | Bench hookup |
|---|---|---|---|
| B:1 | HA12A7 | Sensor ground (same as A:12) | Return for the sensor stand-ins |
| B:3 | HB3A1 | Fuel level sender (B07) | Resistance to sensor ground |
| B:2 + B:18 | HB2A1 / HB18A1 | Brake application air pressure (B06AP) | Supply + signal |
| B:13 + B:20 | HB13A1 / HB20A1 | Air pressure B-tank, front brakes (B06A) | Supply + signal |
| B:14 + B:21 | HB14A1 / HB21A1 | Air pressure A-tank, rear brakes (B06B) | Supply + signal |
| B:6 + B:17 | HB6A1 / HB17A1 | Rear suspension air pressure (B58R) | Supply + signal |
| B:7 + B:19 | HB7A1 / HB19A1 | Front axle suspension air pressure (B56) | Supply + signal |
| B:9 | HB9A1 | Ambient air temperature (B22A) | Resistance to sensor ground |
| B:12 | HB12A1 | Gearbox oil temperature (B09) | Resistance to sensor ground |
| B:10 / B:11 / B:15 | HB10A1 / HB11A1 / HB15A1 | Rear axle oil temps: front-rear, rear-rear, center-rear | Optional |
| B:23 | F02A2 | Accessory (ACC) +12 V, from the ACC rail through fuse F02 (radio fuse, 10 A / 25 A) | +12 V, switched (key at ACC) |
| B:28 | F63C1 | Fifth wheel / lift axle indicator input (switch S88) | Optional: likely +12 V when active (fused F circuit) |

**Air pressure sensors are 3-wire:** sensor ground plus two cluster pins, one
a supply and one the signal. The diagram doesn't say which is which, but the
pattern is the same on all five: sensor pin A is sensor ground, sensor pin C
goes to B:2 / B:6 / B:7 / B:13 / B:14, and sensor pin B goes to B:17 / B:18 /
B:19 / B:20 / B:21. So measuring one sensor's pair tells you all five. With the
cluster powered, the pin reading about 5 V is the supply; the other is the
signal, so a stand-in is a voltage on the signal pin between 0 V and that
supply.

## Connector C

Connector C is optional for the gauges and adds no grounds: **A:2 is the
cluster's only ground**. The inputs are not all the same polarity: C:5 (B39)
switches to sensor ground and C:11 to chassis ground, while C:8 and C:10 are
fused F circuits, so they most likely see +12 V when active. Check each one
with a meter before wiring a switch to it.

| Pin | Wire | Function | Bench hookup |
|---|---|---|---|
| C:3 | HC3A1 | Seat belt switch (S61A) | Optional: other side not on this sheet, check |
| C:5 | HC5A1 | Air filter restriction switch (B39) | Optional: switch to sensor ground |
| C:7 | HC7A1 | To engine harness (Volvo X11.A:9 / Cummins FCEN:D) | Leave open |
| C:8 | F63E1 | Fifth wheel / lift axle circuit (switch S93) | Optional: likely +12 V when active (fused F circuit), check |
| C:10 | F27E1 | Interaxle diff lock switch (S09) | Optional: likely +12 V when active (fused F circuit), check |
| C:11 | HC11A1 | Diff lock engaged indicator (S45x / S047 / S048) | Optional: switch to ground (diff switches go to chassis ground) |
| C:22 / C:25 | HC22A1 / HC25A1 | Steering wheel buttons via contact reel Z06 (HI/MI, radio, YES/NO) | Optional menu buttons |
| C:30 | HC30A1 | Steering wheel button return | With C:22 / C:25 |
| C:29 | HC29A1 | To sheet GP (MCFC:119), wiper circuit | Leave open |

## Minimum bench harness

| Pins | For |
|---|---|
| A:1, A:2, A:3 | Battery +12 V, ground, key-switch +12 V |
| B:23 | ACC +12 V (switched) |
| A:15, A:16 | J1939 CAN1 H / L |
| A:17, A:18 | J1708/J1587 A / B |
| B:3, plus the B air pressure pairs, with A:12 / B:1 sensor ground | Analog gauges (fuel and air), when you get to phase 4 |

## Key position: ACC on B:23, no IGN pin

B:23 is fed from the accessory (ACC) rail through fuse F02, which it shares
with the radio. It is the only hardwired key-position input. No pin
connects to the ignition (IGN) rail, so the cluster most likely learns
RUN/START over the data links from the VECU.

On the bench, put +12 V on B:23 through a switch so you can simulate key
off and key at ACC. If the cluster still doesn't wake with A:1, A:3 and
B:23 powered, start sending J1939 and J1587 traffic before deciding it's
dead.

## Termination

Measure CAN-H to CAN-L (A:15 to A:16) with power off. About 120 Ω means the
cluster terminates one end, so add one 120 Ω at the ESP32 end. Open circuit
means add 120 Ω at both ends. J1708 uses **no** terminator.

## First power-up

Bench supply at 12–13.5 V with the current limit around 1 A. Ground on A:2,
+12 V on A:1, then +12 V on A:3, then +12 V on B:23 (ACC).
