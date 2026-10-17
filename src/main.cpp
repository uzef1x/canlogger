// ─────────────────────────────────────────────────────────────────────────────
// Nissan LEAF / e-NV200 CAN logger — LilyGo T-2CANFD, passive listen-only tap.
//   capture task (core 1, high prio) : drain both CAN channels -> PSRAM ring
//   uplink task  (core 0)            : WiFi hotspot -> batched HTTP POST
//   loop                             : NVS housekeeping + serial stats
// ─────────────────────────────────────────────────────────────────────────────
#include <Arduino.h>
#include <WiFi.h>
#include "esp_timer.h"
#include "config.h"
#include "can_bus.h"
#include "ring.h"
#include "uplink.h"

static uint32_t g_count[2] = {0, 0};

static void capture_task(void *) {
  LogRecord r;
  uint32_t rate_ms = 0, rate_last = 0;
  for (;;) {
    // CAR-CAN counts as alive at >= 20 frames/s; the VIN read waits for that.
    if (millis() - rate_ms >= 1000) {
      rate_ms = millis();
      canbus_vin_auto(g_count[CH_B] - rate_last >= 20);
      rate_last = g_count[CH_B];
    }
    canbus_poll();
    canbus_vin_tick();
    bool any = false;
    for (uint8_t ch = CH_A; ch <= CH_B; ch++) {
      while (canbus_receive(ch, r)) {
        // Stamped at drain time, not at the wire: ±1-2 ms jitter. Hardware
        // timestamping (MCP2518FD TBC) is the upgrade if ordering matters.
        r.ts_us = (uint64_t)esp_timer_get_time();
        ring_push(r);
        if (ch == CH_B) canbus_vin_feed(r);
        g_count[ch]++;
        any = true;
      }
    }
    if (!any) vTaskDelay(1);
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);   // never block when no USB host is attached
  delay(300);
  Serial.printf("[boot] CAN logger board %d\n", LOGGER_BOARD_ID);
  if (!ring_begin()) { Serial.println("[boot] PSRAM ring alloc FAILED"); }
  if (!canbus_begin()) Serial.println("[boot] CAN init failed");
#if LOGGER_BOARD_ID == 1
  canbus_vin_auto_enable();   // board 1's port B is the one wired to CAR-CAN (OBD 6/14)
#endif
  xTaskCreatePinnedToCore(capture_task, "capture", 6144, nullptr, 5, nullptr, 1);
  uplink_start();
}

void loop() {
  canbus_housekeeping();
  static uint32_t last = 0;
  if (millis() - last > 5000) {
    last = millis();
    Serial.printf("[stat] A=%lu B=%lu ring=%lu sent=%lu dropped=%lu wifi=%d rssi=%d dBm\n",
                  (unsigned long)g_count[0], (unsigned long)g_count[1],
                  (unsigned long)ring_used(), (unsigned long)uplink_sent(),
                  (unsigned long)ring_dropped(), uplink_connected(), uplink_connected() ? WiFi.RSSI() : 0);
  }
  delay(20);
}
