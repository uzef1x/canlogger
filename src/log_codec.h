// Lossless compact encoding of a batch of LogRecords (wire version 4). Header-only so the host
// test can compile it with a plain g++. The server side is decode_v4() in server/server.py; both
// must stay in step.
//
// The records of a batch are grouped by (channel, flags, can id, dlc); within a group they keep
// their original order. The server sorts the decoded records by timestamp again, so the order of
// groups carries no meaning.
//
//   varint            timestamp of the first record of the batch, in us
//   per group:
//     1 byte          tag = ch | flags << 1 | dlc << 4     (flags: FLAG_EXT, FLAG_RTR, FLAG_TX; dlc 0..8)
//     varint          CAN id
//     varint          number of records in the group
//     per record:
//       zigzag varint (this timestamp - the previous one of the group) - (the previous such difference)
//                     the first record of a group is measured from the batch's first timestamp,
//                     and its "previous difference" is 0
//       1 byte        mask: bit k set = data byte k differs from its prediction
//       n bytes       prediction errors (data - prediction) mod 256 for the set bits, low byte first
//   The prediction of a data byte is 2 * (previous value) - (value before that), mod 256, in the
//   same group (zeros before the first records): constant bytes and +1 counters both predict exactly.
//
// The `reserved` byte of LogRecord is not sent (always 0, ignored by the server).
// A batch is cut short if it would need more than CODEC_MAX_GROUPS groups; the caller sends the
// returned number of records and the rest go in the next batch.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "log_format.h"

#define CODEC_MAX_GROUPS 2048
#define CODEC_HASH 4096                 // slots, power of two, at least twice CODEC_MAX_GROUPS
#define CODEC_MAX_PER_RECORD 28         // worst case incl. group headers (9 bytes per group, one record each)
#define CODEC_OUT_BYTES(n) ((size_t)(n) * CODEC_MAX_PER_RECORD + 16)   // size of the output buffer for n records

struct CodecWork {
  uint16_t *gidx;                       // [n] group of each record
  uint16_t *order;                      // [n] record indices grouped, original order inside a group
  uint64_t key[CODEC_MAX_GROUPS];
  uint32_t cnt[CODEC_MAX_GROUPS];
  uint32_t start[CODEC_MAX_GROUPS];
  uint16_t slot[CODEC_HASH];            // group index + 1, 0 = empty
};

static inline size_t codec_varint(uint8_t *o, uint64_t v) {
  size_t n = 0;
  while (v >= 0x80) { o[n++] = (uint8_t)(v | 0x80); v >>= 7; }
  o[n++] = (uint8_t)v;
  return n;
}

static inline uint64_t codec_zigzag(int64_t d) { return ((uint64_t)d << 1) ^ (uint64_t)(d >> 63); }

// Encodes up to n records (n <= 65535). Returns the bytes written to out (which must hold
// CODEC_OUT_BYTES(n)) and sets *used to the number of records encoded (1..n when n > 0).
static inline size_t log_encode(const LogRecord *recs, uint32_t n, uint8_t *out, CodecWork *w, uint32_t *used) {
  memset(w->slot, 0, sizeof(w->slot));
  uint32_t groups = 0, i = 0;
  for (; i < n; i++) {
    const LogRecord &r = recs[i];
    const uint8_t dlc = r.dlc > 8 ? 8 : r.dlc;
    const uint64_t key = ((uint64_t)r.id << 8) | (r.ch & 1) | ((r.flags & 7) << 1) | (dlc << 4);
    uint32_t h = (uint32_t)((key * 0x9E3779B97F4A7C15ull) >> 52) & (CODEC_HASH - 1);
    while (w->slot[h] && w->key[w->slot[h] - 1] != key) h = (h + 1) & (CODEC_HASH - 1);
    if (!w->slot[h]) {
      if (groups == CODEC_MAX_GROUPS) break;          // cut the batch here
      w->key[groups] = key; w->cnt[groups] = 0;
      w->slot[h] = (uint16_t)(++groups);
    }
    w->gidx[i] = (uint16_t)(w->slot[h] - 1);
    w->cnt[w->slot[h] - 1]++;
  }
  const uint32_t m = i;                                // records taken
  *used = m;
  if (m == 0) return 0;
  uint32_t pos = 0;
  for (uint32_t g = 0; g < groups; g++) { w->start[g] = pos; pos += w->cnt[g]; }
  for (uint32_t k = 0; k < m; k++) w->order[w->start[w->gidx[k]]++] = (uint16_t)k;   // start[] ends one past each group
  size_t o = 0;
  const uint64_t base = recs[0].ts_us;
  o += codec_varint(out + o, base);
  for (uint32_t g = 0; g < groups; g++) {
    const uint64_t key = w->key[g];
    const uint8_t tag = (uint8_t)((key & 1) | (((key >> 1) & 7) << 1) | (((key >> 4) & 15) << 4));
    const uint8_t dlc = (uint8_t)((key >> 4) & 15);
    out[o++] = tag;
    o += codec_varint(out + o, key >> 8);
    o += codec_varint(out + o, w->cnt[g]);
    uint64_t pt = base;
    int64_t pd = 0;
    uint8_t p1[8] = {0}, p2[8] = {0};
    for (uint32_t k = w->start[g] - w->cnt[g]; k < w->start[g]; k++) {
      const LogRecord &r = recs[w->order[k]];
      const int64_t dt = (int64_t)(r.ts_us - pt);
      pt = r.ts_us;
      o += codec_varint(out + o, codec_zigzag((int64_t)((uint64_t)dt - (uint64_t)pd)));
      pd = dt;
      uint8_t mask = 0;
      const size_t mpos = o++;
      uint8_t cur[8];
      for (uint8_t b = 0; b < 8; b++) cur[b] = b < dlc ? r.data[b] : 0;
      for (uint8_t b = 0; b < dlc; b++) {
        const uint8_t e = (uint8_t)(cur[b] - (uint8_t)(2 * p1[b] - p2[b]));
        if (e) { mask |= (uint8_t)(1u << b); out[o++] = e; }
      }
      out[mpos] = mask;
      memcpy(p2, p1, 8);
      memcpy(p1, cur, 8);
    }
  }
  return o;
}
