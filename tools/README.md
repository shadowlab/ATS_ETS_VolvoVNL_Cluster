# Tools

PC-side scripts for the bench. Install with `pip install -r requirements.txt`
(Python 3.9 or newer).

| Script | What it does |
|---|---|
| `can_selftest.py` | Checks the CANable, with or without the cluster: opens it, loops frames back through it, tests sending, and checks whether anything on CAN1 ACKs |
| `can_listen.py` | Logs CAN1 and summarizes each source and PGN, with Requests (0xEA00) and Address Claimed (0xEE00) decoded; `--live` for a table that updates in place; error frames counted and explained |
| `can_send.py` | Sends one PGN on repeat, for hand tests and waking the cluster |
| `j1708_listen.py` | Logs J1708/J1587 frames from a USB-serial adapter on the XY-485 |
| `la_capture.py` | Captures with the 8-channel logic analyzer through sigrok-cli, saves a PulseView file, and decodes J1708 to CSV; `selftest` checks it through the ESP32 bridge with no cluster |
| `sweep_watch.py` | Unattended J1939 sweep with a camera watching the cluster; logs hits with before and after pictures |
| `canbus.py`, `j1939.py`, `j1587.py` | Shared helpers; `canbus.py` opens the CAN adapter, including candleLight on Windows |
| `test_*.py` | Offline checks: `python -m unittest test_tools test_canbus test_selftest test_la_capture test_sweep_watch` |

## Before the cluster arrives

Check the CANable and the PC side with nothing wired to it. Leave the
CANable's 120 Ω jumper as it is.

```
python can_selftest.py --interface gs_usb --channel 0
```

With candleLight on the CANable 2.0 it prints:

```
gs_usb timing: BR: 250_000 bit/s, SP: 85.00%, BRP: 34, TSEG1: 16, TSEG2: 3, SJW: 3, ...
[PASS] Open: gs_usb 0 at 250000 bit/s
[WARN] Loopback: the firmware took the frames (echoes came back) but didn't loop them back. ...
[INFO] Bus: silent for 2 s (expected with the cluster unplugged or off)
[INFO] Bus: sent the test frame, but the firmware doesn't report bus errors ...
```

That's as far as this adapter can go without a second CAN node. The
CANable 2.0's candleLight build (STM32G4) lists loopback and listen-only
but always runs the controller in normal mode, and has no bus error
reporting, so the WARN and INFO are expected. What the run does prove is
the driver, libusb, the bit timing, and frames going to the CANable and
coming back over USB. Other candleLight adapters (STM32F0/F4/G0) loop back
and report errors, and pass both steps.

