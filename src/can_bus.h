// CAN driver layer — passive (listen-only) capture on both channels.
//   CH_A = MCP2518FD (CAN-FD capable, run as classic 2.0B)
//   CH_B = native ESP32-S3 TWAI (classic only)
// Neither channel ever transmits or acknowledges: this is a tap.
#pragma once
#include "log_format.h"

bool canbus_begin();
// Oscillator detection for CH_A (20 vs 40 MHz crystal). Capture task only.
void canbus_poll();
// Persists a detected oscillator to NVS. Loop task only (flash write).
void canbus_housekeeping();
// Non-blocking. Fills id/flags/dlc/data/ch; caller stamps ts_us.
bool canbus_receive(uint8_t ch, LogRecord &out);
