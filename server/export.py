"""Export of one recorded session for analysis outside the dashboard.

  log_stream(): every frame of the session as gzip-compressed text, one frame per line
  summary():    a short Markdown report of the same session (what is on the bus, what changed,
                what the decoded signals and the polled battery values did)

Both read through their own read-only connection, so a large export never blocks uploads.
"""
import datetime, sqlite3, zlib

import dbc as dbc_mod
import decode

CAP_PAYLOADS = 20000   # distinct payloads remembered per id for the per-byte statistics
MAX_ROWS = 40          # longest signal/ID tables printed before the rest is summarised


def _open(path):
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    return c


def _chname(channels, board, ch):
    return channels.get(str(board), {}).get("AB"[ch], {}).get("name") or f"B{board}{'AB'[ch]}"


def _iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _head(c, cid):
    row = c.execute("SELECT vin, start_t, end_t FROM connections WHERE id=?", (cid,)).fetchone()
    return row


def _frame_id(i, flags):
    s = f"{i:X}"
    return s + ("x" if flags & 1 else "") + ("r" if flags & 2 else "")


def log_stream(path, cid, channels):
    """Yield gzip chunks of the session log, or None if there is no such session."""
    c = _open(path)
    head = _head(c, cid)
    if head is None:
        return None
    vin, start, end = head

    def gen():
        z = zlib.compressobj(6, zlib.DEFLATED, 31)
        buf = [
            "# CAN logger session log\n",
            f"# session {cid}  VIN {vin or 'unknown'}\n",
            f"# start {_iso(start)}  epoch {start:.6f}  duration {end - start:.1f} s\n",
            "# columns: seconds_since_first_frame  channel  can_id_hex  dlc  data_hex  [TX]\n",
            "# can_id_hex has a suffix x for a 29-bit id and r for a remote frame; TX = sent by the logger, not heard on the bus\n",
            "# data is the payload byte for byte (frames longer than 8 bytes are cut to 8 by the logger)\n",
        ]
        t0 = None
        n = 0
        for t, board, ch, i, flags, dlc, data in c.execute(
                "SELECT t, board, ch, id, flags, dlc, data FROM frames WHERE conn=? ORDER BY t, rowid", (cid,)):
            if t0 is None:
                t0 = t
                buf.append(f"# first frame epoch {t0:.6f}\n")
            buf.append(f"{t - t0:.6f} {_chname(channels, board, ch)} {_frame_id(i, flags)} {dlc} "
                       f"{bytes(data).hex()}{' TX' if flags & 4 else ''}\n")
            n += 1
            if len(buf) >= 4000:
                yield z.compress("".join(buf).encode())
                buf = []
        buf.append(f"# end: {n} frames\n")
        yield z.compress("".join(buf).encode()) + z.flush()
        c.close()
    return gen()


