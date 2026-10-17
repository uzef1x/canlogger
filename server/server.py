#!/usr/bin/env python3
"""CAN logger ingest server (stdlib only).

POST /ingest   binary batch from a logger board (see src/log_format.h)
GET  /stats    per board/channel frame counts + last seen
GET  /frames   ?board=1&ch=0&limit=100  newest frames as JSON
GET  /         live dashboard (server/dashboard.html, polls /api/status)
GET  /api/status  boards, channels, live IDs and recent frames as JSON
Run:  python3 server.py [--port 8000] [--db canlog.db]
"""
import argparse, json, os, sqlite3, struct, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HDR = struct.Struct("<IBBHIQI")       # magic, board, ver, count, boot_id, t_send_us, dropped
REC = struct.Struct("<QIBBBB8s")      # ts_us, id, ch, flags, dlc, reserved, data
MAGIC = 0x31474C43
HERE = os.path.dirname(os.path.abspath(__file__))
CHANNELS = json.load(open(os.path.join(HERE, "channels.json")))

db = None
db_lock = threading.Lock()   # one shared sqlite connection: serialise every use


def init_db(path):
    c = sqlite3.connect(path, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS frames(
        t REAL NOT NULL,          -- wall-clock seconds (server-estimated)
        board INTEGER NOT NULL, boot_id INTEGER NOT NULL, ch INTEGER NOT NULL,
        id INTEGER NOT NULL, flags INTEGER NOT NULL, dlc INTEGER NOT NULL, data BLOB NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_t ON frames(t)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_bc ON frames(board, ch, t)")
    c.execute("""CREATE TABLE IF NOT EXISTS batches(
        rx REAL, board INTEGER, boot_id INTEGER, count INTEGER, dropped INTEGER)""")
    c.commit()
    return c


WINDOW = 10.0   # seconds used for rates and the live-ID table


def status():
    with db_lock:
        return _status()


def _status():
    now = time.time()
    since = now - WINDOW
    boards = {}
    for b, rx, boot, dropped, nb in db.execute(
            """SELECT board, MAX(rx), boot_id, dropped, COUNT(*) FROM batches
               GROUP BY board"""):
        boards[b] = {"board": b, "last_rx_age": now - rx, "boot_id": boot,
                     "dropped": dropped, "batches": nb}
    chans = {}
    for b, brd in CHANNELS.items():
        for ci, letter in enumerate("AB"):
            info = brd.get(letter, {})
            chans[(int(b), ci)] = {"board": int(b), "ch": letter, "name": info.get("name"),
                                   "fd": info.get("fd"), "total": 0, "rate": 0.0, "last_age": None}
    for b, c, n, last in db.execute(
            "SELECT board, ch, COUNT(*), MAX(t) FROM frames GROUP BY board, ch"):
        d = chans.setdefault((b, c), {"board": b, "ch": "AB"[c], "name": None, "fd": None})
        d.update(total=n, last_age=now - last)
    for b, c, n in db.execute(
            "SELECT board, ch, COUNT(*) FROM frames WHERE t>? GROUP BY board, ch", (since,)):
        if (b, c) in chans:
            chans[(b, c)]["rate"] = n / WINDOW
    ids = [{"board": b, "ch": "AB"[c], "id": f"0x{i:03X}", "rate": n / WINDOW,
            "dlc": d, "data": bytes(x).hex(" ")}
           for b, c, i, n, d, x in db.execute(
               """SELECT board, ch, id, COUNT(*), dlc, data FROM frames WHERE t>?
                  GROUP BY board, ch, id ORDER BY board, ch, id LIMIT 300""", (since,))]
    recent = [{"age": now - t, "board": b, "ch": "AB"[c], "id": f"0x{i:03X}",
               "dlc": d, "data": bytes(x).hex(" ")}
              for t, b, c, i, d, x in db.execute(
                  "SELECT t, board, ch, id, dlc, data FROM frames ORDER BY t DESC LIMIT 40")]
    return {"now": now, "window": WINDOW, "boards": sorted(boards.values(), key=lambda x: x["board"]),
            "channels": [chans[k] for k in sorted(chans)], "ids": ids, "recent": recent}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/ingest":
            return self._send(404)
        rx = time.time()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if len(body) < HDR.size:
            return self._send(400)
        magic, board, ver, count, boot_id, t_send, dropped = HDR.unpack_from(body)
        if magic != MAGIC or ver != 1 or len(body) != HDR.size + count * REC.size:
            return self._send(400)
        # Device clock -> wall clock: the batch was sent at device time t_send and
        # received at `rx`, so wall = rx - (t_send - ts)/1e6 (error ~ one-way latency).
        rows = []
        for i in range(count):
            ts, fid, ch, flags, dlc, _, data = REC.unpack_from(body, HDR.size + i * REC.size)
            rows.append((rx - (t_send - ts) / 1e6, board, boot_id, ch, fid, flags, dlc, data[:dlc]))
        with db_lock:
            db.executemany("INSERT INTO frames VALUES(?,?,?,?,?,?,?,?)", rows)
            db.execute("INSERT INTO batches VALUES(?,?,?,?,?)", (rx, board, boot_id, count, dropped))
            db.commit()
        self._send(200, b"ok", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/stats":
            out = []
            with db_lock:
                stat_rows = db.execute(
                    "SELECT board, ch, COUNT(*), MAX(t) FROM frames GROUP BY board, ch").fetchall()
            for board, ch, n, last in stat_rows:
                info = CHANNELS.get(str(board), {}).get("AB"[ch], {})
                out.append({"board": board, "ch": "AB"[ch], "name": info.get("name"),
                            "fd_capable": info.get("fd"), "frames": n, "last": last})
            return self._send(200, json.dumps(out, indent=1).encode())
        if u.path == "/frames":
            sql, args = "SELECT t,board,ch,id,flags,dlc,data FROM frames", []
            conds = []
            for k, col in (("board", "board"), ("ch", "ch")):
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
