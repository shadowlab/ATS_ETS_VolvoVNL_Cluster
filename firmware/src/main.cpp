// J1708 <-> USB bridge for the stage 1 bench (docs/wiring.md).
//
// Bytes from the bus go to USB one frame at a time, unchanged, so
// tools/j1708_listen.py works on this board the same as on a USB-serial
// adapter. Bytes from the PC are gathered into a frame and sent on the bus
// once it has been idle long enough, with GPIO6 high while it goes out so the
// logic analyzer can line sweep frames up with the bus.

#include <Arduino.h>

constexpr int kJ1708Tx = 17;     // UART1 TX -> XY-485 TXD
constexpr int kJ1708Rx = 18;     // UART1 RX <- XY-485 RXD
constexpr int kMarker = 6;       // logic analyzer CH4
constexpr int kTwaiTx = 4;       // SN65HVD230 CTX, unused until stage 2

constexpr uint32_t kBaud = 9600;
constexpr uint32_t kBitUs = 1000000 / kBaud + 1;
constexpr size_t kMaxFrame = 21;

// J1708: a frame ends after 10 idle bit times, and a sender waits
// 12 + 2 * priority bit times of idle before it starts. Priority 5 leaves
// room for the cluster's own higher priority messages.
constexpr uint32_t kFrameEndUs = 10 * kBitUs;
constexpr uint32_t kBusAccessUs = (12 + 2 * 5) * kBitUs;
// The PC writes each frame in one go, so a short pause on USB ends it.
constexpr uint32_t kUsbFrameEndUs = 3000;

static uint8_t busFrame[64];
static size_t busLen = 0;
static uint32_t lastBusByteUs = 0;

static uint8_t pcFrame[kMaxFrame];
static size_t pcLen = 0;
static uint32_t lastPcByteUs = 0;

static void readBus() {
  while (Serial1.available()) {
    if (busLen < sizeof(busFrame)) {
      busFrame[busLen++] = Serial1.read();
    } else {
      Serial1.read();  // longer than any J1708 frame: noise, drop it
    }
    lastBusByteUs = micros();
  }
  if (busLen && micros() - lastBusByteUs >= kFrameEndUs) {
    Serial.write(busFrame, busLen);
    busLen = 0;
  }
}

static void readPc() {
  while (pcLen < kMaxFrame && Serial.available()) {
    pcFrame[pcLen++] = Serial.read();
    lastPcByteUs = micros();
  }
}

static bool pcFrameReady() {
  return pcLen == kMaxFrame || (pcLen && micros() - lastPcByteUs >= kUsbFrameEndUs);
}

static bool busIdle() {
  return busLen == 0 && !Serial1.available() && micros() - lastBusByteUs >= kBusAccessUs;
}

static void sendPcFrame() {
  digitalWrite(kMarker, HIGH);
  Serial1.write(pcFrame, pcLen);
  Serial1.flush();  // returns once the last stop bit is out
  digitalWrite(kMarker, LOW);
  lastBusByteUs = micros();
  pcLen = 0;
}

void setup() {
  // Hold CTX recessive so a wired SN65HVD230 can't jam CAN1.
  pinMode(kTwaiTx, OUTPUT);
  digitalWrite(kTwaiTx, HIGH);
  pinMode(kMarker, OUTPUT);
  digitalWrite(kMarker, LOW);

  Serial.setTxTimeoutMs(0);  // don't stall when no PC is listening
  Serial.begin(115200);      // USB ignores the baud rate

  Serial1.setRxBufferSize(1024);
  Serial1.begin(kBaud, SERIAL_8N1, kJ1708Rx, kJ1708Tx);
  Serial1.setRxFIFOFull(1);  // hand over each byte at once, for idle timing
}

void loop() {
  readBus();
  readPc();
  if (pcFrameReady() && busIdle()) {
    sendPcFrame();
  }
}
