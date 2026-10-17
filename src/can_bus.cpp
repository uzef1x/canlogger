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

static bool beginBusB() {
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
      (gpio_num_t)CANB_TX_GPIO, (gpio_num_t)CANB_RX_GPIO, TWAI_MODE_LISTEN_ONLY);
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
