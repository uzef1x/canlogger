"""Every decodable value of one session over time, for the chart page (/grafana).

series() covers a time range with at most `points` buckets. Passive signals (the DBC files) take
the first frame of each id in each bucket, in SQL, so a long range stays cheap; polled values
(ZE1 active mode, including the 96 cell voltages) are replayed from the session start and
sampled once per bucket. A bucket without a sample is null.
"""
import math
import sqlite3

import dbc as dbc_mod
import decode


def _open(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def _chname(channels, board, ch):
    return channels.get(str(board), {}).get("AB"[ch], {}).get("name") or f"B{board}{'AB'[ch]}"


def series(path, cid, channels, t0=None, t1=None, points=600):
    c = _open(path)
    row = c.execute("SELECT vin, start_t, end_t FROM connections WHERE id=?", (cid,)).fetchone()
    if row is None:
        return None
    vin, start, end = row
    t0 = start if t0 is None else t0
    t1 = end if t1 is None else t1
    t1 = max(t1, t0 + 1.0)
    points = max(10, min(int(points), 3000))
    step = max((t1 - t0) / points, 0.01)
    nb = int(math.ceil((t1 - t0) / step))
    files, label = decode.dbc_set(vin)

    sigs = {}      # key -> {"group", "name", "unit", "v": [...], "states": {}}

    def put(key, group, name, unit, b, val, text=None):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return
        s = sigs.get(key)
        if s is None:
            s = sigs[key] = {"key": key, "group": group, "name": name, "unit": unit,
                             "v": [None] * nb, "states": {}}
        s["v"][b] = val
        if text is not None:
            s["states"][str(val)] = text

    # ── passive signals: first frame of each id in each bucket ──────────────────
    msgs_cache = {}
    for board, ch, i, dlc, data, t in c.execute(
            """SELECT board, ch, id, dlc, data, MIN(t) FROM frames
               WHERE conn=? AND t>=? AND t<? AND flags&5=0
               GROUP BY board, ch, id, CAST((t-?)/? AS INTEGER)""", (cid, t0, t1, t0, step)):
        name = _chname(channels, board, ch)
        if name not in msgs_cache:
            f = files.get(name)
            msgs_cache[name] = decode._load(f) if f else None
        m = msgs_cache[name]
        msg = m.get(i) if m else None
        if not msg or dlc != msg["dlc"]:
            continue
        b = min(int((t - t0) / step), nb - 1)
        for sn, val, unit, text in dbc_mod.decode(msg, bytes(data)):
            if sn.startswith("Unknown_Padding"):
                continue
            put((name, msg["name"], sn), name, f"{msg['name']} · {sn}", unit, b, val, text)

    # ── polled values: replay from the session start, one snapshot per bucket ──
    ps = decode.Polled()
    marks = ",".join(str(i) for i in decode.REPLY_IDS)
    last_b = -1
    for rid, i, x, t in c.execute(
            f"""SELECT rowid, id, data, t FROM frames WHERE conn=? AND board=1 AND ch=1 AND flags&4=0
                AND id IN ({marks}) AND t<? ORDER BY t, rowid""", (cid, t1)):
        ps.feed([(rid, i, x, t)])
        if t < t0:
            continue
        b = min(int((t - t0) / step), nb - 1)
        if b == last_b:
            continue
        last_b = b
        for v in decode.polled(ps, vin):
            if v["group"] == "Vehicle" and v["name"] == "VIN":
                continue
            put(("Polled", v["group"], v["name"]), "Polled · " + v["group"], v["name"], v["unit"], b, v["value"])
        cells = ps.msgs.get((0x7BB, 0x61, 0x02))
        if cells:
            for n, mv in enumerate(decode.cell_voltages(cells[0]), 1):
                if 0 < mv < 6000:
                    put(("Polled", "Cell", n), "Polled · Cell voltages", f"Cell {n:02d}", "mV", b, mv)
    c.close()

    out = sorted(sigs.values(), key=lambda s: (s["group"], s["name"]))
    for s in out:
        s["key"] = "/".join(str(k) for k in s["key"])
    return {"vin": vin, "dbc": label, "start": start, "end": end, "t0": t0, "t1": t1, "step": step,
            "t": [round(t0 + k * step, 3) for k in range(nb)], "signals": out}
