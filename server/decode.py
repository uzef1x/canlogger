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
# Source of every formula below: the "Nissan Leaf 2018" polling list of
# dalathegreat/leaf_can_bus_messages (Leaf2018-CAN.pdf), except where a line says otherwise.
REPLY_IDS = (0x79A, 0x7BB, 0x763, 0x760, 0x78C, 0x765, 0x764)


class Polled:
    """Incremental ISO-TP reassembly. Keeps the last complete reply per (module, service, id)."""

    def __init__(self):
        self.last_rowid = 0
        self.part = {}
        self.msgs = {}          # key -> (payload, t)

    def feed(self, rows):
        """rows: [(rowid, can_id, data, t)] in time order."""
        for rowid, cid, d, t in rows:
            self.last_rowid = max(self.last_rowid, rowid)
            d = bytes(d)
            if not d:
                continue
            pci = d[0] & 0xF0
            if pci == 0x00:
                self._store(cid, d[1:1 + (d[0] & 0x0F)], t)
            elif pci == 0x10 and len(d) >= 8:
                self.part[cid] = {"len": ((d[0] & 0x0F) << 8) | d[1], "buf": bytearray(d[2:8]), "sn": 1}
            elif pci == 0x20 and cid in self.part:
                q = self.part[cid]
                if (d[0] & 0x0F) == (q["sn"] & 0x0F):
                    q["buf"] += d[1:8]
                    q["sn"] += 1
                    if len(q["buf"]) >= q["len"]:
                        self._store(cid, bytes(q["buf"][:q["len"]]), t)
                        del self.part[cid]
                else:
                    del self.part[cid]

    def _store(self, cid, p, t):
        if len(p) >= 3 and p[0] == 0x62:
            self.msgs[(cid, 0x62, (p[1] << 8) | p[2])] = (p, t)
        elif len(p) >= 2 and p[0] == 0x61:
            self.msgs[(cid, 0x61, p[1])] = (p, t)


def _s16(b):
    return int.from_bytes(b, "big", signed=True)


def _s32(b):
    return int.from_bytes(b, "big", signed=True)


def _u(b):
    return int.from_bytes(b, "big")


def _raw_to_f(i):                     # algorithm 1 of the polling list
    if i == 1021: return 1.0
    if i >= 589: return 162.0 - i * 0.181
    if i >= 569: return 57.2 + (579 - i) * 0.18
    if i >= 558: return 60.8 + (558 - i) * 0.16363636363636364
    if i >= 548: return 62.6 + (548 - i) * 0.18
    if i >= 537: return 64.4 + (537 - i) * 0.16363636363636364
    if i >= 447: return 66.2 + (527 - i) * 0.18
    if i >= 438: return 82.4 + (438 - i) * 0.2
    if i >= 428: return 84.2 + (428 - i) * 0.18
    if i >= 365: return 86.0 + (419 - i) * 0.2
    if i >= 357: return 98.6 + (357 - i) * 0.225
    if i >= 348: return 100.4 + (348 - i) * 0.2
    if i >= 316: return 102.2 + (340 - i) * 0.225
    return 109.4 + (309 - i) * 0.2571428571428572


GEAR = {1: "Park", 2: "Reverse", 3: "Neutral", 4: "Drive", 7: "Eco"}
PLUG = {0: "Not plugged", 1: "Partly plugged", 2: "Plugged"}
CHARGE = {0: "Not charging", 1: "Level 1", 2: "Level 2", 3: "Quick charge"}
WIPER_RAIN = {0xf: 1, 0x1f: 1, 0x47: 1, 0x87: 1, 0xd: 2, 0x1d: 2, 0x45: 2, 0x85: 2,
              0xb: 3, 0x13: 3, 0x43: 3, 0x83: 3, 0xa: 4, 0x19: 4, 0x41: 4, 0x81: 4}
WIPER_LEVEL = {0xf: "Off", 0xd: "Off", 0xb: "Off", 0xa: "Off", 0x1f: "Auto", 0x1d: "Auto", 0x13: "Auto",
               0x19: "Auto", 0x47: "Slow", 0x45: "Slow", 0x43: "Slow", 0x41: "Slow",
               0x87: "Fast", 0x85: "Fast", 0x83: "Fast", 0x81: "Fast"}


def _vcm(name, unit, fn, grp):
    return lambda d: [(name, fn(d), unit, grp)]


