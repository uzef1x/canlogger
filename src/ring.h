// Single-producer / single-consumer ring of LogRecords in PSRAM.
// Producer = capture task, consumer = uplink task. The consumer peeks and only
// commits (advances the tail) after the server acknowledged the batch, so a
// failed upload loses nothing. A full ring drops NEW records and counts them.
#pragma once
#include "log_format.h"

bool     ring_begin();
void     ring_push(const LogRecord &r);               // producer only
uint32_t ring_peek(LogRecord *dst, uint32_t max);     // consumer only, no commit
void     ring_commit(uint32_t n);                     // consumer only
uint32_t ring_used();
uint32_t ring_dropped();
