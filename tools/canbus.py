"""Open and close the CAN adapter, with the workarounds candleLight needs.

The CANable 2.0 runs either slcan firmware (a COM port) or candleLight
(python-can's gs_usb interface, a WinUSB device on Windows). gs_usb needs
three things python-can 4.6 doesn't do on its own:

- pyusb has to be pointed at the libusb DLL from libusb-package, or the scan
  finds no devices on Windows.
- python-can always asks for an 87.5% sample point, which the CANable 2.0's
  170 MHz clock can't hit at 250 kbit/s ("No suitable bit timings found").
  The timing is worked out here from the clock the device reports instead.
- Shutdown can raise from the device reset python-can does on close.

Never format a GsUsbBus with str() or an f-string: python-can's __str__ for
it returns an int and raises TypeError.
"""

import inspect

import can

SAMPLE_POINT = 87.5
MIN_TSEG2 = 3  # keeps SJW at 3, as in the timing tested on the CANable 2.0


def add_bus_args(ap):
    ap.add_argument("--interface", default="slcan", help="python-can interface: slcan, gs_usb (candleLight), socketcan, virtual")
    ap.add_argument("--channel", required=True, help="COM port for slcan, device number (0) for gs_usb, can0 for socketcan")
    ap.add_argument("--bitrate", type=int, default=250000)
    ap.add_argument("--sample-point", type=float, default=SAMPLE_POINT, help="gs_usb only: target sample point in percent")
    ap.add_argument("--fclk", type=int, help="gs_usb only: CAN clock in Hz, if the device can't report it")


def pick_timing(f_clock, bitrate, sample_point=SAMPLE_POINT, limits=None):
    """The exact-bitrate timing closest to sample_point, within python-can's
    limits and the device's (a gs_usb DeviceCapability, if given)."""
    tseg1_max, tseg2_max, sjw_max, brp_max = 16, 8, 4, 64
    tseg1_min, tseg2_min, brp_min = 1, MIN_TSEG2, 1
    if limits is not None:
        tseg1_min, tseg1_max = max(tseg1_min, limits.tseg1_min), min(tseg1_max, limits.tseg1_max)
        tseg2_min, tseg2_max = max(tseg2_min, limits.tseg2_min), min(tseg2_max, limits.tseg2_max)
        sjw_max = min(sjw_max, limits.sjw_max)
        brp_min, brp_max = max(brp_min, limits.brp_min), min(brp_max, limits.brp_max)
    best = None
    for brp in range(brp_min, brp_max + 1):
        if f_clock % (bitrate * brp):
            continue
        nbt = f_clock // (bitrate * brp)
        for tseg2 in range(tseg2_min, tseg2_max + 1):
            tseg1 = nbt - 1 - tseg2
            if not tseg1_min <= tseg1 <= tseg1_max:
                continue
            error = abs(100 * (1 + tseg1) / nbt - sample_point)
            if best is None or error < best[0]:
                best = (error, brp, tseg1, tseg2)
    if best is None:
        raise ValueError(f"No bit timing for {bitrate} bit/s from a {f_clock} Hz clock")
    _, brp, tseg1, tseg2 = best
    return can.BitTiming(f_clock=f_clock, brp=brp, tseg1=tseg1, tseg2=tseg2, sjw=min(tseg2, sjw_max))


def prime_libusb():
    """Point pyusb at libusb-package's DLL. pyusb keeps the first backend it
    loads, so gs_usb's later scans use this one."""
    try:
        import libusb_package
        import usb.backend.libusb1
    except ImportError:
        return
    usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)


def open_gs_usb(index, bitrate, sample_point=SAMPLE_POINT, fclk=None):
    from can.interfaces.gs_usb import GsUsbBus
    from gs_usb.gs_usb import GsUsb

    prime_libusb()
    devices = GsUsb.scan()
    if len(devices) <= index:
        raise SystemExit(f"No candleLight device {index} (found {len(devices)}). "
                         "Check the WinUSB driver in Zadig and that libusb-package is installed.")
    capability = devices[index].device_capability
    timing = pick_timing(fclk or capability.fclk_can, bitrate, sample_point, capability)
    print(f"gs_usb timing: {timing}")

    if "timing" in inspect.signature(GsUsbBus.__init__).parameters:
        return GsUsbBus(channel=index, bitrate=bitrate, timing=timing)
    # python-can 4.6 builds its own 87.5% timing inside __init__; hand it ours.
    original = can.BitTiming.__dict__["from_sample_point"]
    can.BitTiming.from_sample_point = classmethod(lambda cls, *a, **k: timing)
    try:
        return GsUsbBus(channel=index, bitrate=bitrate)
    finally:
        can.BitTiming.from_sample_point = original


def open_bus(args):
    if args.interface == "gs_usb":
        return open_gs_usb(int(args.channel), args.bitrate, args.sample_point, args.fclk)
    return can.Bus(interface=args.interface, channel=args.channel, bitrate=args.bitrate)


def close_bus(bus):
    """Shut the bus down without letting a driver error hide the results."""
    try:
        bus.shutdown()
    except Exception as e:  # noqa: BLE001 - any driver error here is only worth a warning
        print(f"Warning: CAN shutdown failed ({type(e).__name__}: {e})")