# (module reply id, DID) -> decoder(payload data after 62 xx xx) -> [(name, value, unit, group)]
SF = {
    (0x79A, 0x1304): _vcm("Power switch", "", lambda d: "On" if d[0] & 128 else "Off", "Vehicle"),
    (0x79A, 0x1156): _vcm("Gear", "", lambda d: GEAR.get(d[0], f"? ({d[0]})"), "Drive"),
    (0x79A, 0x1103): _vcm("12 V battery (VCM)", "V", lambda d: round(d[0] * 0.08, 2), "Vehicle"),
    (0x79A, 0x1183): _vcm("12 V battery current", "A", lambda d: round(_s16(d[0:2]) / 256, 2), "Vehicle"),
    (0x79A, 0x1203): _vcm("Quick charges", "", lambda d: _u(d[0:2]), "Charging"),
    (0x79A, 0x1205): _vcm("L1/L2 charges", "", lambda d: _u(d[0:2]), "Charging"),
    (0x79A, 0x115D): _vcm("Ambient temperature", "°C", lambda d: round(((d[0] * 0.9 - 40.9) - 32) * 5 / 9, 1), "Climate"),
    (0x79A, 0x1261): _vcm("A/C power estimate", "W", lambda d: d[0] * 50, "Climate"),
    (0x79A, 0x1262): _vcm("Heater power estimate", "W", lambda d: d[0] * 250, "Climate"),
    (0x79A, 0x1152): _vcm("Auxiliary power", "W", lambda d: d[0] * 100, "Vehicle"),
    (0x79A, 0x1151): _vcm("A/C system power", "W", lambda d: d[0] * 250, "Climate"),
    (0x79A, 0x1234): _vcm("Charge port", "", lambda d: PLUG.get(d[0], f"? ({d[0]})"), "Charging"),
    (0x79A, 0x114E): _vcm("Charge mode", "", lambda d: CHARGE.get(d[0], f"? ({d[0]})"), "Charging"),
    (0x79A, 0x1255): _vcm("Motor speed", "rpm", lambda d: _s16(d[0:2]), "Drive"),
    (0x79A, 0x1236): _vcm("On-board charger output", "W", lambda d: _u(d[0:2]) * 100, "Charging"),
    (0x79A, 0x1146): _vcm("Motor power", "W", lambda d: _u(d[0:2]) * 40, "Drive"),
    (0x79A, 0x121A): _vcm("Speed", "km/h", lambda d: _u(d[0:2]) / 10, "Drive"),
    (0x79A, 0x1106): _vcm("A/C", "", lambda d: "On" if d[0] else "Off", "Climate"),
    (0x79A, 0x110F): _vcm("Rear heater", "", lambda d: "On" if d[0] == 0xA2 else "Off", "Climate"),
    (0x79A, 0x1318): _vcm("ECO mode", "", lambda d: "On" if d[0] in (0x10, 0x11) else "Off", "Drive"),
    (0x79A, 0x131A): _vcm("e-Pedal", "", lambda d: "On" if d[0] == 0x04 else "Off", "Drive"),
    (0x763, 0x0E01): _vcm("Odometer", "km", lambda d: _u(d[0:3]), "Vehicle"),
    (0x763, 0x0E25): _vcm("Tyre pressure front right", "kPa", lambda d: round(d[0] * 0.068947576 * 100 / 4), "Chassis"),
    (0x763, 0x0E26): _vcm("Tyre pressure front left", "kPa", lambda d: round(d[0] * 0.068947576 * 100 / 4), "Chassis"),
    (0x763, 0x0E27): _vcm("Tyre pressure rear right", "kPa", lambda d: round(d[0] * 0.068947576 * 100 / 4), "Chassis"),
    (0x763, 0x0E28): _vcm("Tyre pressure rear left", "kPa", lambda d: round(d[0] * 0.068947576 * 100 / 4), "Chassis"),
    (0x760, 0x1208): _vcm("Steering wheel angle", "°", lambda d: _s16(d[0:2]) / 10, "Chassis"),
    (0x760, 0x1209): _vcm("Brake pedal", "", lambda d: _u(d[0:2]), "Chassis"),
    (0x760, 0x1115): _vcm("Accelerator pedal", "", lambda d: d[0], "Chassis"),
    (0x78C, 0x1225): _vcm("Motor torque", "Nm", lambda d: round(_s16(d[0:2]) / 64, 2), "Drive"),
    (0x78C, 0x1121): _vcm("Motor temperature", "°C", lambda d: d[0] - 40, "Drive"),
}


def _group1(p):
    if len(p) != 0x35:               # layout documented for the 2018 ZE1 only
        return []
    soc = ((p[33] << 16) | (p[34] << 8) | p[35]) / 10000
    ahr = ((p[37] << 16) | (p[38] << 8) | p[39]) / 10000
    return [
        ("State of charge", round(soc, 2), "%", "Battery"),
        ("Pack capacity", round(ahr, 1), "Ah", "Battery"),
        ("Energy in pack", round(4.425 * soc * 0.01 * ahr * 0.08, 1), "kWh", "Battery"),   # GIDs x 80 Wh
        ("Pack voltage", round(((p[20] << 8) | p[21]) / 100, 2), "V", "Battery"),
        ("Pack current (1)", round(_s32(p[2:6]) / 1024, 2), "A", "Battery"),
        ("Pack current (2)", round(_s32(p[8:12]) / 1024, 2), "A", "Battery"),
        ("12 V battery", round(((p[22] << 8) | p[23]) / 1000, 2), "V", "Vehicle"),          # Battery-Emulator offset
        ("Insulation resistance", (p[24] << 8) | p[25], "kOhm", "Battery"),
    ]


