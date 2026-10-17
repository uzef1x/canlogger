// ─────────────────────────────────────────────────────────────────────────────
// Wire format shared with server/server.py. Little-endian, packed.
//   POST /ingest body = LogHeader followed by `count` LogRecords.
// Timestamps are device monotonic microseconds (esp_timer). The server converts
// them to wall-clock time using t_send_us (the device clock at the moment the
// batch was sent) against its own receive time, so no clock sync is needed on
// the device. Error ≈ one-way network latency (tens of ms on a hotspot).
// ─────────────────────────────────────────────────────────────────────────────
#pragma once
#include <stdint.h>

#define LOG_MAGIC   0x31474C43u   // "CLG1"
#define LOG_VERSION 2   // v2 adds rssi (the server still accepts v1)

enum : uint8_t { CH_A = 0, CH_B = 1 };            // CH_A = MCP2518FD, CH_B = TWAI
enum : uint8_t { FLAG_EXT = 1, FLAG_RTR = 2 };

struct __attribute__((packed)) LogRecord {        // 24 bytes
  uint64_t ts_us;
  uint32_t id;
  uint8_t  ch;
  uint8_t  flags;
  uint8_t  dlc;
  uint8_t  reserved;
  uint8_t  data[8];
};

struct __attribute__((packed)) LogHeader {        // 28 bytes
  uint32_t magic;
  uint8_t  board_id;
  uint8_t  version;
  uint16_t count;
  uint32_t boot_id;      // random per boot; distinguishes restarts of ts_us
  uint64_t t_send_us;
  uint32_t dropped;      // records lost to a full ring since boot
  int8_t   rssi;         // WiFi signal strength in dBm at send time (0 = not connected)
  uint8_t  pad[3];
};

static_assert(sizeof(LogRecord) == 24, "LogRecord size");
static_assert(sizeof(LogHeader) == 28, "LogHeader size");
