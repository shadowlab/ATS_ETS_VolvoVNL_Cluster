"""J1708 framing and J1587 decoding shared by the tools.

A J1708 frame is MID + data + checksum, where the checksum makes the byte sum
0 mod 256. Frames are separated by bus idle time, which a USB-serial adapter
usually hides, so frames are also split by checksum.

MID and PID numbers are standard SAE assignments from memory (see
docs/messages.md), not checked against the paid SAE documents. The MID list
and SIDs 200-250 come from Volvo service bulletin 371-44 (docs/data_links.md).
"""

MAX_FRAME = 21
DATA_LINK_ESCAPE = 254

MID_NAMES = {
    128: "Engine",
    130: "Transmission",
    136: "Brakes",
    140: "Instrument cluster",
    142: "Satellite communications",
    144: "Vehicle ECU",
    146: "Climate control",
    172: "Test tool",
    206: "Radio",
    216: "Lighting control module",
    219: "VORAD",
    232: "Airbag",
    249: "Body builder module",
    250: "Steering wheel module",
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

# Volvo data link SIDs from bulletin 371-44. A cluster fault on one of these
# names the module it is missing.
SID_NAMES = {
    200: "Data link, engine ECU (MID 128)",
    201: "Data link, vehicle ECU (MID 144)",
    202: "Data link, instrument cluster (MID 140)",
    204: "Data link, ABS (MID 136)",
    210: "Data link, lighting control module (MID 216)",
    214: "Data link, body builder module (MID 249)",
    231: "J1939 data link",
    250: "J1587/J1708 data link",
}

FMI_NAMES = {
    0: "high", 1: "low", 2: "erratic", 3: "shorted high", 4: "shorted low",
    5: "open circuit", 6: "short circuit", 7: "no mechanical response",
    8: "abnormal frequency", 9: "abnormal update rate", 10: "abnormal rate of change",
    11: "unknown failure", 12: "device lost", 13: "out of calibration", 14: "special",
}


def decode_diag_codes(data):
    """Decode PID 194 diagnostic codes (the bytes after the count).

    Each code is an id byte and a code byte, then an occurrence count when bit 8
    of the code byte is set. Code byte: bit 7 inactive, bit 6 standard (clear
    means a page 2 PID), bit 5 SID rather than PID, bits 4-1 FMI.
    """
    codes = []
    i = 0
    while i + 1 < len(data):
        ident, code = data[i], data[i + 1]
        i += 2
        count = None
        if code & 0x80 and i < len(data):
            count = data[i]
            i += 1
        if code & 0x10:
            kind, name = "SID", SID_NAMES.get(ident, "")
        elif code & 0x20:
            kind, name = "PID", PID_NAMES.get(ident, "")
        else:
            kind, name, ident = "PID", "", ident + 256
        fmi = code & 0x0F
        text = f"{kind} {ident}" + (f" {name}" if name else "") + f" FMI {fmi} {FMI_NAMES[fmi]}"
        text += " (inactive" if code & 0x40 else " (active"
        text += f", x{count})" if count is not None else ")"
        codes.append(text)
    return codes


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
        extended = pid == 255
        if extended:
            if i >= len(data):
                return [len(data) + 1]
            pid = data[i]
            i += 1
        if pid < 128:
            i += 1
        elif pid < 192:
            i += 2
        elif pid == DATA_LINK_ESCAPE and not extended:
            if i + 1 >= len(data):
                return [len(data) + 1]
            i += 2 + data[i + 1]
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
        if pid == DATA_LINK_ESCAPE and i + 1 < len(data):
            # Seen on the bench: PID 254, destination MID, length, data.
            size = data[i + 1]
            params.append((pid, bytes([data[i]]) + bytes(data[i + 2:i + 2 + size])))
            i += 2 + size
            continue
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
        if pid == DATA_LINK_ESCAPE and value:
            text = f"PID 254 data link escape to MID {value[0]} [{value[1:].hex(' ')}]"
            inner = value[2:]
            if len(inner) >= 2 and inner[0] == 194 and inner[1] == len(inner) - 2:
                text += " codes: " + "; ".join(decode_diag_codes(inner[2:]))
        elif pid == 194 and value:
            text += " codes: " + "; ".join(decode_diag_codes(value))
        elif pid == 192 and len(value) >= 2:
            text += f" section {(value[1] & 0x0F) + 1} of {(value[1] >> 4) + 1} of PID {value[0]}"
        elif pid == 0 and value:
            text += f" asks for PID {value[0]} {PID_NAMES.get(value[0], '')}".rstrip()
        elif pid == 128 and len(value) == 2:
            text += f" asks MID {value[1]} for PID {value[0]} {PID_NAMES.get(value[0], '')}".rstrip()
        parts.append(text)
    return "  ".join(parts)