def _group2(p):
    cells = [_u(p[2 + 2 * i:4 + 2 * i]) for i in range(96) if 4 + 2 * i <= len(p)]
    cells = [c for c in cells if 0 < c < 6000]
    if len(cells) < 90:
        return []
    return [("Cell voltage min", min(cells), "mV", "Battery"), ("Cell voltage max", max(cells), "mV", "Battery"),
            ("Cell voltage average", round(sum(cells) / len(cells)), "mV", "Battery"),
            ("Cell voltage spread", max(cells) - min(cells), "mV", "Battery")]


def cell_voltages(p):
    return [_u(p[2 + 2 * i:4 + 2 * i]) for i in range(96) if 4 + 2 * i <= len(p)]


def _group4(p):
    out = []
    for n, (a, b) in enumerate(((2, 3), (5, 6), (8, 9), (11, 12)), 1):
        if b >= len(p):
            continue
        raw = (p[a] << 8) | p[b]
        if raw >= 1000:               # 1021 / 0xFFFF = no sensor
            continue
        out.append((f"Pack temperature {n}", round((_raw_to_f(raw) - 32) * 5 / 9, 1), "°C", "Battery"))
    return out


def _group6(p):
    if len(p) < 14:
        return []
    bits = [0] * 24
    for i in range(12):
        bits[i * 2] = p[2 + i] & 0xF
        bits[i * 2 + 1] = p[2 + i] >> 4
    active = [i + 1 for i in range(96) if int("8421"[i % 4]) & bits[i >> 2]]
    return [("Shunt bits set (balancing)", len(active), "", "Battery")]


def _group61(p):                       # Hx / SOH / capacity: offsets from Battery-Emulator (ZE1)
    if len(p) < 18:
        return []
    out = []
    hx, soh, sohr, cap = _u(p[2:4]), _u(p[4:6]), _u(p[7:9]), _u(p[14:18])
    if 0 < hx <= 20000: out.append(("Hx", round(hx / 100, 2), "%", "Battery"))
    if 0 < soh <= 10000: out.append(("State of health", round(soh / 100, 2), "%", "Battery"))
    if 0 < sohr <= 10000: out.append(("State of health (raw)", round(sohr / 100, 2), "%", "Battery"))
    if 10000 < cap <= 4000000: out.append(("Pack capacity (health block)", round(cap / 10000, 1), "Ah", "Battery"))
    return out


def _group84(p):
    txt = "".join(chr(c) for c in p[2:] if 32 <= c < 127).strip()
    if not txt.strip("*"):                # this car answers with a masked serial (all asterisks)
        return []
    return [("Battery serial", txt, "", "Battery")]


def _meter(p):
    if len(p) < 5:
        return []
    w, l1, l2 = p[2], p[3], p[4]
    stalk = w in (0x22, 0x09)
    out = [("Indicator left", "On" if stalk and l1 == 0x06 else "Off", "", "Body"),
           ("Indicator right", "On" if stalk and l1 == 0x0A else "Off", "", "Body"),
           ("Front washer", "On" if stalk and l1 == 0x01 else "Off", "", "Body"),
           ("Rear washer", "On" if stalk and l1 == 0x31 else "Off", "", "Body"),
           ("Rear wiper", {0x41: "Intermittent", 0x91: "Continuous"}.get(l1, "Off"), "", "Body")]
    if w in WIPER_LEVEL:
        out.append(("Front wiper", WIPER_LEVEL[w], "", "Body"))
        out.append(("Rain sensitivity", WIPER_RAIN[w], "", "Body"))
    return out


def _hvac(p):
    if len(p) < 14:
        return []
    fan = p[13] - 131
    return [("Fan speed", fan, "", "Climate")] if 0 <= fan <= 8 else []


LONG = {  # (module reply id, service, id) -> decoder(payload)
    (0x7BB, 0x61, 0x01): _group1, (0x7BB, 0x61, 0x02): _group2, (0x7BB, 0x61, 0x04): _group4,
    (0x7BB, 0x61, 0x06): _group6, (0x7BB, 0x61, 0x61): _group61, (0x7BB, 0x61, 0x84): _group84,
    (0x765, 0x61, 0x09): _meter, (0x764, 0x61, 0x10): _hvac,
}


def polled(state, vin=None):
    """[{group, name, value, unit, age}] from the last complete replies in `state` (a Polled)."""
    out = []
    for (cid, svc, ident), (p, t) in state.msgs.items():
        try:
            if svc == 0x62:
                fn = SF.get((cid, ident))
                items = fn(p[3:]) if fn else []
            elif cid == 0x79A and ident == 0x81:
                items = [("VIN", p[2:19].decode("ascii", "replace"), "", "Vehicle")] if len(p) >= 19 else []
            else:
                fn = LONG.get((cid, svc, ident))
                items = fn(p) if fn else []
        except (IndexError, ValueError):
            items = []
        for name, value, unit, grp in items:
            out.append({"group": grp, "name": name, "value": value, "unit": unit, "t": t})
    return out
