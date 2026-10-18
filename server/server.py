#!/usr/bin/env python3
"""CAN logger ingest server (stdlib only).

Every time the boards are powered up in a car, the server starts a *connection*. The
board that can read the VIN does so by itself at power-on and reports it; the server
attaches the VIN to the connection, so every connection belongs to a specific car and
a car's connections accumulate over time. The live dashboard shows only the car that is
connected right now, and nothing when no board is uploading.

POST /ingest      binary batch from a logger board (see src/log_format.h)
POST /hello       board announces its WiFi network (and the VIN, once it knows it)
POST /vin         board reports the result of its automatic VIN read
GET  /            live dashboard (server/dashboard.html, polls /api/status)
GET  /api/status  the connected car: boards, channels, live IDs, recent frames
GET  /api/cars    every car seen (by VIN) with its connections
GET  /stats, /frames   raw access
Run:  python3 server.py [--port 8000] [--db canlog.db]
"""
import argparse, json, os, re, sqlite3, struct, threading, time
import decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HDR = struct.Struct("<IBBHIQI")       # v1: magic, board, ver, count, boot_id, t_send_us, dropped
HDR2 = struct.Struct("<IBBHIQIb3x")   # v2: v1 + rssi (dBm) + 3 pad
REC = struct.Struct("<QIBBBB8s")      # ts_us, id, ch, flags, dlc, reserved, data
MAGIC = 0x31474C43
HERE = os.path.dirname(os.path.abspath(__file__))
CHANNELS = json.load(open(os.path.join(HERE, "channels.json")))
VIN_RE = re.compile(r"^[0-9A-Z]{17}$")

WINDOW = 10.0      # seconds used for rates and the live-ID table
ACTIVE_S = 15.0    # no upload for this long = the car is disconnected (dashboard empties)

db = None
db_lock = threading.Lock()   # one shared sqlite connection: serialise every use
polled_states = {}      # connection id -> decode.Polled (incremental reassembly of polled replies)
fast = {"on": False}      # dashboard switch: poll the cell voltages every cycle (~5 s) instead of every 3rd (~15 s)
conn = {"id": None, "last": 0.0, "boots": set(), "boards": {}}   # the current/most recent connection


def init_db(path):
    c = sqlite3.connect(path, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS frames(
        t REAL NOT NULL,          -- wall-clock seconds (server-estimated)
        board INTEGER NOT NULL, boot_id INTEGER NOT NULL, ch INTEGER NOT NULL,
        id INTEGER NOT NULL, flags INTEGER NOT NULL, dlc INTEGER NOT NULL, data BLOB NOT NULL)""")
    if "conn" not in [r[1] for r in c.execute("PRAGMA table_info(frames)")]:
        c.execute("ALTER TABLE frames ADD COLUMN conn INTEGER")   # NULL = logged before cars existed
    c.execute("CREATE INDEX IF NOT EXISTS ix_t ON frames(t)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_bc ON frames(board, ch, t)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_conn ON frames(conn, board, ch)")
    c.execute("""CREATE TABLE IF NOT EXISTS batches(
        rx REAL, board INTEGER, boot_id INTEGER, count INTEGER, dropped INTEGER)""")
    if "rssi" not in [r[1] for r in c.execute("PRAGMA table_info(batches)")]:
        c.execute("ALTER TABLE batches ADD COLUMN rssi INTEGER")
    # One row per power-up session in a car. vin stays NULL if it could not be read.
    c.execute("""CREATE TABLE IF NOT EXISTS connections(
        id INTEGER PRIMARY KEY, start_t REAL NOT NULL, end_t REAL NOT NULL, vin TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_conn_vin ON connections(vin)")
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        t REAL NOT NULL, board INTEGER, kind TEXT NOT NULL, value TEXT, note TEXT, conn INTEGER)""")
    if "conn" not in [r[1] for r in c.execute("PRAGMA table_info(events)")]:
        c.execute("ALTER TABLE events ADD COLUMN conn INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS devices(
        board INTEGER PRIMARY KEY, ssid TEXT, ip TEXT, bssid TEXT, hello_t REAL, peer TEXT)""")
    c.execute("DROP VIEW IF EXISTS v_frames")
    c.execute("""CREATE VIEW v_frames AS
        SELECT f.*, c.vin, c.start_t AS conn_start
        FROM frames f LEFT JOIN connections c ON c.id = f.conn""")
    c.commit()
    return c


def PAGE_VERSION():
    """Changes whenever dashboard.html changes, so an open page can reload itself."""
    return os.path.getmtime(os.path.join(HERE, "dashboard.html"))


