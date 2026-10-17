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
// The only place this firmware ever transmits. Tries up to three read-only VIN
// requests in turn (standard OBD-II mode 09, UDS F190 to the charger/PDM, Nissan
// service 21/81) and stops at the first valid 17-character reply. Triggered only by
// an explicit command from the server (dashboard button; the user confirms the car
// is parked). The driver is in normal mode only for the attempt (a few seconds at
// most), then back to listen-only. Every frame stays in the log as usual.
void canbus_vin_request();                          // any task (used by auto mode)
// Auto mode: after power-on, once CAR-CAN is alive, read the VIN by itself; retry every
// 10 s up to 5 times if it fails, and stop at the first success.
void canbus_vin_auto_enable();
void canbus_vin_auto(bool bus_alive);               // capture task, ~once per second
bool canbus_vin_known(char out[18]);                // true once a VIN has been read this boot
void canbus_vin_tick();                             // capture task, every loop
void canbus_vin_feed(const LogRecord &r);           // capture task, each CH_B frame
// Returns true once when a result is ready. ok=false means no candidate worked;
// note says why for each, e.g. "A-timeout_B-nrc31_C-nrc80".
bool canbus_tx_pop(LogRecord &r);                   // capture task: frames this logger transmitted
bool canbus_vin_take(char vin[18], bool &ok, char note[48]);
