#include "ring.h"
#include "config.h"
#include <Arduino.h>

static LogRecord *g_buf = nullptr;
static volatile uint32_t g_head = 0;   // next write (producer)
static volatile uint32_t g_tail = 0;   // next read (consumer)
static volatile uint32_t g_dropped = 0;

bool ring_begin() {
  g_buf = (LogRecord *)ps_malloc(sizeof(LogRecord) * RING_RECORDS);
  return g_buf != nullptr;
}

void ring_push(const LogRecord &r) {
  const uint32_t h = g_head;
  const uint32_t next = (h + 1) % RING_RECORDS;
  if (next == g_tail) { g_dropped = g_dropped + 1; return; }
  g_buf[h] = r;
  __sync_synchronize();   // record visible before the head moves
  g_head = next;
}

uint32_t ring_used() {
  const uint32_t h = g_head, t = g_tail;
  return h >= t ? h - t : RING_RECORDS - t + h;
}

uint32_t ring_peek(LogRecord *dst, uint32_t max) {
  uint32_t t = g_tail;
  uint32_t n = min(ring_used(), max);
  for (uint32_t i = 0; i < n; i++) dst[i] = g_buf[(t + i) % RING_RECORDS];
  return n;
}

void ring_commit(uint32_t n) { g_tail = (g_tail + n) % RING_RECORDS; }
uint32_t ring_dropped() { return g_dropped; }