def touch_conn(now, board=None, boot_id=None):
    """Return the id of the connection this activity belongs to (db_lock held).

    A connection is one power-up of the boards in a car. A new one starts when
      * a board that is already part of the current connection comes back with a new boot id
        (it was power-cycled: a new ignition cycle, however short), or
      * the connection went quiet (ACTIVE_S, the dashboard has already emptied) and activity
        resumes from a board session we have NOT seen in it.
    Activity from a board session that was already in the last connection is the same car,
    just a WiFi outage. Boards coming up a few seconds apart share one connection.
    """
    stale = conn["id"] is None or now - conn["last"] > ACTIVE_S
    known = boot_id is not None and boot_id in conn["boots"]
    rebooted = (board is not None and boot_id is not None
                and conn["boards"].get(board, boot_id) != boot_id)
    if conn["id"] is None or rebooted or (stale and not known):
        cur = db.execute("INSERT INTO connections(start_t, end_t) VALUES(?,?)", (now, now))
        conn["id"], conn["boots"], conn["boards"] = cur.lastrowid, set(), {}
    if boot_id is not None:
        conn["boots"].add(boot_id)
        if board is not None:
            conn["boards"][board] = boot_id
    conn["last"] = now
    db.execute("UPDATE connections SET end_t=? WHERE id=?", (now, conn["id"]))
    return conn["id"]


def set_vin(cid, vin):
    db.execute("UPDATE connections SET vin=? WHERE id=? AND (vin IS NULL OR vin<>?)", (vin, cid, vin))


def status():
    with db_lock:
        return _status()


def _status():
    now = time.time()
    cid = conn["id"]
    if cid is None or now - conn["last"] > ACTIVE_S:
        return {"active": False, "page": PAGE_VERSION(), "now": now}
    since = now - WINDOW
    start_t, vin = db.execute("SELECT start_t, vin FROM connections WHERE id=?", (cid,)).fetchone()
    fail = db.execute("SELECT note FROM events WHERE conn=? AND kind='vin_fail' ORDER BY t DESC LIMIT 1",
                      (cid,)).fetchone()
    car = {"vin": vin, "connected_age": now - start_t,
           "state": "identified" if vin else ("failed" if fail and now - start_t > 30 else "identifying"),
           "note": fail[0] if fail else None}
    boards = {}
    for b, rx, boot, dropped, rssi in db.execute(
            """SELECT board, MAX(rx), boot_id, dropped, rssi FROM batches WHERE rx>=? GROUP BY board""",
            (start_t - 1,)):
        boards[b] = {"board": b, "last_rx_age": now - rx, "boot_id": boot, "dropped": dropped,
                     "rssi": rssi, "ssid": None}
    for b, ssid, hello_t in db.execute("SELECT board, ssid, hello_t FROM devices"):
        if b in boards:
            boards[b]["ssid"] = ssid or None
        elif now - hello_t <= 90:      # hello every 60 s: connected, but nothing to upload (no CAN traffic)
            boards[b] = {"board": b, "last_rx_age": None, "boot_id": None, "dropped": 0,
                         "rssi": None, "ssid": ssid or None}
    chans = {}
    for b, brd in CHANNELS.items():
        if int(b) not in boards:
            continue
        for ci, letter in enumerate("AB"):
            info = brd.get(letter, {})
            chans[(int(b), ci)] = {"board": int(b), "ch": letter, "name": info.get("name"),
                                   "fd": info.get("fd"), "total": 0, "rate": 0.0, "last_age": None}
    for b, c, n, last in db.execute(
            "SELECT board, ch, COUNT(*), MAX(t) FROM frames WHERE conn=? GROUP BY board, ch", (cid,)):
        d = chans.setdefault((b, c), {"board": b, "ch": "AB"[c], "name": None, "fd": None})
        d.update(total=n, last_age=now - last)
    for b, c, n in db.execute(
            "SELECT board, ch, COUNT(*) FROM frames WHERE conn=? AND t>? GROUP BY board, ch", (cid, since)):
        if (b, c) in chans:
            chans[(b, c)]["rate"] = n / WINDOW
    ids = [{"board": b, "ch": "AB"[c], "id": f"0x{i:03X}", "rate": n / WINDOW,
            "dlc": d, "data": bytes(x).hex(" ")}
           for b, c, i, n, d, x in db.execute(
               """SELECT board, ch, id, COUNT(*), dlc, data FROM frames WHERE conn=? AND t>?
                  GROUP BY board, ch, id ORDER BY board, ch, id LIMIT 300""", (cid, since))]
    recent = [{"age": now - t, "board": b, "ch": "AB"[c], "id": f"0x{i:03X}",
               "dlc": d, "data": bytes(x).hex(" ")}
              for t, b, c, i, d, x in db.execute(
                  "SELECT t, board, ch, id, dlc, data FROM frames WHERE conn=? ORDER BY t DESC LIMIT 40",
                  (cid,))]
    return {"active": True, "page": PAGE_VERSION(), "car": car, "now": now, "window": WINDOW,
            "boards": sorted(boards.values(), key=lambda x: x["board"]),
            "channels": [chans[k] for k in sorted(chans)], "ids": ids, "recent": recent}


