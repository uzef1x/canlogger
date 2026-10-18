// Lossless compact encoding of a batch of LogRecords (wire version 3). Header-only so the host
// test (test/test_codec.sh) can compile it with a plain g++. The server side is decode_v3() in
// server/server.py; both must stay in step.
//
// Per record:
//   zigzag-varint  timestamp delta in us (the first record: delta from 0, i.e. absolute)
//   1 byte         tag = ch | flags << 1 | dlc << 4        (dlc is 0..8)
//   varint         CAN id
//   1 byte         mask: bit k set = data byte k differs from the previous frame of the same id
//   n bytes        the differing data bytes, XOR previous (low byte first)
// "Previous frame of the same id" is per batch and per channel, for 11-bit ids only; the first
// frame of an id in a batch, and every extended-id frame, is XORed with zeros (i.e. sent whole).
// The `reserved` byte of LogRecord is not sent (always 0, ignored by the server).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "log_format.h"

#define CODEC_IDS 2048
#define CODEC_PREV_BYTES (2 * CODEC_IDS * 8)
#define CODEC_MAX_PER_RECORD 25   // 10 (ts) + 1 (tag) + 5 (id) + 1 (mask) + 8 (data)

static inline size_t codec_varint(uint8_t *o, uint64_t v) {
  size_t n = 0;
  while (v >= 0x80) { o[n++] = (uint8_t)(v | 0x80); v >>= 7; }
  o[n++] = (uint8_t)v;
  return n;
}

// prev: CODEC_PREV_BYTES of scratch (cleared here). out: n * CODEC_MAX_PER_RECORD bytes.
// Returns the number of bytes written.
static inline size_t log_encode(const LogRecord *recs, uint32_t n, uint8_t *out, uint8_t *prev) {
  memset(prev, 0, CODEC_PREV_BYTES);
  static const uint8_t zeros[8] = {0};
  size_t o = 0;
  uint64_t last = 0;
  for (uint32_t i = 0; i < n; i++) {
    const LogRecord &r = recs[i];
    const int64_t d = (int64_t)(r.ts_us - last);
    last = r.ts_us;
    o += codec_varint(out + o, ((uint64_t)d << 1) ^ (uint64_t)(d >> 63));
    const uint8_t dlc = r.dlc > 8 ? 8 : r.dlc;
    out[o++] = (uint8_t)((r.ch & 1) | ((r.flags & 7) << 1) | (dlc << 4));
    o += codec_varint(out + o, r.id);
    uint8_t cur[8];
    for (uint8_t k = 0; k < 8; k++) cur[k] = k < dlc ? r.data[k] : 0;
    uint8_t *p = (uint8_t *)zeros;
    const bool keep = !(r.flags & FLAG_EXT) && r.id < CODEC_IDS;
    if (keep) p = prev + ((size_t)(r.ch & 1) * CODEC_IDS + r.id) * 8;
    uint8_t mask = 0;
    size_t mpos = o++;
    for (uint8_t k = 0; k < dlc; k++) {
      const uint8_t x = cur[k] ^ p[k];
      if (x) { mask |= (uint8_t)(1u << k); out[o++] = x; }
    }
    out[mpos] = mask;
    if (keep) memcpy(p, cur, 8);
  }
  return o;
}
