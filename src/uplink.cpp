#include "uplink.h"
#include "config.h"
#include "ring.h"
#include "log_codec.h"
#include "can_bus.h"
#include "log_format.h"
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <time.h>
#include "ca_cert.h"
#include "esp_timer.h"
#include "esp_system.h"

static uint32_t g_boot_id;
static volatile uint32_t g_sent = 0;
static uint8_t *g_body = nullptr;   // LogHeader + encoded batch
static LogRecord *g_recs = nullptr;  // batch as peeked from the ring
static uint8_t *g_prev = nullptr;    // encoder scratch

// Two server addresses: the logger stays on one until two requests in a row fail, then tries the other.
static uint8_t g_url_idx = 0, g_url_fails = 0;
static String server_url() { return (g_url_idx && SERVER_URL_ALT[0]) ? String(SERVER_URL_ALT) : String(SERVER_URL); }
static void server_result(bool ok) {
  if (ok) { g_url_fails = 0; return; }
  if (++g_url_fails >= 2 && SERVER_URL_ALT[0]) {
    g_url_idx ^= 1;
    g_url_fails = 0;
    Serial.printf("[uplink] switching server to %s\n", server_url().c_str());
  }
}

// https:// URLs go through one shared TLS client (the connection is kept open between requests).
// The certificate is only valid against the real date, so the clock is set from NTP first.
static WiFiClientSecure g_tls;
static bool g_tls_ready = false;

static bool clock_ok() {
  static bool asked = false;
  if (time(nullptr) > 1700000000) return true;
  if (!asked) { configTime(0, 0, "pool.ntp.org", "time.google.com"); asked = true; }
  return false;
}

static bool begin_request(HTTPClient &http, const String &url) {
  bool ok;
  if (url.startsWith("https://")) {
    if (!g_tls_ready) { g_tls.setCACert(ISRG_ROOT_X1); g_tls_ready = true; }
    ok = http.begin(g_tls, url);
  } else {
    ok = http.begin(url);
  }
  if (!ok) return false;
  http.setReuse(true);
  if (SERVER_TOKEN[0]) http.addHeader("X-Logger-Token", SERVER_TOKEN);
  return true;
}

bool uplink_connected() { return WiFi.status() == WL_CONNECTED; }
uint32_t uplink_sent() { return g_sent; }

// The VIN result goes to <server>/vin as plain text. Nothing the server sends can make
// the board transmit: the VIN read is started by the board itself, after power-on.
static void send_vin_result() {
  char vin[18], note[48];
  bool ok;
  if (!canbus_vin_take(vin, ok, note)) return;
  String url = server_url();
  url.replace("/ingest", "/vin");
  char body[128];
  snprintf(body, sizeof(body), "board=%d&ok=%d&vin=%s&note=%s", LOGGER_BOARD_ID, ok ? 1 : 0, vin, note);
  HTTPClient http;
  http.setTimeout(5000);
  if (begin_request(http, url)) {
    http.addHeader("Content-Type", "text/plain");
    server_result(http.POST((uint8_t *)body, strlen(body)) == 200);
    http.end();
  }
  Serial.printf("[vin] %s %s (%s)\n", ok ? "read" : "FAILED", vin, note);
}

// Tell the server which network we are on (every 60 s, so a server restart relearns it).
static void send_hello() {
  static uint32_t last = 0;
  if (last && millis() - last < 60000) return;
  last = millis();
  String url = server_url();
  url.replace("/ingest", "/hello");
  String body = "board=" + String(LOGGER_BOARD_ID) + "&ssid=" + WiFi.SSID() +
                "&ip=" + WiFi.localIP().toString() + "&bssid=" + WiFi.BSSIDstr();
  char vin[18];
  if (canbus_vin_known(vin)) body += String("&vin=") + vin;   // lets a restarted server relabel the car
  HTTPClient http;
  http.setTimeout(3000);
  if (begin_request(http, url)) {
    http.addHeader("Content-Type", "text/plain");
    server_result(http.POST((uint8_t *)body.c_str(), body.length()) == 200);
    http.end();
  }
}

static void uplink_task(void *) {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);   // modem sleep holds incoming TCP ACKs until the next beacon: slow uploads
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t backoff = 500;

  uint32_t down_since = millis();
  for (;;) {
    // A failed first connection is not retried by auto-reconnect, so restart the attempt
    // ourselves if we have been offline for 15 s.
    if (uplink_connected()) down_since = millis();
    else if (millis() - down_since > 15000) {
      Serial.println("[wifi] retrying connection");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      down_since = millis();
    }
    if (uplink_connected() && server_url().startsWith("https://") && !clock_ok()) {
      vTaskDelay(pdMS_TO_TICKS(500));    // waiting for NTP: a TLS certificate cannot be checked without the date
      continue;
    }
    if (uplink_connected()) { send_hello(); send_vin_result(); }
    if (!uplink_connected() || ring_used() == 0) {
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    const uint32_t n = ring_peek(g_recs, BATCH_RECORDS);
    const size_t enc = log_encode(g_recs, n, g_body + sizeof(LogHeader), g_prev);

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
    http.setTimeout(20000);   // a timed-out reply makes the board resend a batch the server may already have stored
    int code = -1;
    const bool was_open = g_tls.connected();
    const uint32_t t0 = millis();
    if (begin_request(http, server_url())) {
      http.addHeader("Content-Type", "application/octet-stream");
      code = http.POST(g_body, sizeof(LogHeader) + enc);
      server_result(code == 200);
      if (code == 200) canbus_set_fast_cells(http.getString().indexOf("fast") >= 0);
      http.end();
    }
    Serial.printf("[uplink] n=%lu %lu bytes code=%d %lu ms reused=%d\n", (unsigned long)n,
                  (unsigned long)enc, code, (unsigned long)(millis() - t0), (int)was_open);
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
  g_body = (uint8_t *)ps_malloc(sizeof(LogHeader) + BATCH_RECORDS * CODEC_MAX_PER_RECORD);
  g_recs = (LogRecord *)ps_malloc(BATCH_RECORDS * sizeof(LogRecord));
  g_prev = (uint8_t *)ps_malloc(CODEC_PREV_BYTES);
  xTaskCreatePinnedToCore(uplink_task, "uplink", 8192, nullptr, 1, nullptr, 0);
}
