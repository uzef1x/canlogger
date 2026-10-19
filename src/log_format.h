// ─────────────────────────────────────────────────────────────────────────────
// Wire format shared with server/server.py. Little-endian, packed.
//   POST /ingest body = LogHeader followed by `count` LogRecords (v1, v2) or by the
//   log_codec.h encoding of `count` records (v3 to v5).
// Record timestamps are device monotonic microseconds (esp_timer). From v5 the header also
// carries t_wall_us, the board's NTP-synced wall clock read at the same instant as t_send_us,
// and the server places every frame at t_wall_us - (t_send_us - ts_us): frame times come from
// the board alone and do not depend on when or how fast the batch reached the server.
// Before v5 the server used its own receive time instead (wrong by the upload time).
// ─────────────────────────────────────────────────────────────────────────────
#pragma once
#include <stdint.h>

#define LOG_MAGIC   0x31474C43u   // "CLG1"
#define LOG_VERSION 5   // v2 adds rssi; v3 and v4 send the records compacted (log_codec.h); v5 adds t_wall_us. The server still accepts v1 to v4

enum : uint8_t { CH_A = 0, CH_B = 1 };            // CH_A = MCP2518FD, CH_B = TWAI
enum : uint8_t { FLAG_EXT = 1, FLAG_RTR = 2, FLAG_TX = 4 };   // FLAG_TX: sent by this logger, not heard on the bus

struct __attribute__((packed)) LogRecord {        // 24 bytes
  uint64_t ts_us;
  uint32_t id;
  uint8_t  ch;
  uint8_t  flags;
  uint8_t  dlc;
  uint8_t  reserved;
  uint8_t  data[8];
};

struct __attribute__((packed)) LogHeader {        // 36 bytes
  uint32_t magic;
  uint8_t  board_id;
  uint8_t  version;
  uint16_t count;
  uint32_t boot_id;      // random per boot; distinguishes restarts of ts_us
  uint64_t t_send_us;
  uint32_t dropped;      // records lost to a full ring since boot
  int8_t   rssi;         // WiFi signal strength in dBm at send time (0 = not connected)
  uint8_t  pad[3];
  uint64_t t_wall_us;    // v5: wall clock (microseconds since 1970, NTP) at the instant of t_send_us
};

static_assert(sizeof(LogRecord) == 24, "LogRecord size");
static_assert(sizeof(LogHeader) == 36, "LogHeader size");
