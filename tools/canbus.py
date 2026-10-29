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

set_gs_usb_mode() switches an open candleLight bus to loopback or listen-only, and describe_error() puts error frames into words.

Never format a GsUsbBus with str() or an f-string: python-can's __str__ for
it returns an int and raises TypeError.
"""

import inspect

import can

SAMPLE_POINT = 87.5
MIN_TSEG2 = 3  # keeps SJW at 3, as in the timing tested on the CANable 2.0
GS_USB_BREQ_MODE = 2  # gs_usb control request and mode, as in gs_usb.gs_usb
GS_CAN_MODE_START = 1


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
    import usb.util
    from gs_usb.gs_usb import GsUsb

    prime_libusb()
    devices = GsUsb.scan()
    if len(devices) <= index:
        raise SystemExit(f"No candleLight device {index} (found {len(devices)}). "
                         "Check the WinUSB driver in Zadig and that libusb-package is installed.")
    device = devices[index]
    try:
        capability = device.device_capability
    finally:
        # WinUSB allows one open handle per device, and GsUsbBus opens its own.
        usb.util.dispose_resources(device.gs_usb)
        del device, devices
    timing = pick_timing(fclk or capability.fclk_can, bitrate, sample_point, capability)
    print(f"gs_usb timing: {timing}")

    kwargs = {"channel": index, "bitrate": bitrate}
    if "timing" in inspect.signature(GsUsbBus.__init__).parameters:
        kwargs["timing"] = timing
    # Built in two steps so a failed open can be marked shut down; otherwise
    # python-can's __del__ retries shutdown on a half-made bus, which crashes.
    bus = GsUsbBus.__new__(GsUsbBus)
    original = can.BitTiming.__dict__["from_sample_point"]
    if "timing" not in kwargs:
        # python-can 4.6 builds its own 87.5% timing inside __init__; hand it ours.
        can.BitTiming.from_sample_point = classmethod(lambda cls, *a, **k: timing)
    try:
        bus.__init__(**kwargs)
    except BaseException:
        bus._is_shutdown = True
        raise
    finally:
        can.BitTiming.from_sample_point = original
    bus.vnl_timing = timing
    return bus


def set_gs_usb_mode(bus, loopback=False, listen_only=False):
    """Restart an open gs_usb bus in another mode, keeping our bit timing.
    False if the firmware doesn't offer the mode (the bus is left as it was)."""
    from gs_usb import constants as c
    from gs_usb.gs_usb_structures import DeviceMode

    dev = bus.gs_usb
    want = (c.GS_CAN_MODE_LOOP_BACK if loopback else 0) | (c.GS_CAN_MODE_LISTEN_ONLY if listen_only else 0)
    feature = dev.device_capability.feature
    if want & ~feature:
        return False
    # The mode request directly, as the Linux driver does: GsUsb.start() resets
    # the USB device first, and the timing has to be set again after a stop.
    dev.stop()
    t = bus.vnl_timing
    dev.set_timing(prop_seg=1, phase_seg1=t.tseg1 - 1, phase_seg2=t.tseg2, sjw=t.sjw, brp=t.brp)
    dev.device_flags = want | (feature & c.GS_CAN_MODE_HW_TIMESTAMP)
    dev.gs_usb.ctrl_transfer(0x41, GS_USB_BREQ_MODE, 0, 0, DeviceMode(GS_CAN_MODE_START, dev.device_flags).pack())
    return True

# SocketCAN error frame classes (linux/can/error.h), which candleLight uses.
ERROR_CLASSES = [(0x001, "TX timeout"), (0x002, "lost arbitration"), (0x004, "controller"),
                 (0x008, "protocol violation"), (0x010, "transceiver"), (0x020, "no ACK"),
                 (0x040, "bus off"), (0x080, "bus error"), (0x100, "restarted")]
CONTROLLER_STATES = [(0x01, "RX overflow"), (0x02, "TX overflow"), (0x04, "RX warning"),
                     (0x08, "TX warning"), (0x10, "RX passive"), (0x20, "TX passive"), (0x40, "back to active")]
PROTOCOL_ERRORS = [(0x01, "bit"), (0x02, "form"), (0x04, "stuff"), (0x08, "dominant bit"),
                   (0x10, "recessive bit"), (0x20, "overload"), (0x40, "active error"), (0x80, "while sending")]


def error_classes(msg):
    return {name for bit, name in ERROR_CLASSES if msg.arbitration_id & bit}


def describe_error(msg, counters=True):
    """Plain words for an error frame, with the TX/RX error counters."""
    data = bytes(msg.data) + bytes(8)
    parts = [name for bit, name in ERROR_CLASSES if msg.arbitration_id & bit]
    if msg.arbitration_id & 0x004:
        parts += [name for bit, name in CONTROLLER_STATES if data[1] & bit]
    if msg.arbitration_id & 0x008:
        parts += [name + " error" for bit, name in PROTOCOL_ERRORS if data[2] & bit]
    text = ", ".join(parts) or f"error frame {msg.arbitration_id:08X}"
    if counters and msg.dlc >= 8:
        text += f" (TX errors {data[6]}, RX errors {data[7]})"
    return text


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
