#!/usr/bin/env python3
"""Send a fake batch to the server to test it without hardware."""
import struct, sys, time, urllib.request
url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/ingest"
now = int(time.monotonic() * 1e6)
recs = b"".join(struct.pack("<QIBBBB8s", now + i * 1000, 0x1DB + i, i % 2, 0, 8, 0, bytes(range(8)))
                for i in range(10))
body = struct.pack("<IBBHIQI", 0x31474C43, 1, 1, 10, 1234, now + 20000, 0) + recs
print(urllib.request.urlopen(urllib.request.Request(url, body)).read())
