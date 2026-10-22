# Cluster pinout

From the 2011–2013 VNL wiring diagram (bridgeplan 21849527), read off the
drawing on sheet BA (connectors A and B). Connector C (sheet BB) is still
pending. **Check every pin with a meter on your own cluster and harness
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
| A:10 / A:22 | CAN3 A / B | Radio and sleeper climate (LECM) bus; 120 Ω inside the cluster | Leave open |
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
| B:9 | HB9B1 | Ambient air temperature (B22A) | Resistance to sensor ground |
| B:12 | HB12A1 | Gearbox oil temperature (B09) | Resistance to sensor ground |
| B:10 / B:11 / B:15 | HB10A1 / HB11A1 / HB15A1 | Rear axle oil temps: front-rear, rear-rear, center-rear | Optional |
| B:28 | F63C1 | Fifth wheel / lift axle indicator input (switch S88) | Optional |

**Air pressure sensors are 3-wire:** sensor ground plus two cluster pins, one
a supply and one the signal. The diagram doesn't say which is which. With the
cluster powered, the pin reading about 5 V is the supply; the other is the
signal, so a stand-in is a voltage on the signal pin between 0 V and that
supply.

## Connector C (pending sheet BB)

Known from cross-references so far: C:3 seat belt switch (HC3A1), C:8 F63
indicator circuit, C:10 interaxle / interwheel diff lock indicator (F27),
C:29 wiper circuit (HC29A1, F26). It may also carry more grounds.

## No ignition pin

No cluster pin connects to the ignition (IGN) rail. The cluster most likely
learns key position over the data links from the VECU, so it may stay dark
with power applied until it sees bus traffic. If it doesn't wake on bench
power alone, start sending J1939 and J1587 traffic before deciding it's dead.

## Termination

Measure CAN-H to CAN-L (A:15 to A:16) with power off. About 120 Ω means the
cluster terminates one end, so add one 120 Ω at the ESP32 end. Open circuit
means add 120 Ω at both ends. J1708 uses **no** terminator.

## First power-up

Bench supply at 12–13.5 V with the current limit around 1 A. Ground on A:2,
+12 V on A:1, then +12 V on A:3.