def _table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def summary(path, cid, channels):
    c = _open(path)
    head = _head(c, cid)
    if head is None:
        return None
    vin, start, end = head
    files, label = decode.dbc_set(vin)
    dur = max(end - start, 1e-9)
    step = max(0.5, dur / 400)                # decode one frame per id per `step` seconds

    # ── one pass over all frames ────────────────────────────────────────────────
    ids = {}       # (chname, id, ext) -> stats
    sigs = {}      # (chname, message, signal) -> [min, max, first, last, n, unit]
    chan = {}      # chname -> [frames, first_t, last_t]
    first_t = last_t = None
    total = 0
    msgs_cache = {}
    for t, board, ch, i, flags, dlc, data in c.execute(
            "SELECT t, board, ch, id, flags, dlc, data FROM frames WHERE conn=? ORDER BY t, rowid", (cid,)):
        total += 1
        first_t = t if first_t is None else first_t
        last_t = t
        name = _chname(channels, board, ch)
        a = chan.setdefault(name, [0, t, t])
        a[0] += 1; a[2] = t
        k = (name, i, flags & 5)                       # extended id and TX are separate series
        s = ids.get(k)
        if s is None:
            s = ids[k] = {"n": 0, "first": t, "last": t, "dlc": set(), "pay": set(), "over": False,
                          "next": t}
        s["n"] += 1; s["last"] = t; s["dlc"].add(dlc)
        data = bytes(data)
        if len(s["pay"]) < CAP_PAYLOADS:
            s["pay"].add(data)
        else:
            s["over"] = True
        if t >= s["next"] and not flags & 5:
            s["next"] = t + step
            if name not in msgs_cache:
                f = files.get(name)
                msgs_cache[name] = decode._load(f) if f else None
            m = msgs_cache[name]
            msg = m.get(i) if m else None
            if msg and dlc == msg["dlc"]:
                s["msg"] = msg["name"]
                for sn, val, unit, text in dbc_mod.decode(msg, data):
                    if sn.startswith("Unknown_Padding") or not isinstance(val, (int, float)):
                        continue
                    e = sigs.get((name, msg["name"], sn))
                    if e is None:
                        sigs[(name, msg["name"], sn)] = [val, val, val, val, 1, unit]
                    else:
                        e[0] = min(e[0], val); e[1] = max(e[1], val); e[3] = val; e[4] += 1

    # ── polled battery values: replay the replies once, snapshot every few seconds ──
    pstep = max(5.0, dur / 40)
    ps = decode.Polled()
    marks = ",".join(str(i) for i in decode.REPLY_IDS)
    snaps = []
    nxt = None
    for rid, i, x, t in c.execute(
            f"""SELECT rowid, id, data, t FROM frames WHERE conn=? AND board=1 AND ch=1 AND flags&4=0
                AND id IN ({marks}) ORDER BY t, rowid""", (cid,)):
        ps.feed([(rid, i, x, t)])
        nxt = t if nxt is None else nxt
        if t >= nxt:
            nxt = t + pstep
            snaps.append((t, {(v["group"], v["name"]): (v["value"], v["unit"]) for v in decode.polled(ps, vin)}))
    final = {(v["group"], v["name"]): (v["value"], v["unit"]) for v in decode.polled(ps, vin)}
    snaps = [(t, d) for t, d in snaps if d]
    if snaps:
        snaps.append((last_t, final))

    out = []
    w = out.append
    w(f"# CAN logger session {cid}\n")
    w(f"- VIN: {vin or 'unknown'}")
    w(f"- Start: {_iso(start)}   End: {_iso(end)}   Duration: {end - start:.0f} s")
    w(f"- Frames: {total:,}   Signal definitions used: {label} (EV-CAN {files['EV-CAN']}, CAR-CAN {files['CAR-CAN']}, AV-CAN {files['AV-CAN']})")
    w("- Full log of every frame: `/api/export?conn=%d&format=log` (gzip text, columns in its header). "
      "Times below are seconds from the first frame unless a date is shown.\n" % cid)

    w("## Channels\n")
    w(_table(["Channel", "Frames", "Per second", "First", "Last"],
             [(n, f"{a[0]:,}", f"{a[0] / max(a[2] - a[1], 1e-9):.0f}", f"+{a[1] - first_t:.1f} s", f"+{a[2] - first_t:.1f} s")
              for n, a in sorted(chan.items())]) + "\n")

    b = c.execute("""SELECT board, boot_id, MIN(dropped), MAX(dropped), MIN(rssi), AVG(rssi), COUNT(*)
                     FROM batches WHERE rx>=? AND rx<=? GROUP BY board, boot_id""", (start - 1, end + 5)).fetchall()
    if b:
        w("## Logger boards\n")
        w(_table(["Board", "Boot", "Frames lost in the board's buffer (counter at end)", "WiFi dBm min / avg", "Uploads"],
                 [(r[0], r[1], f"{r[3]:,}" + (f" (+{r[3] - r[2]:,} during the session)" if r[3] != r[2] else ""),
                   f"{r[4]} / {r[5]:.0f}" if r[4] is not None else "-", r[6]) for r in b]) + "\n")

    ev = c.execute("SELECT t, board, kind, value, note FROM events WHERE conn=? ORDER BY t", (cid,)).fetchall()
    if ev:
        w("## Events\n")
        w(_table(["Time", "Board", "Event", "Value", "Note"],
                 [(f"+{t - first_t:.1f} s", bd, k, v or "", n or "") for t, bd, k, v, n in ev[:MAX_ROWS * 2]]) + "\n")

    w("## Every id seen\n")
    w("Bytes that change are listed as `byte(number of distinct values)`; a byte with 16 or 256 values is "
      "usually a counter or checksum, a byte with 2 to 4 values a flag or state.\n")
    rows = []
    for (name, i, fl), s in sorted(ids.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        vals = [set() for _ in range(8)]
        for p in s["pay"]:
            for k, byte in enumerate(p):
                vals[k].add(byte)
        chg = " ".join(f"{k}({len(v)})" for k, v in enumerate(vals) if len(v) > 1) or "none"
        d = len(s["pay"])
        rows.append((name, _frame_id(i, fl) + (" TX" if fl & 4 else ""), s.get("msg", ""),
                     f"{s['n']:,}", f"{s['n'] / max(last_t - first_t, 1e-9):.1f}",
                     "/".join(str(x) for x in sorted(s["dlc"])), f"{d}{'+' if s['over'] else ''}", chg))
    w(_table(["Channel", "Id", "DBC message", "Frames", "Per second", "DLC", "Distinct payloads", "Changing bytes"], rows) + "\n")

    moving = sorted(((k, e) for k, e in sigs.items() if e[0] != e[1]), key=lambda kv: kv[0])
    still = len(sigs) - len(moving)
    w("## Decoded signals that changed\n")
    w(f"Sampled about every {step:.1f} s per id. {len(moving)} signals changed, {still} stayed constant "
      "(not listed). Names come from the DBC files and are not all verified.\n")
    if moving:
        w(_table(["Channel", "Message", "Signal", "Min", "Max", "First", "Last", "Unit"],
                 [(k[0], k[1], k[2], f"{e[0]:.6g}", f"{e[1]:.6g}", f"{e[2]:.6g}", f"{e[3]:.6g}", e[5])
                  for k, e in moving]) + "\n")

    w("## Polled battery values (from the logger's own requests)\n")
    if not snaps:
        w("No polled replies in this session.\n")
    else:
        names = sorted({k for _, d in snaps for k in d})
        stat = []
        moving_n = []
        for k in names:
            v = [d[k][0] for _, d in snaps if k in d and isinstance(d[k][0], (int, float))]
            u = next((d[k][1] for _, d in snaps if k in d), "")
            if not v:
                stat.append((k[0], k[1], str(next((d[k][0] for _, d in snaps if k in d), "")), "", "", ""))
                continue
            stat.append((k[0], k[1], f"{v[0]:.6g}", f"{min(v):.6g}", f"{max(v):.6g}", f"{v[-1]:.6g} {u}"))
            if min(v) != max(v):
                moving_n.append(k)
        w(_table(["Group", "Value", "First", "Min", "Max", "Last"], stat) + "\n")
        if moving_n:
            # cell extremes and temperatures first, then the rest
            pri = lambda k: next((n for n, w in enumerate(("Cell voltage spread", "Cell voltage min", "Cell voltage max",
                                                            "Pack temperature", "Shunt")) if w in k[1]), 9)
            cols = sorted(moving_n, key=lambda k: (pri(k), k[1]))[:10]
            w(f"Timeline of the {len(cols)} values that changed:\n")
            w(_table(["Time"] + [k[1] for k in cols],
                     [[f"+{t - first_t:.0f} s"] + [f"{d[k][0]:.6g}" if k in d else "" for k in cols] for t, d in snaps]) + "\n")
    c.close()
    return "\n".join(out)
