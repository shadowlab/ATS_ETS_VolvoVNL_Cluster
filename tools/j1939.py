"""J1939 helpers shared by the tools: 29-bit ID packing and a few PGN names.

PGN numbers are standard SAE assignments from memory (see docs/messages.md),
not checked against the paid SAE documents.
"""

GLOBAL = 0xFF

PGN_NAMES = {
    0xE800: "ACK",
    0xEA00: "Request",
    0xEB00: "TP.DT",
    0xEC00: "TP.CM",
    0xEE00: "Address Claimed",
    0xF001: "EBC1",
    0xF003: "EEC2",
    0xF004: "EEC1",
    0xF005: "ETC2",
    0xFE56: "AT1T1I",
    0xFEAE: "AIR1",
    0xFEC1: "VDHR",
    0xFECA: "DM1",
    0xFEE5: "HOURS",
    0xFEE6: "TD",
    0xFEE9: "LFC",
    0xFEEE: "ET1",
    0xFEEF: "EFL/P1",
    0xFEF1: "CCVS1",
    0xFEF2: "LFE",
    0xFEF5: "AMB",
    0xFEF6: "IC1",
    0xFEF7: "VEP1",
    0xFEFC: "DD",
}


def pgn_name(pgn):
    if pgn in PGN_NAMES:
        return PGN_NAMES[pgn]
    if 0xFF00 <= pgn <= 0xFFFF:
        return "Proprietary B"
    if pgn == 0xEF00:
        return "Proprietary A"
    return ""


def parse_id(can_id):
    """Split a 29-bit ID into (priority, pgn, source, destination)."""
    priority = (can_id >> 26) & 0x7
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 0xF0:
        return priority, (dp << 16) | (pf << 8), sa, ps
    return priority, (dp << 16) | (pf << 8) | ps, sa, GLOBAL


def make_id(pgn, sa, da=GLOBAL, priority=6):
    """Build a 29-bit ID. For PDU1 PGNs (PF < 0xF0), da goes in the PS byte."""
    pf = (pgn >> 8) & 0xFF
    if pf < 0xF0:
        pgn = (pgn & 0x3FF00) | da
    return ((priority & 0x7) << 26) | ((pgn & 0x3FFFF) << 8) | (sa & 0xFF)


def requested_pgn(data):
    """The PGN asked for in a Request (0xEA00) payload."""
    if len(data) < 3:
        return None
    return data[0] | (data[1] << 8) | (data[2] << 16)


def decode_name(data):
    """Decode the 64-bit NAME carried in an Address Claimed (0xEE00) payload."""
    if len(data) < 8:
        return None
    n = int.from_bytes(bytes(data[:8]), "little")
    return {
        "identity": n & 0x1FFFFF,
        "manufacturer": (n >> 21) & 0x7FF,
        "ecu_instance": (n >> 32) & 0x7,
        "function_instance": (n >> 35) & 0x1F,
        "function": (n >> 40) & 0xFF,
        "vehicle_system": (n >> 49) & 0x7F,
        "vehicle_system_instance": (n >> 56) & 0xF,
        "industry_group": (n >> 60) & 0x7,
        "arbitrary_address": (n >> 63) & 0x1,
    }


def describe(can_id, data):
    """One-line description of a frame, with requests and claims decoded."""
    _, pgn, sa, da = parse_id(can_id)
    text = f"SA {sa:02X} -> {da:02X}  PGN {pgn:05X} {pgn_name(pgn)}"
    if pgn == 0xEA00:
        req = requested_pgn(data)
        if req is not None:
            text += f"  asks for {req:05X} {pgn_name(req)}"
    elif pgn == 0xEE00:
        name = decode_name(data)
        if name:
            text += "  NAME " + " ".join(f"{k}={v}" for k, v in name.items())
    return text
