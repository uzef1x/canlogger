#include "uplink.h"
#include "config.h"
#include "ring.h"
#include "can_bus.h"
#include "log_format.h"
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_timer.h"
#include "esp_system.h"

static uint32_t g_boot_id;
static volatile uint32_t g_sent = 0;
static uint8_t *g_body = nullptr;

bool uplink_connected() { return WiFi.status() == WL_CONNECTED; }
uint32_t uplink_sent() { return g_sent; }

// Server tells us what to do in the body of its /ingest reply ("ok" or "ok vin").
// The VIN result goes to <server>/vin as plain text.
static void send_vin_result() {
  char vin[18];
  bool ok;
  if (!canbus_vin_take(vin, ok)) return;
  String url = SERVER_URL;
  url.replace("/ingest", "/vin");
  char body[64];
  snprintf(body, sizeof(body), "board=%d&ok=%d&vin=%s", LOGGER_BOARD_ID, ok ? 1 : 0, vin);
  HTTPClient http;
  http.setTimeout(5000);
  if (http.begin(url)) {
    http.addHeader("Content-Type", "text/plain");
    http.POST((uint8_t *)body, strlen(body));
    http.end();
  }
  Serial.printf("[vin] %s %s\n", ok ? "read" : "FAILED", vin);
}

static void uplink_task(void *) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t backoff = 500;

  for (;;) {
    if (uplink_connected()) send_vin_result();
    if (!uplink_connected() || ring_used() == 0) {
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    LogRecord *recs = (LogRecord *)(g_body + sizeof(LogHeader));
    const uint32_t n = ring_peek(recs, BATCH_RECORDS);

    LogHeader h;
    h.magic = LOG_MAGIC;
    h.board_id = LOGGER_BOARD_ID;
    h.version = LOG_VERSION;
    h.count = (uint16_t)n;
    h.boot_id = g_boot_id;
    h.t_send_us = (uint64_t)esp_timer_get_time();   // stamped as late as possible
    h.dropped = ring_dropped();
    h.rssi = (int8_t)WiFi.RSSI();
    h.pad[0] = h.pad[1] = h.pad[2] = 0;
    memcpy(g_body, &h, sizeof(h));

    HTTPClient http;
    http.setReuse(true);
    http.setConnectTimeout(3000);
    http.setTimeout(5000);
    int code = -1;
    if (http.begin(SERVER_URL)) {
      http.addHeader("Content-Type", "application/octet-stream");
      code = http.POST(g_body, sizeof(LogHeader) + n * sizeof(LogRecord));
      if (code == 200 && http.getString().indexOf("vin") >= 0) canbus_vin_request();
      http.end();
    }
    if (code == 200) {
      ring_commit(n);
      g_sent += n;
      backoff = 500;
      vTaskDelay(pdMS_TO_TICKS(n == BATCH_RECORDS ? 1 : 250));  // drain fast, idle slow
    } else {
      Serial.printf("[uplink] POST failed (%d), retry in %lu ms\n", code, (unsigned long)backoff);
      vTaskDelay(pdMS_TO_TICKS(backoff));
      backoff = min<uint32_t>(backoff * 2, 15000);
    }
  }
}

void uplink_start() {
  g_boot_id = esp_random();
  g_body = (uint8_t *)ps_malloc(sizeof(LogHeader) + BATCH_RECORDS * sizeof(LogRecord));
  xTaskCreatePinnedToCore(uplink_task, "uplink", 8192, nullptr, 1, nullptr, 0);
}
