"""Minimal DBC reader (stdlib only): messages, signals, multiplexing, value tables.

Only what the Leaf databases use: standard 11-bit IDs, integer signals, Intel and
Motorola byte order, plain "M" / "mN" multiplexing.
"""
import re

BO_RE = re.compile(r"^BO_\s+(\d+)\s+(\S+)\s*:\s*(\d+)")
SG_RE = re.compile(r'^\s*SG_\s+(\S+)\s+(?:(M|m\d+M?)\s+)?:\s*(\d+)\|(\d+)@([01])([+-])\s*'
                   r'\(([^,]+),([^)]+)\)\s*\[[^\]]*\]\s*"([^"]*)"')
VAL_RE = re.compile(r"^VAL_\s+(\d+)\s+(\S+)\s+(.*);\s*$")
PAIR_RE = re.compile(r'(-?\d+)\s+"([^"]*)"')


def load(path):
    """Returns {can_id: {"name", "dlc", "signals": [...]}}."""
    msgs, cur = {}, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = BO_RE.match(line)
            if m:
                cid = int(m.group(1))
                cur = None
                if cid < 0x800:                       # skip 29-bit and the "independent signals" pseudo message
                    cur = msgs.setdefault(cid, {"name": m.group(2), "dlc": int(m.group(3)), "signals": []})
                continue
            m = SG_RE.match(line)
            if m and cur is not None:
                name, mux, start, ln, order, sign, fac, off, unit = m.groups()
                cur["signals"].append({
                    "name": name, "mux": mux, "start": int(start), "len": int(ln),
                    "little": order == "1", "signed": sign == "-",
                    "factor": float(fac), "offset": float(off), "unit": unit, "values": {}})
                continue
            m = VAL_RE.match(line)
            if m:
                cid = int(m.group(1))
                if cid in msgs:
                    for s in msgs[cid]["signals"]:
                        if s["name"] == m.group(2):
                            s["values"] = {int(k): v for k, v in PAIR_RE.findall(m.group(3))}
    return msgs


def _raw(data, s):
    n = int.from_bytes(data, "little" if s["little"] else "big")
    total = len(data) * 8
    if s["little"]:
        if s["start"] + s["len"] > total:
            return None
        v = (n >> s["start"]) & ((1 << s["len"]) - 1)
    else:                                              # Motorola: start bit is the MSB, walk down
        v, p = 0, s["start"]
        for _ in range(s["len"]):
            byte, bit = p // 8, p % 8
            if byte >= len(data):
                return None
            v = (v << 1) | ((data[byte] >> bit) & 1)
            p = (byte + 1) * 8 + 7 if bit == 0 else p - 1
    if s["signed"] and v >= 1 << (s["len"] - 1):
        v -= 1 << s["len"]
    return v


def decode(msg, data):
    """[(signal, value, unit, text or None)] for one frame. Multiplexed signals only when selected."""
    muxval = None
    for s in msg["signals"]:
        if s["mux"] == "M":
            muxval = _raw(data, s)
    out = []
    for s in msg["signals"]:
        if s["mux"] and s["mux"] != "M":
            if muxval is None or int(s["mux"][1:].rstrip("M")) != muxval:
                continue
        r = _raw(data, s)
        if r is None:
            continue
        v = r * s["factor"] + s["offset"]
        out.append((s["name"], round(v, 4) if v != int(v) else int(v), s["unit"], s["values"].get(r)))
    return out