- **Open fails:** the driver or libusb, see
  [CANable 2.0 on Windows](#canable-20-on-windows-candlelight).
- **Loopback FAIL, or no echoes:** USB or firmware. Try another cable and
  USB port, then reflash candleLight.

Then try the live view, which should open, show a header with zero frames,
and close cleanly with Ctrl+C:

```
python can_listen.py --interface gs_usb --channel 0 --live
```

When the cluster is powered on CAN1, run the self-test again. The bus step
should now say it heard frames and that the cluster answered the request,
which proves wiring, termination and bitrate both ways. Since this
firmware can't report errors, a wiring fault shows as silence instead:
check termination (about 60 Ω across A:15/A:16 with power off, see
[wiring](../docs/wiring.md)), swapped H/L, and the bitrate. On adapters that
do report errors, "traffic but error frames" points at the same things.

## Logic analyzer (Lonely Binary 8-channel)

It's a 24 MHz FX2 analyzer, which sigrok drives with its `fx2lafw` driver.
PulseView is the viewer and `la_capture.py` drives `sigrok-cli` for logs.

1. Install **PulseView** and **sigrok-cli** from sigrok.org (Downloads,
   the Windows installers for each; the PulseView one includes Zadig).
2. Plug in the analyzer, open **Zadig** (Start menu, sigrok folder), choose
   Options > List All Devices, select the analyzer and install **WinUSB**.
   The first time sigrok talks to it, it reloads its firmware and shows up
   as a new USB device ("fx2lafw"); if the next step finds nothing, run
   Zadig again and give that one WinUSB too.
3. Check it's seen:

```
python la_capture.py scan
```

It should list an `fx2lafw` device with channels D0–D7. The board's CH0–CH7
are sigrok's D0–D7, the same numbers as in
[docs/wiring.md](../docs/wiring.md#logic-analyzer-8-channels).

### Self-test without the cluster

With the ESP32-S3 running the J1708 bridge firmware on its native USB port,
clip D2 to GPIO17, D4 to GPIO6 and the analyzer's GND to the ESP32's GND.
Nothing else needs to be wired. Then:

```
python la_capture.py selftest --bridge COM7
```

It sends ten J1587 frames through the bridge while capturing for three
seconds and expects:

```
[PASS] Analyzer found: fx2lafw - ...
[PASS] Captured 3 s at 2m (..., opens in PulseView)
[PASS] J1708 TX (D2): all 10 frames decoded intact
[PASS] Marker (D4): 10 pulses, every frame inside one
```

That proves the driver, the capture and decode chain, the ESP32's UART at
9600 and its sweep marker. If TX decodes nothing, check the clips and the
COM port; if the marker fails, check D4 on GPIO6.

### Capturing

```
python la_capture.py capture --seconds 30 --out first_power.sr --log first_power_la.csv
python la_capture.py decode first_power.sr
```

The CSV has every J1708 frame on TX (D2) and RX (D3), with times from the
start of the capture and a `marker` column for frames sent under the sweep
marker. Open the `.sr` in PulseView to look at the waveforms: add a UART
decoder on D3 at 9600 8N1 for J1708, and in stage 2 a CAN decoder on D1 at
250 kbit/s. The default 2 MHz sample rate gives 8 samples per CAN bit; raise
it with `--samplerate 4m` if CAN decoding looks unreliable.

### J1708 frames in PulseView

PulseView's UART decoder shows J1708 as loose bytes. The third-party
[sr-j1708](https://github.com/TruckHacking/sr-j1708) decoder (MIT) stacks on
it and splits them into J1708 messages. To install it on
Windows:

1. Download the repo as a zip (Code > Download ZIP).
2. Make a folder `j1708` in
   `C:\Program Files\sigrok\PulseView\share\libsigrokdecode\decoders`
   and put the zip's `__init__.py` and `pd.py` in it (Windows asks for admin
   rights).
3. Restart PulseView.

To use it, add a UART decoder with RX on D3, 9600 baud, 8N1, LSB first.
Then open that decoder's menu, choose Stack Decoder and pick J1708. It only
reads the UART's RX line, so for the frames the ESP32 sends add a second
UART with RX on D2 and stack another J1708 on that. `la_capture.py` doesn't
need it, since it does its own J1708 decoding.

## First power-up session

With the CANable 2.0 on CAN1 (A:15/A:16) and a USB-serial adapter on the
XY-485 (A:17/A:18), run both listeners in separate windows, then power the
cluster as in [docs/pinout.md](../docs/pinout.md#first-power-up):

```
python can_listen.py --interface gs_usb --channel 0 --poke --log first_power_can.csv
python j1708_listen.py --port COM6 --log first_power_j1708.csv
```

That's for candleLight firmware on the CANable (see
[CANable 2.0 on Windows](#canable-20-on-windows-candlelight)); with slcan
firmware use `--interface slcan --channel COM5` instead.
After a minute, Ctrl+C prints a summary. `--poke` asks every node to claim
its address; run `can_listen.py` again with `--live` to watch
each PGN's bytes change in place (bytes that just changed are highlighted,
sources gone quiet are dimmed). Error frames are printed and counted either
way, on adapters that report them (the CANable 2.0's candleLight doesn't). To open a log in SavvyCAN, name it `.asc` instead of `.csv`.

If the bus stays silent, first check the cluster is on it at all, with the
cluster powered:

```
python can_selftest.py --interface gs_usb --channel 0
```

`[PASS] ACK: all 12 test frames went out` means the cluster is ACKing, so the
wiring, termination and bitrate are right and it just isn't talking. `[WARN]
ACK: only 3 of 12` means nobody ACKed. Run it once with CAN H/L unplugged to
see that "nobody" number on your adapter; the check counts the CANable's
transmit echoes, since the CANable 2.0 reports no bus errors.

Then give the cluster something to hear (it has no ignition pin, so it may
wait for an engine ECU), while logging what comes back:

```
python can_listen.py --interface gs_usb --channel 0 --send "F004:00:FF FF FF 40 1F FF FF FF" --send-period 0.01 --log eec1.csv
```

That's EEC1 at 1000 rpm, so the tach should move. Repeat `--send` for more
frames. On Windows only one program can open a gs_usb CANable at a time, so
use `--send` rather than `can_send.py` in a second window, which fails with
"Access denied".

Save the logs, and copy what the cluster requested into
[docs/messages.md](../docs/messages.md).

## CANable 2.0 on Windows (candleLight)

candleLight firmware makes the CANable a native USB device instead of a COM
port, which python-can reaches through its `gs_usb` interface.

1. Flash candleLight with the CANable updater (canable.io/updater), if it
   still runs slcan.
2. In [Zadig](https://zadig.akeo.ie/), choose Options > List All Devices,
   select the CANable (it shows as a gs_usb or candleLight device) and
   install the **WinUSB** driver.
3. `pip install -r requirements.txt`. This brings in `libusb-package`,
   which supplies the libusb DLL. Without it the scan reports
   "Devices found: 0".
4. Run any tool with `--interface gs_usb --channel 0`:

```
python can_listen.py --interface gs_usb --channel 0 --log first_power_can.csv --quiet
```

The tools work out the bit timing from the clock the device reports and
print it. On the CANable 2.0 (170 MHz) at 250 kbit/s that's BRP 34, TSEG1
16, TSEG2 3, SJW 3, an 85% sample point. python-can's own choice of 87.5%
can't be hit at that clock, which is where "No suitable bit timings found"
comes from. Use `--sample-point` to aim elsewhere, or `--fclk` if a device
reports no clock.

If Python crashes with an access violation when a tool exits, look for a
stray `libusb-1.0.dll` next to `python.exe` (or anywhere else on PATH) and
remove or rename it, so only the copy from `libusb-package` gets loaded.

## Camera sweep

`sweep_watch.py` sends each candidate frame for a second, watches the
cluster through a camera, and records anything that changes. It's built
around DroidCam over USB, which shows up as a normal camera number.

Setup:

1. Mount the phone square to the cluster and dim the room or hood the
   cluster so the lighting stays steady.
2. In DroidCam, lock exposure, white balance and focus once the cluster is
   lit. Keep the phone plugged in, and turn off screen timeout and battery
   optimization for DroidCam.
3. With the cluster powered and idle, mark areas that change on their own
   (clock, blinking icons). Drag a box around each, Enter after each, Esc
   when done. They're saved to `ignore.json`.

```
python sweep_watch.py pick --camera 0
python sweep_watch.py preview --camera 0
```

In the preview, wave a hand or flip a switch to check that changes get a
red box and the change reading goes over the hit level. Raise
`--pixel-threshold` or `--min-area` if it triggers on noise.

Sweep the Volvo proprietary range from the usual source addresses (about
1.5 s per candidate, so this one is roughly 75 minutes):

```
python sweep_watch.py sweep --channel COM5 --camera 0 --pgns FF00-FFFF --sources 00,03,0B,21,31,17 --payloads 00,FF
```

If the cluster needs to hear an engine to stay awake, add
`--background F004:00:FFFFFF401FFFFFFF` (repeatable) to keep sending a
frame through the whole run.

Each run gets a folder under `sweeps/`. Open its `review.html` to see every
hit with before and after pictures, mark it confirmed or ignored, add a
note, and export the marks to CSV. Stop with Ctrl+C at any time, and rerun
with `--out <that folder>` to pick up where it left off.

To find which bit of a hit does what, sweep it one bit at a time:

```
python sweep_watch.py sweep --channel COM5 --camera 0 --pgns FF23 --sources 21 --payloads 00 --bits
```

Copy confirmed results into [docs/messages.md](../docs/messages.md).

### If the picture is wrong

Run `python sweep_watch.py cameras`. It saves a snapshot from every camera
number and driver into `cameras/`, so you can see which number is the phone.
A solid orange or gray picture is usually DroidCam's placeholder, which it
shows when the phone isn't streaming: open the DroidCam app on the phone and
connect from the DroidCam PC client first. For USB, the phone needs Developer
options with **USB debugging** turned on and the "Allow USB debugging?" prompt
accepted, or the PC client lists no devices. On Windows, if a number shows the
right camera in one driver but not another, pass that driver, for example
`--backend dshow`.

If the phone is mounted upside down or sideways, add `--rotate 180` (or 90
or 270) to `pick`, `preview` and `sweep`. Use the same value for all three,
since the ignore areas are saved in the turned picture's coordinates.

You can also skip the virtual webcam and read the phone's own stream. Close
the DroidCam PC client, then use the address the phone app shows:
`--camera http://<phone-ip>:4747/video`.
