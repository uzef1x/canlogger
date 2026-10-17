#!/usr/bin/env python3
"""CAN logger ingest server (stdlib only).

POST /ingest   binary batch from a logger board (see src/log_format.h)
GET  /stats    per board/channel frame counts + last seen
GET  /frames   ?board=1&ch=0&limit=100  newest frames as JSON
Run:  python3 server.py [--port 8000] [--db canlog.db]
"""
import argparse, json, os, sqlite3, struct, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HDR = struct.Struct("<IBBHIQI")       # magic, board, ver, count, boot_id, t_send_us, dropped
REC = struct.Struct("<QIBBBB8s")      # ts_us, id, ch, flags, dlc, reserved, data
MAGIC = 0x31474C43
HERE = os.path.dirname(os.path.abspath(__file__))
CHANNELS = json.load(open(os.path.join(HERE, "channels.json")))

db = None


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
        db.executemany("INSERT INTO frames VALUES(?,?,?,?,?,?,?,?)", rows)
        db.execute("INSERT INTO batches VALUES(?,?,?,?,?)", (rx, board, boot_id, count, dropped))
        db.commit()
        self._send(200, b"ok", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/stats":
            out = []
            for board, ch, n, last in db.execute(
                    "SELECT board, ch, COUNT(*), MAX(t) FROM frames GROUP BY board, ch"):
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
            rows = [{"t": t, "board": b, "ch": "AB"[c], "id": f"0x{i:X}", "ext": bool(f & 1),
                     "dlc": d, "data": bytes(x).hex()} for t, b, c, i, f, d, x in db.execute(sql, args)]
            return self._send(200, json.dumps(rows).encode())
        self._send(200, b"CAN logger server. See /stats and /frames.\n", "text/plain")

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
