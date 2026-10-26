# Firmware

ESP32-S3 firmware, built with PlatformIO (Arduino framework). Pins follow
[docs/wiring.md](../docs/wiring.md).

## Stage 1: J1708 bridge

`src/main.cpp` turns the board into a J1708 ↔ USB bridge for the sweeps,
while the CANable does all CAN1 work.

- **Bus to PC:** each frame from the XY-485 (GPIO18) goes to USB as it
  arrived, so `tools/j1708_listen.py --port COMx` works as with a USB-serial
  adapter.
- **PC to bus:** write one whole frame (MID, data, checksum) per write. The
  board waits for the bus to be idle (22 bit times, J1708 priority 5) and
  sends it on GPIO17, holding GPIO6 high while it goes out.
- GPIO4 (SN65HVD230 CTX) is held high, so a wired transceiver stays recessive.

Use the board's native USB port (often labelled USB, not COM or UART). The
baud rate set on the PC side is ignored. If the XY-485 echoes what it sends,
those frames come back to the PC too.

## Builds and flashing

GitHub Actions builds every PR and push that touches `firmware/`. Each merge
to main publishes a release (`fw-build-<run>`), and `fw-v*` tags publish a
versioned one. The newest main build is always at
<https://github.com/shadowlab/ATS_ETS_VolvoVNL_Cluster/releases/latest/download/vnl-bridge.bin>,
a merged image to write at 0x0.

On the PC, [`tools/flash_ci.ps1`](../tools/flash_ci.ps1) downloads, flashes
and listens for 10 seconds:

```
.\flash_ci.ps1 -Port COM7          # latest release
.\flash_ci.ps1 -Port COM7 -Pr 7    # a PR's build
```

By hand: `python -m esptool --chip esp32s3 --port COM7 write_flash 0x0 vnl-bridge.bin`.
