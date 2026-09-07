// See hearing.h. Borrows the I2S bus from the speaker for one short window.
#include "hearing.h"

#include <M5Unified.h>
#include <esp_heap_caps.h>

#include "chirp.h"

namespace {

// A window long enough to catch a bang and short enough that a chirp asked
// for during it is not noticeably late. 16 kHz x 48 ms = 768 samples.
constexpr uint32_t kRate = 16000;
constexpr size_t   kSamples = 768;
constexpr uint32_t kWindowMs = (kSamples * 1000) / kRate;
constexpr uint32_t kPeriodMs = 2000;      // how often a window is taken
constexpr uint32_t kReportMs = 2000;      // how often a line goes to the host
constexpr uint32_t kSettleMs = 40;        // let the codec wake before believing it
constexpr uint32_t kRecordTimeoutMs = 200;

// The learned floor: the quietest this room gets. Rises slowly, falls fast,
// so a fan switching on becomes "normal" within a few minutes but a quiet
// afternoon is recognised at once.
constexpr float kQuietRise = 0.02f;
constexpr float kQuietFall = 0.25f;

int16_t* s_buf = nullptr;
bool  s_available = false;
bool  s_failed = false;
bool  s_micOn = false;
uint32_t s_lastWindowMs = 0;
uint32_t s_lastReportMs = 0;
float s_quiet = -1.0f;
uint8_t s_rms = 0, s_peak = 0;

// 0..100 from a 16-bit amplitude, on a log scale: hearing is logarithmic and
// a linear reading spends its whole range on the loudest sounds in the room.
uint8_t toScale(float amplitude) {
  if (amplitude < 1.0f) return 0;
  // 20*log10(a/32768) is -90..0 dB; map -60..0 dB onto 0..100.
  float db = 20.0f * log10f(amplitude / 32768.0f);
  if (db < -60.0f) db = -60.0f;
  if (db > 0.0f) db = 0.0f;
  return (uint8_t)((db + 60.0f) * (100.0f / 60.0f) + 0.5f);
}

bool micOn() {
  if (s_micOn) return true;
  M5.Speaker.end();                       // the bus has one owner
  if (!M5.Mic.begin()) {
    M5.Speaker.begin();
    return false;
  }
  s_micOn = true;
  return true;
}

void micOff() {
  if (!s_micOn) return;
  M5.Mic.end();
  s_micOn = false;
  M5.Speaker.begin();                     // hand the voice back immediately
  M5.Speaker.setVolume(96);
}

}  // namespace

void hearingBegin() {
  s_buf = (int16_t*)heap_caps_malloc(kSamples * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!s_buf) s_buf = (int16_t*)malloc(kSamples * sizeof(int16_t));
  if (!s_buf) {
    Serial.println("[ear] no buffer; hearing off");
    s_failed = true;
    return;
  }
  Serial.println("[ear] ready (borrows the speaker's bus for 48 ms at a time)");
}

bool hearingAvailable() { return s_available; }
uint8_t hearingRms() { return s_rms; }
uint8_t hearingPeak() { return s_peak; }
uint8_t hearingQuiet() { return s_quiet < 0 ? 0 : (uint8_t)(s_quiet + 0.5f); }

void hearingUpdate(bool exploring, uint32_t nowMs) {
  if (s_failed || !s_buf) return;
  if (!exploring) {
    micOff();                             // never hold the bus outside explore
    return;
  }
  if (chirpPlaying()) return;             // buddy is talking; it cannot listen
  if (nowMs - s_lastWindowMs < kPeriodMs) return;
  s_lastWindowMs = nowMs;

  if (!micOn()) {
    Serial.println("[ear] mic would not start; hearing off for this boot");
    s_failed = true;
    return;
  }
  // The ES7210 does not produce sound the instant the bus is handed to it:
  // the first window after a begin() comes back as digital silence (bench
  // 2026-09-06, rms 0 every time). Give it a moment, then throw one window
  // away and keep the next.
  delay(kSettleMs);
  if (M5.Mic.record(s_buf, kSamples, kRate)) {
    uint32_t warm = 0;
    while (M5.Mic.isRecording() && warm < kRecordTimeoutMs) { delay(2); warm += 2; }
  }
  bool started = M5.Mic.record(s_buf, kSamples, kRate);
  if (!started) {
    micOff();
    Serial.println("[ear] record refused; hearing off for this boot");
    s_failed = true;
    return;
  }
  uint32_t waited = 0;
  while (M5.Mic.isRecording() && waited < kRecordTimeoutMs) {
    delay(2);
    waited += 2;
  }
  micOff();                               // the voice comes back before anything else
  if (waited >= kRecordTimeoutMs) {
    Serial.println("[ear] record timed out; hearing off for this boot");
    s_failed = true;
    return;
  }

  static bool logged = false;
  if (!logged) {
    logged = true;
    auto cfg = M5.Mic.config();
    Serial.printf("[ear] mic enabled=%d port=%d bck=%d ws=%d din=%d mck=%d mag=%d ch=%d rate=%u\n",
                  (int)M5.Mic.isEnabled(), (int)cfg.i2s_port, (int)cfg.pin_bck, (int)cfg.pin_ws,
                  (int)cfg.pin_data_in, (int)cfg.pin_mck, (int)cfg.magnification,
                  (int)cfg.input_channel, (unsigned)cfg.sample_rate);
    Serial.printf("[ear] first samples: %d %d %d %d %d %d %d %d\n",
                  s_buf[0], s_buf[1], s_buf[2], s_buf[3], s_buf[4], s_buf[5], s_buf[6], s_buf[7]);
  }

  double sum = 0.0;
  int32_t peak = 0;
  for (size_t i = 0; i < kSamples; ++i) {
    int32_t v = s_buf[i];
    sum += (double)v * v;
    int32_t a = v < 0 ? -v : v;
    if (a > peak) peak = a;
  }
  float rms = sqrtf((float)(sum / kSamples));
  s_rms = toScale(rms);
  s_peak = toScale((float)peak);
  s_available = true;

  if (s_quiet < 0.0f) s_quiet = s_rms;
  else if (s_rms < s_quiet) s_quiet += (s_rms - s_quiet) * kQuietFall;
  else s_quiet += (s_rms - s_quiet) * kQuietRise;

  if (nowMs - s_lastReportMs >= kReportMs) {
    s_lastReportMs = nowMs;
    Serial.printf("\n{\"sound\":{\"rms\":%u,\"peak\":%u,\"quiet\":%u}}\n",
                  (unsigned)s_rms, (unsigned)s_peak, (unsigned)hearingQuiet());
  }
  (void)kWindowMs;
  (void)kSettleMs;
}