def decoded(cid=None):
    """Signals from the passive frames of the last WINDOW s and values from polled replies."""
    with db_lock:
        now = time.time()
        if cid is None:
            if conn["id"] is None or now - conn["last"] > ACTIVE_S:
                return {"active": False}
            cid = conn["id"]
        row = db.execute("SELECT vin FROM connections WHERE id=?", (cid,)).fetchone()
        vin = row[0] if row else None
        latest = db.execute(
            """SELECT board, ch, id, dlc, data, MAX(t) FROM frames
               WHERE conn=? AND t>? AND flags&4=0 AND flags&1=0 GROUP BY board, ch, id""",
            (cid, now - WINDOW)).fetchall()
        ps = polled_states.setdefault(cid, decode.Polled())
        marks = ",".join(str(i) for i in decode.REPLY_IDS)
        ps.feed([(r, i, x, t) for r, i, x, t in db.execute(
            f"""SELECT rowid, id, data, t FROM frames WHERE conn=? AND board=1 AND ch=1 AND flags&4=0
                AND id IN ({marks}) AND rowid>? ORDER BY rowid""", (cid, ps.last_rowid))])
    frames = []
    for b, c, i, d, x, _ in latest:
        name = CHANNELS.get(str(b), {}).get("AB"[c], {}).get("name")
        if name:
            frames.append((b, name, i, d, bytes(x)))
    sigs, label, have_dbc = decode.signals(frames, vin)
    pol = decode.polled(ps)
    for v in pol:
        v["age"] = round(now - v.pop("t"), 1)
    cells = ps.msgs.get((0x7BB, 0x61, 0x02))
    cv = decode.cell_voltages(cells[0]) if cells else []
    return {"active": True, "vin": vin, "dbc": label, "have_dbc": have_dbc, "signals": sigs,
            "polled": pol, "cells": cv, "fast": fast["on"], "history": balance_history(cid, cv, now)}


# Cells that went out of balance (more than 10 mV from the pack average) in the current session,
# one entry per cell with its lowest and highest voltage and its worst deviation, like the min/max
# of a meter. Kept per connection, so a new session (new connection) starts with an empty list.
balance_state = {}


def balance_history(cid, cv, now):
    for old in [k for k in balance_state if k != cid]:
        del balance_state[old]
    cells = balance_state.setdefault(cid, {})
    if len(cv) >= 4:
        avg = round(sum(cv) / len(cv))
        for i, c in enumerate(cv):
            dv = c - avg
            e = cells.get(i + 1)
            if e is None and abs(dv) > 10:
                e = cells[i + 1] = {"cell": i + 1, "module": i // 4 + 1, "first": now,
                                    "lo": c, "hi": c, "peak": dv}
            if e is None:
                continue
            e["lo"], e["hi"] = min(e["lo"], c), max(e["hi"], c)
            if abs(dv) > abs(e["peak"]):
                e["peak"] = dv
    return sorted(cells.values(), key=lambda e: e["cell"])


