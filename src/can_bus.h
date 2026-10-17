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

// ── VIN read (active, one-shot, CH_B only) ───────────────────────────────────
// The only place this firmware ever transmits. Request: UDS-style service 0x21,
// PID 0x81 to ECU 0x797 (the charger/PDM), reply on 0x79A, 17 ASCII VIN bytes
// (method taken from OVMS's vehicle_nissanleaf, which polls it on CAR-CAN).
// Triggered only by an explicit command from the server (dashboard button; the
// user confirms the car is parked). The driver is switched to normal mode for at
// most ~1.5 s, then back to listen-only. All frames stay in the log as usual.
void canbus_vin_request();                          // any task
void canbus_vin_tick();                             // capture task, every loop
void canbus_vin_feed(const LogRecord &r);           // capture task, each CH_B frame
// Returns true once when a result is ready. ok=false means timeout / no valid reply.
bool canbus_vin_take(char out[18], bool &ok);
