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

// Capture ring buffer, in 24-byte records (lives in PSRAM). 131072 = 3 MiB.
#ifndef RING_RECORDS
#define RING_RECORDS 131072
#endif

// Records per HTTP POST.
#ifndef BATCH_RECORDS
#define BATCH_RECORDS 400
#endif