def cars():
    """Every car (by VIN) with its connections; unidentified connections grouped last."""
    with db_lock:
        rows = db.execute(
            """SELECT c.id, c.start_t, c.end_t, c.vin,
                      (SELECT COUNT(*) FROM frames f WHERE f.conn=c.id) FROM connections c
               ORDER BY c.start_t""").fetchall()
    out = {}
    for cid, s, e, vin, n in rows:
        car = out.setdefault(vin, {"vin": vin, "connections": []})
        car["connections"].append({"id": cid, "start": s, "end": e, "frames": n})
    return sorted(out.values(), key=lambda c: (c["vin"] is None, c["vin"] or ""))


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _form(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return parse_qs(raw.decode("utf-8", "replace"))

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/hello":                     # board says which WiFi network it is on (+ VIN)
            q = self._form()
            g = lambda k: q.get(k, [""])[0][:64]
            now = time.time()
            with db_lock:
                db.execute("""INSERT INTO devices(board,ssid,ip,bssid,hello_t,peer) VALUES(?,?,?,?,?,?)
                              ON CONFLICT(board) DO UPDATE SET ssid=excluded.ssid, ip=excluded.ip,
                              bssid=excluded.bssid, hello_t=excluded.hello_t, peer=excluded.peer""",
                           (int(g("board") or 0), g("ssid"), g("ip"), g("bssid"), now, self.client_address[0]))
                # Once a board knows the VIN it repeats it here, so a server restart mid-drive
                # still labels the new connection.
                if VIN_RE.match(g("vin")) and conn["id"] is not None and now - conn["last"] <= ACTIVE_S:
                    set_vin(conn["id"], g("vin"))
                db.commit()
            return self._send(200, b"ok", "text/plain")
        if u.path == "/vin":                       # board reports the result of its automatic VIN read
            q = self._form()
            board, ok, vin = int(q.get("board", ["0"])[0]), q.get("ok", ["0"])[0] == "1", q.get("vin", [""])[0]
            note = q.get("note", [""])[0][:60]
            ok = ok and bool(VIN_RE.match(vin))
            now = time.time()
            with db_lock:
                cid = touch_conn(now)
                if ok:
                    set_vin(cid, vin)
                db.execute("INSERT INTO events(t,board,kind,value,note,conn) VALUES(?,?,?,?,?,?)",
                           (now, board, "vin" if ok else "vin_fail", vin if ok else None, note, cid))
                db.commit()
            return self._send(200, b"ok", "text/plain")
        if u.path == "/api/fast":                  # dashboard button
            fast["on"] = parse_qs(u.query).get("on", ["0"])[0] == "1"
            return self._send(200, json.dumps(fast).encode())
        if u.path != "/ingest":
            return self._send(404)
        rx = time.time()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if len(body) < HDR.size:
            return self._send(400)
        ver = body[5]
        hdr = HDR2 if ver == 2 else HDR
        if ver not in (1, 2) or len(body) < hdr.size:
            return self._send(400)
        vals = hdr.unpack_from(body)
        magic, board, _, count, boot_id, t_send, dropped = vals[:7]
        rssi = vals[7] if ver == 2 else None
        if magic != MAGIC or len(body) != hdr.size + count * REC.size:
            return self._send(400)
        # Device clock -> wall clock: the batch was sent at device time t_send and
        # received at `rx`, so wall = rx - (t_send - ts)/1e6 (error ~ one-way latency).
        recs = []
        for i in range(count):
            ts, fid, ch, flags, dlc, _, data = REC.unpack_from(body, hdr.size + i * REC.size)
            recs.append((rx - (t_send - ts) / 1e6, board, boot_id, ch, fid, flags, dlc, data[:dlc]))
        with db_lock:
            cid = touch_conn(rx, board, boot_id)
            db.executemany("""INSERT INTO frames(t,board,boot_id,ch,id,flags,dlc,data,conn)
                              VALUES(?,?,?,?,?,?,?,?,?)""", [r + (cid,) for r in recs])
            db.execute("INSERT INTO batches VALUES(?,?,?,?,?,?)", (rx, board, boot_id, count, dropped, rssi))
            db.commit()
        self._send(200, b"ok fast" if fast["on"] else b"ok", "text/plain")   # the board reads the flag

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/stats":
            with db_lock:
                stat_rows = db.execute(
                    "SELECT board, ch, COUNT(*), MAX(t) FROM frames GROUP BY board, ch").fetchall()
            out = []
            for board, ch, n, last in stat_rows:
                info = CHANNELS.get(str(board), {}).get("AB"[ch], {})
                out.append({"board": board, "ch": "AB"[ch], "name": info.get("name"),
                            "fd_capable": info.get("fd"), "frames": n, "last": last})
            return self._send(200, json.dumps(out, indent=1).encode())
        if u.path == "/frames":
            sql, args = "SELECT t,board,ch,id,flags,dlc,data FROM frames", []
            conds = []
            for k, col in (("board", "board"), ("ch", "ch"), ("conn", "conn")):
                if k in q:
                    conds.append(f"{col}=?"); args.append(int(q[k][0]))
            if conds: sql += " WHERE " + " AND ".join(conds)
            sql += " ORDER BY t DESC LIMIT ?"; args.append(min(int(q.get("limit", ["100"])[0]), 5000))
            with db_lock:
                got = db.execute(sql, args).fetchall()
            rows = [{"t": t, "board": b, "ch": "AB"[c], "id": f"0x{i:X}", "ext": bool(f & 1),
                     "dlc": d, "data": bytes(x).hex()} for t, b, c, i, f, d, x in got]
            return self._send(200, json.dumps(rows).encode())
        if u.path == "/api/status":
            return self._send(200, json.dumps(status()).encode())
        if u.path == "/api/decoded":
            cid = int(q["conn"][0]) if "conn" in q else None
            return self._send(200, json.dumps(decoded(cid)).encode())
        if u.path == "/api/cars":
            return self._send(200, json.dumps(cars(), indent=1).encode())
        if u.path in ("/", "/index.html"):
            return self._send(200, open(os.path.join(HERE, "dashboard.html"), "rb").read(),
                              "text/html; charset=utf-8")
        self._send(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=os.path.join(HERE, "canlog.db"))
    a = ap.parse_args()
    db = init_db(a.db)
    print(f"listening on 0.0.0.0:{a.port}, db={a.db}")
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()
