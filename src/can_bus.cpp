#include "can_bus.h"
#include "board_pins.h"
#include <Arduino.h>
#include <SPI.h>
#include <ACAN2517FD.h>
#include <Preferences.h>
#include "driver/twai.h"

static ACAN2517FD gCanA(MCP2518_CS_GPIO, SPI, MCP2518_INT_GPIO);
static Preferences prefs;

// The T-2CANFD ships with a 20 or 40 MHz crystal on the MCP2518FD and offers no
// way to read it back; the wrong value gives the wrong bit rate and a deaf bus.
// Same approach as CANBRIDGE: use the NVS value if known, otherwise alternate
// listen-only trials until real frames arrive, then lock and persist. Listen-only
// never transmits or sends error flags, so a wrong trial cannot disturb the car.
// (Restricted-operation trials, needed by CANBRIDGE for a two-node segment, are
// not needed here: a tap on a vehicle bus always has other nodes to ACK.)
static constexpr uint32_t TRIAL_MS    = 600;
static constexpr uint8_t  LOCK_FRAMES = 2;

static const ACAN2517FDSettings::Oscillator TRIALS[2] = {
  ACAN2517FDSettings::OSC_20MHz, ACAN2517FDSettings::OSC_40MHz};

static ACAN2517FDSettings::Oscillator g_osc = ACAN2517FDSettings::OSC_20MHz;
static bool     g_begun = false;
static bool     g_detecting = false;
static uint8_t  g_trial = 0;
static uint8_t  g_trial_frames = 0;
static uint32_t g_trial_ms = 0;
static volatile bool g_save_pending = false;

static unsigned osc_mhz(ACAN2517FDSettings::Oscillator o) {
  return o == ACAN2517FDSettings::OSC_40MHz ? 40 : 20;
}

static bool beginA(ACAN2517FDSettings::Oscillator osc) {
  if (g_begun) gCanA.end();   // begin() leaks a task + heap per call otherwise
  g_begun = true;
  ACAN2517FDSettings s(osc, CAN_BITRATE, DataBitRateFactor::x1);
  s.mRequestedMode = ACAN2517FDSettings::ListenOnly;
  s.mDriverReceiveFIFOSize = 128;   // headroom for the capture task's poll period
  return gCanA.begin(s, [] { gCanA.isr(); }) == 0;
}

static bool beginBusA() {
  SPI.begin(SPI_SCLK_GPIO, SPI_MISO_GPIO, SPI_MOSI_GPIO, MCP2518_CS_GPIO);
  prefs.begin("logger", false);
  const uint8_t saved = prefs.isKey("canaosc") ? prefs.getUChar("canaosc", 0) : 0;
  if (saved == 20 || saved == 40) {
    g_osc = saved == 40 ? ACAN2517FDSettings::OSC_40MHz : ACAN2517FDSettings::OSC_20MHz;
    Serial.printf("[can_a] using saved %u MHz oscillator\n", saved);
    return beginA(g_osc);
  }
  g_detecting = true;
  g_trial = 0;
  g_trial_frames = 0;
  g_trial_ms = millis();
  Serial.println("[can_a] oscillator unknown, auto-detecting (needs bus traffic)");
  beginA(TRIALS[0]);
  return true;   // detection in progress is not an init failure
}

static bool beginBusB(twai_mode_t mode = TWAI_MODE_LISTEN_ONLY) {
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
      (gpio_num_t)CANB_TX_GPIO, (gpio_num_t)CANB_RX_GPIO, mode);
  g.rx_queue_len = 256;
  twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  if (twai_driver_install(&g, &t, &f) != ESP_OK) return false;
  return twai_start() == ESP_OK;
}

bool canbus_begin() {
  const bool a = beginBusA();
  const bool b = beginBusB();
  return a && b;
}

void canbus_poll() {
  if (!g_detecting) return;
  CANFDMessage m;
  while (gCanA.receive(m)) {
    if (g_trial_frames < LOCK_FRAMES) g_trial_frames++;
  }
  if (g_trial_frames >= LOCK_FRAMES) {
    g_osc = TRIALS[g_trial];
    g_detecting = false;
    g_save_pending = true;
    Serial.printf("[can_a] oscillator locked: %u MHz\n", osc_mhz(g_osc));
    return;
  }
  const uint32_t now = millis();
  if (now - g_trial_ms < TRIAL_MS) return;
  g_trial_ms = now;
  g_trial_frames = 0;
  g_trial = (g_trial + 1) % 2;
  beginA(TRIALS[g_trial]);
}

void canbus_housekeeping() {
  if (!g_save_pending) return;
  g_save_pending = false;
  prefs.putUChar("canaosc", (uint8_t)osc_mhz(g_osc));
}

