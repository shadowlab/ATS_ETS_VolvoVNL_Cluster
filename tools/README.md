# Tools

PC-side scripts for the bench. Install with `pip install -r requirements.txt`
(Python 3.9 or newer).

| Script | What it does |
|---|---|
| `can_listen.py` | Logs CAN1 and summarizes each source and PGN, with Requests (0xEA00) and Address Claimed (0xEE00) decoded |
| `can_send.py` | Sends one PGN on repeat, for hand tests and waking the cluster |
| `j1708_listen.py` | Logs J1708/J1587 frames from a USB-serial adapter on the XY-485 |
| `sweep_watch.py` | Unattended J1939 sweep with a camera watching the cluster; logs hits with before and after pictures |
| `canbus.py`, `j1939.py`, `j1587.py` | Shared helpers; `canbus.py` opens the CAN adapter, including candleLight on Windows |
| `test_*.py` | Offline checks: `python -m unittest test_tools test_canbus test_sweep_watch` |

## First power-up session

With the CANable 2.0 on CAN1 (A:15/A:16) and a USB-serial adapter on the
XY-485 (A:17/A:18), run both listeners in separate windows, then power the
cluster as in [docs/pinout.md](../docs/pinout.md#first-power-up):

```
python can_listen.py --interface slcan --channel COM5 --log first_power_can.csv --quiet
python j1708_listen.py --port COM6 --log first_power_j1708.csv
```

With candleLight firmware on the CANable, use `--interface gs_usb --channel 0`
instead (see [CANable 2.0 on Windows](#canable-20-on-windows-candlelight)).
After a minute, Ctrl+C prints a summary. Run `can_listen.py` again with
`--poke` to ask every node to claim its address.

If the cluster stays dark with B:23 (ACC) powered (it has no ignition pin,
so it may wait for bus traffic), give it something to hear:

```
python can_send.py --channel COM5 --pgn F004 --sa 00 --data "FF FF FF 40 1F FF FF FF" --period 0.01
```

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
