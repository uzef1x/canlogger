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
// Tries read-only VIN requests in turn until one returns 17 valid characters.
// None of them is confirmed for the LEAF; every try and reply is in the log.
//   A  standard OBD-II mode 09 PID 02, functional request 0x7DF, any 0x7E8-0x7EF reply
//   B  UDS ReadDataByIdentifier F190 to 0x797 (charger/PDM), reply 0x79A
//   C  Nissan service 21 PID 81 to 0x797, reply 0x79A (the OVMS method; refused with
//      NRC 0x80 on the first car tried)
struct VinCand {
  char     tag;
  uint32_t tx_id;
  uint8_t  req[8];
  uint32_t rsp_lo, rsp_hi;
  uint8_t  hdr[3];       // expected positive-reply header
  uint8_t  hdr_len;
  bool     opt_count;    // mode 09 may carry a 1-byte item count after the header
};
static const VinCand CANDS[] = {
  {'A', 0x7DF, {0x02, 0x09, 0x02, 0, 0, 0, 0, 0}, 0x7E8, 0x7EF, {0x49, 0x02, 0},    2, true},
  {'B', 0x797, {0x03, 0x22, 0xF1, 0x90, 0, 0, 0, 0}, 0x79A, 0x79A, {0x62, 0xF1, 0x90}, 3, false},
  {'C', 0x797, {0x02, 0x21, 0x81, 0, 0, 0, 0, 0}, 0x79A, 0x79A, {0x61, 0x81, 0},    2, false},
};
static constexpr uint8_t N_CANDS = sizeof(CANDS) / sizeof(CANDS[0]);
static constexpr uint32_t VIN_TRY_MS = 1000;

enum VinState : uint8_t { V_IDLE, V_SEND, V_WAIT_FF, V_WAIT_CF };
static volatile bool g_vin_req = false;
static VinState g_vin_state = V_IDLE;
static uint8_t  g_vin_cand = 0;
static uint32_t g_vin_deadline = 0;
static uint8_t  g_vin_buf[32];
static uint16_t g_vin_expected = 0, g_vin_got = 0;
static uint8_t  g_vin_sn = 1;
static uint32_t g_vin_fc_id = 0;
static volatile bool g_vin_done = false;
static bool     g_vin_ok = false;
static char     g_vin_text[18];
static char     g_vin_note[48];

static void vin_set_mode(twai_mode_t m) {
  twai_stop();
  twai_driver_uninstall();
  beginBusB(m);
}

static bool vin_tx(uint32_t id, const uint8_t *d) {
  twai_message_t m = {};
  m.identifier = id;
  m.ss = 1;                 // single shot: never retransmit
  m.data_length_code = 8;
  memcpy(m.data, d, 8);
  return twai_transmit(&m, pdMS_TO_TICKS(20)) == ESP_OK;
}

static void vin_note(const char *what) {
  size_t n = strlen(g_vin_note);
  snprintf(g_vin_note + n, sizeof(g_vin_note) - n, "%s%c-%s", n ? "_" : "",
           CANDS[g_vin_cand].tag, what);
}

static void vin_finish(bool ok) {
  g_vin_ok = ok;
  g_vin_done = true;
  g_vin_state = V_IDLE;
  vin_set_mode(TWAI_MODE_LISTEN_ONLY);
}

// This candidate failed: note why and move on, or finish if none are left.
static void vin_next(const char *why) {
  vin_note(why);
  if (++g_vin_cand >= N_CANDS) { vin_finish(false); return; }
  g_vin_state = V_SEND;
}

void canbus_vin_request() { g_vin_req = true; }

void canbus_vin_tick() {
  const uint32_t now = millis();
  if (g_vin_state == V_IDLE) {
    if (!g_vin_req) return;
    g_vin_req = false;
    g_vin_cand = 0;
    g_vin_note[0] = 0;
    vin_set_mode(TWAI_MODE_NORMAL);
    g_vin_state = V_SEND;
  }
  if (g_vin_state == V_SEND) {
    const VinCand &c = CANDS[g_vin_cand];
    if (!vin_tx(c.tx_id, c.req)) { vin_next("txfail"); return; }
    g_vin_deadline = now + VIN_TRY_MS;
    g_vin_state = V_WAIT_FF;
    return;
  }
  if ((int32_t)(now - g_vin_deadline) > 0) vin_next("timeout");
}

void canbus_vin_feed(const LogRecord &r) {
  if (g_vin_state != V_WAIT_FF && g_vin_state != V_WAIT_CF) return;
  const VinCand &c = CANDS[g_vin_cand];
  if (r.id < c.rsp_lo || r.id > c.rsp_hi || (r.flags & FLAG_EXT)) return;
  const uint8_t pci = r.data[0] & 0xF0;

  if (g_vin_state == V_WAIT_FF) {
    if (pci == 0x00) {                          // single frame
      if (r.data[1] == 0x7F) {
        if (r.data[3] == 0x78) { g_vin_deadline = millis() + 2000; return; }   // response pending
        char w[8];
        snprintf(w, sizeof(w), "nrc%02X", r.data[3]);
        vin_next(w);
      } else {
        vin_next("short");
      }
      return;
    }
    if (pci != 0x10) return;
    g_vin_expected = ((r.data[0] & 0x0F) << 8) | r.data[1];
    if (g_vin_expected < 19 || g_vin_expected > sizeof(g_vin_buf)) { vin_next("badlen"); return; }
    memcpy(g_vin_buf, r.data + 2, 6);
    g_vin_got = 6;
    g_vin_sn = 1;
    // Flow control goes to the responder's physical request ID: the request ID for a
    // physical request, reply-ID minus 8 for the functional 0x7DF request.
    g_vin_fc_id = (c.tx_id == 0x7DF) ? r.id - 8 : c.tx_id;
    const uint8_t fc[8] = {0x30, 0x00, 0x00, 0, 0, 0, 0, 0};    // clear to send
    if (!vin_tx(g_vin_fc_id, fc)) { vin_next("txfail"); return; }
    g_vin_deadline = millis() + VIN_TRY_MS;
    g_vin_state = V_WAIT_CF;
    return;
  }

  if (pci != 0x20 || (r.data[0] & 0x0F) != (g_vin_sn & 0x0F)) return;
  const uint16_t take = min<uint16_t>(7, g_vin_expected - g_vin_got);
  memcpy(g_vin_buf + g_vin_got, r.data + 1, take);
  g_vin_got += take;
  g_vin_sn++;
  if (g_vin_got < g_vin_expected) return;

  bool ok = memcmp(g_vin_buf, c.hdr, c.hdr_len) == 0;
  uint8_t off = c.hdr_len;
  if (ok && c.opt_count && g_vin_buf[off] == 0x01) off++;
  if (ok && off + 17 > g_vin_expected) ok = false;
  char text[18] = {0};
  for (int i = 0; i < 17 && ok; i++) {
    const uint8_t ch = g_vin_buf[off + i];
    text[i] = (char)ch;
    if (!((ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'Z'))) ok = false;
  }
  if (!ok) { vin_next("bad"); return; }
  memcpy(g_vin_text, text, 18);
  vin_note("ok");
  vin_finish(true);
}

bool canbus_vin_take(char out[18], bool &ok, char note[48]) {
  if (!g_vin_done) return false;
  g_vin_done = false;
  ok = g_vin_ok;
  memcpy(out, g_vin_text, 18);
  memcpy(note, g_vin_note, 48);
  if (!ok) out[0] = 0;
  return true;
}