bool canbus_receive(uint8_t ch, LogRecord &out) {
  out.ch = ch;
  out.reserved = 0;
  if (ch == CH_A) {
    if (g_detecting) return false;   // canbus_poll() owns the controller
    CANFDMessage m;
    if (!gCanA.receive(m)) return false;
    out.id = m.id;
    out.flags = (m.ext ? FLAG_EXT : 0) | (m.type == CANFDMessage::CAN_REMOTE ? FLAG_RTR : 0);
    out.dlc = m.len > 8 ? 8 : m.len;
    for (uint8_t i = 0; i < 8; i++) out.data[i] = i < out.dlc ? m.data[i] : 0;
    return true;
  }
  twai_message_t m;
  if (twai_receive(&m, 0) != ESP_OK) return false;
  out.id = m.identifier;
  out.flags = (m.extd ? FLAG_EXT : 0) | (m.rtr ? FLAG_RTR : 0);
  out.dlc = m.data_length_code > 8 ? 8 : m.data_length_code;
  for (uint8_t i = 0; i < 8; i++) out.data[i] = i < out.dlc ? m.data[i] : 0;
  return true;
}

// ── VIN read ─────────────────────────────────────────────────────────────────
static constexpr uint32_t VIN_REQ_ID = 0x797;
static constexpr uint32_t VIN_RSP_ID = 0x79A;
static constexpr uint32_t VIN_TIMEOUT_MS = 1500;

enum VinState : uint8_t { V_IDLE, V_SEND, V_WAIT_FF, V_WAIT_CF };
static volatile bool g_vin_req = false;
static VinState g_vin_state = V_IDLE;
static uint32_t g_vin_deadline = 0;
static uint8_t  g_vin_buf[32];
static uint16_t g_vin_expected = 0, g_vin_got = 0;
static uint8_t  g_vin_sn = 1;
static volatile bool g_vin_done = false;
static bool     g_vin_ok = false;
static char     g_vin_text[18];

static void vin_set_mode(twai_mode_t m) {
  twai_stop();
  twai_driver_uninstall();
  beginBusB(m);
}

static bool vin_tx(const uint8_t *d) {
  twai_message_t m = {};
  m.identifier = VIN_REQ_ID;
  m.ss = 1;                 // single shot: never retransmit
  m.data_length_code = 8;
  memcpy(m.data, d, 8);
  return twai_transmit(&m, pdMS_TO_TICKS(20)) == ESP_OK;
}

static void vin_finish(bool ok) {
  g_vin_ok = ok;
  g_vin_done = true;
  g_vin_state = V_IDLE;
  vin_set_mode(TWAI_MODE_LISTEN_ONLY);
}

void canbus_vin_request() { g_vin_req = true; }

void canbus_vin_tick() {
  const uint32_t now = millis();
  if (g_vin_state == V_IDLE) {
    if (!g_vin_req) return;
    g_vin_req = false;
    g_vin_state = V_SEND;
  }
  if (g_vin_state == V_SEND) {
    vin_set_mode(TWAI_MODE_NORMAL);
    const uint8_t req[8] = {0x02, 0x21, 0x81, 0, 0, 0, 0, 0};   // single frame, service 21, PID 81
    if (!vin_tx(req)) { vin_finish(false); return; }
    g_vin_deadline = now + VIN_TIMEOUT_MS;
    g_vin_state = V_WAIT_FF;
    return;
  }
  if ((int32_t)(now - g_vin_deadline) > 0) vin_finish(false);
}

void canbus_vin_feed(const LogRecord &r) {
  if (g_vin_state != V_WAIT_FF && g_vin_state != V_WAIT_CF) return;
  if (r.id != VIN_RSP_ID || (r.flags & FLAG_EXT)) return;
  const uint8_t pci = r.data[0] & 0xF0;
  if (g_vin_state == V_WAIT_FF) {
    if (pci == 0x00) {                       // single frame: negative response or too short
      vin_finish(false);
      return;
    }
    if (pci != 0x10) return;
    g_vin_expected = ((r.data[0] & 0x0F) << 8) | r.data[1];
    if (g_vin_expected < 19 || g_vin_expected > sizeof(g_vin_buf)) { vin_finish(false); return; }
    memcpy(g_vin_buf, r.data + 2, 6);
    g_vin_got = 6;
    g_vin_sn = 1;
    const uint8_t fc[8] = {0x30, 0x00, 0x00, 0, 0, 0, 0, 0};    // flow control: clear to send
    if (!vin_tx(fc)) { vin_finish(false); return; }
    g_vin_state = V_WAIT_CF;
    return;
  }
  if (pci != 0x20 || (r.data[0] & 0x0F) != (g_vin_sn & 0x0F)) return;
  const uint16_t take = min<uint16_t>(7, g_vin_expected - g_vin_got);
  memcpy(g_vin_buf + g_vin_got, r.data + 1, take);
  g_vin_got += take;
  g_vin_sn++;
  if (g_vin_got < g_vin_expected) return;

  // Reply = 0x61 0x81 + 17 ASCII VIN characters (+ padding).
  bool ok = g_vin_buf[0] == 0x61 && g_vin_buf[1] == 0x81;
  for (int i = 0; i < 17 && ok; i++) {
    const uint8_t c = g_vin_buf[2 + i];
    g_vin_text[i] = (char)c;
    if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z'))) ok = false;
  }
  g_vin_text[17] = 0;
  vin_finish(ok);
}

bool canbus_vin_take(char out[18], bool &ok) {
  if (!g_vin_done) return false;
  g_vin_done = false;
  ok = g_vin_ok;
  memcpy(out, g_vin_text, 18);
  if (!ok) out[0] = 0;
  return true;
}
