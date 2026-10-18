// ─────────────────────────────────────────────────────────────────────────────
// Logger configuration. Override any value in include/config_local.h (gitignored;
// copy config_local.example.h). Never commit WiFi credentials.
// ─────────────────────────────────────────────────────────────────────────────
#pragma once

#if __has_include("config_local.h")
#include "config_local.h"
#endif

#ifndef LOGGER_BOARD_ID
#define LOGGER_BOARD_ID 1
#endif

// Phone hotspot credentials.
#ifndef WIFI_SSID
#define WIFI_SSID "CHANGE_ME"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "CHANGE_ME"
#endif

// Ingest endpoint on the Mac (its address on the hotspot network).
#ifndef SERVER_URL
#define SERVER_URL "http://172.20.10.2:8000/ingest"
#endif

// Optional second server address (e.g. the Mac on the home network when SERVER_URL is its
// hotspot address). After 2 failed requests in a row the logger switches to the other one.
#ifndef SERVER_URL_ALT
#define SERVER_URL_ALT ""
#endif

// Secret sent to the server in the X-Logger-Token header (set it in config_local.h, never commit it).
#ifndef SERVER_TOKEN
#define SERVER_TOKEN ""
#endif

// Capture ring buffer, in 24-byte records (lives in PSRAM). 131072 = 3 MiB.
#ifndef RING_RECORDS
#define RING_RECORDS 131072
#endif

// Maximum records per HTTP POST. A batch holds whatever arrived during the previous request, up
// to this cap. Every request costs a TLS handshake, so small batches cannot keep up with a ZE0
// bus (~2,400 frames/s): at 400 per request the ring buffer overflowed.
#ifndef BATCH_RECORDS
#define BATCH_RECORDS 20000
#endif
