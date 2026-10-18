"""Turns logged frames into values.

1. Passive frames: signals from the dalathegreat/leaf_can_bus_messages DBC files (fetched by
   fetch_dbc.sh, GPL-3.0, not stored in this repo). The set is chosen from the VIN: a "ZE1"
   car uses the ZE1 files, a "ZE0" car the ZE0 EV-CAN file, anything else the AZE0 files.
   There is no ZE0 CAR-CAN file, so the AZE0 one is used for it (marked as such).
2. Polled replies (ZE1 active mode): VIN, odometer, and the LBC group 1 block, using the
   formulas of the "Nissan Leaf 2018" polling list in the same project, plus Battery-Emulator's
   offsets for the 12 V battery. Reply layouts are only decoded for the lengths they are
   documented for.
"""
import os
import dbc

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "dbc", "leaf_can_bus_messages")
FLAG_TX = 4
_cache = {}


def _load(name):
    if name not in _cache:
        p = os.path.join(DIR, name + ".dbc")
        _cache[name] = dbc.load(p) if os.path.exists(p) else None
    return _cache[name]


def dbc_set(vin):
    """({'EV-CAN': (file, messages), ...}, label)"""
    v = vin or ""
    if "ZE1" in v:
        ev, label = "EV-can_ZE1", "ZE1"
    elif "ZE0" in v:
        ev, label = "EV-can_ZE0", "ZE0"
    else:
        ev, label = "EV-can_AZE0", "AZE0 (VIN not recognised)"
    return {"EV-CAN": ev, "CAR-CAN": "CAR-can_AZE0", "AV-CAN": "AV-CAN"}, label


def signals(frames, vin):
    """frames: [(board, chname, can_id, dlc, data)], the latest frame per id."""
    files, label = dbc_set(vin)
    out = []
    for board, chname, cid, dlc, data in frames:
        f = files.get(chname)
        msgs = _load(f) if f else None
        msg = msgs.get(cid) if msgs else None
        if not msg or dlc != msg["dlc"]:               # wrong length: not this message
            continue
        for name, val, unit, text in dbc.decode(msg, data):
            out.append({"ch": chname, "id": f"0x{cid:03X}", "msg": msg["name"], "sig": name,
                        "value": val, "unit": unit, "text": text, "src": f})
    return out, label, all(_load(f) is not None for f in set(files.values()))


# ── polled replies ───────────────────────────────────────────────────────────
def reassemble(frames):
    """frames: chronological [(can_id, data)] of replies. Returns {can_id: payload} of the
    last complete ISO-TP message per id (single and multi-frame)."""
    last, part = {}, {}
    for cid, d in frames:
        if not d:
            continue
        pci = d[0] & 0xF0
        if pci == 0x00:
            n = d[0] & 0x0F
            last[cid] = bytes(d[1:1 + n])
        elif pci == 0x10 and len(d) >= 8:
            part[cid] = {"len": ((d[0] & 0x0F) << 8) | d[1], "buf": bytearray(d[2:8]), "sn": 1}
        elif pci == 0x20 and cid in part:
            p = part[cid]
            if (d[0] & 0x0F) == (p["sn"] & 0x0F):
                p["buf"] += d[1:8]
                p["sn"] += 1
                if len(p["buf"]) >= p["len"]:
                    last[cid] = bytes(p["buf"][:p["len"]])
                    del part[cid]
            else:
                del part[cid]
    return last


def _s32(b):
    return int.from_bytes(b, "big", signed=True)


def polled(replies):
    """[{name, value, unit, src}] from the last complete replies."""
    out = []

    def add(name, value, unit, src):
        out.append({"name": name, "value": value, "unit": unit, "src": src})

    vin = replies.get(0x79A)
    if vin and vin[:2] == b"\x61\x81" and len(vin) >= 19:
        add("VIN", vin[2:19].decode("ascii", "replace"), "", "service 21 81")
    odo = replies.get(0x763)
    if odo and odo[:3] == b"\x62\x0e\x01" and len(odo) >= 6:
        add("Odometer", int.from_bytes(odo[3:6], "big"), "km", "22 0E 01, Leaf 2018 polling list")
    lbc = replies.get(0x7BB)
    if lbc and lbc[:2] == b"\x61\x01" and len(lbc) == 0x35:
        # p = ISO-TP payload including the 61 01 header. First frame u8[n] is p[n-2]; consecutive
        # frame k (0x21, 0x22 ...) u8[n] is p[6+7(k-1)+n-1]. The polling list's data[n] counts the PCI byte.
        p = lbc
        soc = ((p[33] << 16) | (p[34] << 8) | p[35]) / 10000
        ahr = ((p[37] << 16) | (p[38] << 8) | p[39]) / 10000
        add("State of charge", round(soc, 2), "%", "group 1 (polling list formula)")
        add("Pack capacity", round(ahr, 1), "Ah", "group 1 (polling list formula)")
        add("Energy in pack (GIDs x 80 Wh)", round(4.425 * soc * 0.01 * ahr * 0.08, 1), "kWh",
            "derived: 4.425 x SoC x Ah = GIDs")
        add("Pack voltage", round(((p[20] << 8) | p[21]) / 100, 2), "V", "group 1 (polling list formula)")
        add("Pack current (1)", round(_s32(p[2:6]) / 1024, 2), "A", "group 1; may be unreliable on 62 kWh")
        add("Pack current (2)", round(_s32(p[8:12]) / 1024, 2), "A", "group 1; may be unreliable on 62 kWh")
        add("12 V battery", round(((p[22] << 8) | p[23]) / 1000, 2), "V", "group 1 (Battery-Emulator offset)")
        add("Insulation resistance", (p[24] << 8) | p[25], "kOhm",
            "group 1 (polling list formula)")
    return out
