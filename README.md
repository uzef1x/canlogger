# CAN logger — Nissan LEAF / e-NV200

Passive, listen-only CAN logger on two LilyGo T-2CANFD boards. Each board logs two
channels and uploads timestamped frames over WiFi (phone hotspot) to a server.

| Board | Ch A (MCP2518FD, FD-capable) | Ch B (TWAI, classic only) |
|-------|------------------------------|---------------------------|
| 1     | EV-CAN                       | CAR-CAN                   |
| 2     | AV-CAN                       | spare                     |

Mapping lives in `server/channels.json`; the firmware only sends board id + channel A/B.
All Nissan buses are classic CAN 500 kbit/s, so FD capability is informational.
The boards never transmit or ACK. EV-CAN is not on the OBD port; tap it at the harness.

## Firmware
1. `cp include/config_local.example.h include/config_local.h`, set hotspot SSID/password
   and `SERVER_URL` (the Mac's IP on the hotspot).
2. `pio run -e board1 -t upload` / `pio run -e board2 -t upload`.

Capture → 3 MiB PSRAM ring → batched HTTP POST. The ring is only drained after the
server acks, so a dropped hotspot loses nothing until the ring fills (counted in `dropped`).

## Server (Mac)
`python3 server/server.py` → SQLite `server/canlog.db`; `/stats`, `/frames?board=1&ch=0`.
`python3 server/simulate.py` posts a fake batch to test without hardware.
The dashboard decodes signals with the Leaf DBC files of dalathegreat/leaf_can_bus_messages (GPL-3.0):
run `sh server/fetch_dbc.sh` once to download them (they are git-ignored, not copied into this repo).

## Known gaps
- Timestamps are taken at drain time (±1–2 ms) and mapped to wall clock by the server (± network latency).
- Ring is RAM-only: a reboot or a long hotspot outage loses buffered frames (flash spill not built).
- Oscillator auto-detect needs live bus traffic on first run.
- Untested on hardware.
