# Sweep plan

There's no real VN truck to record from, so messages are found by sweeping,
as in the Mazda 3 build. The difference: the Mazda uses 11-bit IDs, which can
be swept blindly. J1939 uses 29-bit IDs, so sweep by structure instead.

Tools: CANable 2.0 with SavvyCAN for listening, python-can for scripted
sweeps, and the XY-485 plus a USB-serial adapter (or the ESP32) for J1708.

## 1. Listen first

Power the cluster with nothing else on the bus and log what it sends.

- **Requests (PGN 59904 / 0xEA00)** name the PGNs it wants to hear.
- **Address claims (PGN 60928 / 0xEE00)** show its own source address.
- On J1708, note anything it sends as MID 140, especially requests to the
  VECU (likely MID 144).

## 2. Standard J1939 PGNs

Send the PGNs in [messages.md](messages.md) from the engine address (0x00)
at their normal rates. These should move the main gauges quickly.

## 3. Volvo proprietary range

PGN 0xFF00 to 0xFFFF is 256 PGNs per source address. Sweep from 0x00 first,
then other common addresses (0x03 transmission, 0x0B brakes, 0x21 body,
0x17 cluster itself, 0x31 cab controller). Try all-zeros and all-ones
payloads and watch for telltales and display changes. This is the likely home
of turn signals and warning lights.

## 4. J1708/J1587

Step through PIDs 0 to 255 from MID 128 (engine) and MID 144 (likely VECU).
It's a small space at 9600 baud. The communication error pop-up most likely
clears here once the right MID is heard regularly. J1708 frames are
MID + data + checksum (two's complement of the byte sum), and a sender waits
for bus idle before transmitting.

## 5. CAN3 (only if needed)

CAN3 goes to the radio and LECM, so skip it unless something on the display
depends on it. If so, find its bitrate by listening (try 250 and 500 kbit/s),
then sweep it the same way.

## Logging results

Record every hit in [messages.md](messages.md#confirmed-on-the-bench):
bus, ID or MID/PID, payload, what moved.
