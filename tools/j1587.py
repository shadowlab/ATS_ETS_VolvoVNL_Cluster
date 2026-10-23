"""J1708 framing and J1587 decoding shared by the tools.

A J1708 frame is MID + data + checksum, where the checksum makes the byte sum
0 mod 256. Frames are separated by bus idle time, which a USB-serial adapter
usually hides, so frames are also split by checksum.

MID and PID numbers are standard SAE assignments from memory (see
docs/messages.md), not checked against the paid SAE documents.
"""

MAX_FRAME = 21

MID_NAMES = {
    128: "Engine",
    130: "Transmission",
    136: "Brakes",
    140: "Instrument cluster",
    142: "Vehicle management",
    144: "Vehicle ECU (likely)",
}

PID_NAMES = {
    0: "Request parameter",
    84: "Road speed",
    96: "Fuel level",
    100: "Oil pressure",
    110: "Coolant temperature",
    117: "Air pressure primary",
    118: "Air pressure secondary",
    128: "Component-specific request",
    168: "Battery voltage",
    190: "Engine speed",
    194: "Diagnostic codes",
    245: "Total distance",
}


def checksum(body):
    """Checksum byte for MID + data."""
    return (-sum(body)) & 0xFF


def make_frame(mid, data):
    body = bytes([mid]) + bytes(data)
    return body + bytes([checksum(body)])


def is_valid(frame):
    """Checksum is good and the parameters fill the data exactly."""
    return 2 <= len(frame) <= MAX_FRAME and sum(frame) & 0xFF == 0 and params_fit(frame[1:-1])


def split_frames(chunk):
    """Split bytes that arrived together into valid frames.

    Two valid frames back to back always pass the checksum as one, so the split
    with the most frames wins. Returns None if the bytes can't be split cleanly.
    """
    n = len(chunk)
    best = [None] * (n + 1)
    best[0] = []
    for end in range(2, n + 1):
        for start in range(max(0, end - MAX_FRAME), end - 1):
            if best[start] is None:
                continue
            frame = chunk[start:end]
            if is_valid(frame) and (best[end] is None or len(best[start]) + 1 > len(best[end])):
                best[end] = best[start] + [bytes(frame)]
    return best[n]


def params_fit(data):
    """True if the parameters end exactly at the end of the data."""
    return sum(_param_spans(data)) == len(data) if data else True


def _param_spans(data):
    spans = []
    i = 0
    while i < len(data):
        start = i
        pid = data[i]
        i += 1
        if pid == 255:
            if i >= len(data):
                return [len(data) + 1]
            pid = data[i]
            i += 1
        if pid < 128:
            i += 1
        elif pid < 192:
            i += 2
        else:
            if i >= len(data):
                return [len(data) + 1]
            i += 1 + data[i]
        spans.append(i - start)
    return spans


def parse_params(data):
    """Split J1587 data (no MID, no checksum) into (pid, value bytes) pairs."""
    params = []
    i = 0
    while i < len(data):
        pid = data[i]
        i += 1
        if pid == 255 and i < len(data):
            pid = 256 + data[i]
            i += 1
        low = pid & 0xFF
        if low < 128:
            size = 1
        elif low < 192:
            size = 2
        elif i < len(data):
            size = data[i]
            i += 1
        else:
            size = 0
        params.append((pid, bytes(data[i:i + size])))
        i += size
    return params


def describe(frame):
    mid = frame[0]
    parts = [f"MID {mid} {MID_NAMES.get(mid, '')}".rstrip()]
    for pid, value in parse_params(frame[1:-1]):
        text = f"PID {pid} {PID_NAMES.get(pid, '')}".rstrip() + f" [{value.hex(' ')}]"
        if pid == 0 and value:
            text += f" asks for PID {value[0]} {PID_NAMES.get(value[0], '')}".rstrip()
        elif pid == 128 and len(value) == 2:
            text += f" asks MID {value[1]} for PID {value[0]} {PID_NAMES.get(value[0], '')}".rstrip()
        parts.append(text)
    return "  ".join(parts)
