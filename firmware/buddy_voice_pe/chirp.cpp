#include "chirp.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <math.h>

#include "sb_config.h"
#include "sb_i2s.h"

namespace {

// Rendered at 16 kHz like the StackChan (sweeps reach 7 kHz), then each
// sample is written three times to the 48 kHz bus.
constexpr uint32_t RATE = 16000;
constexpr size_t MAX_SAMPLES = RATE * 2;  // 2 s per phrase
constexpr float F_MIN = 60.0f, F_MAX = 7000.0f;
constexpr size_t RAMP = RATE * 2 / 1000;  // 2 ms attack/release
constexpr float STEP_MS = 1.2f;

int16_t *buf = nullptr;
size_t len = 0;
uint32_t phase = 0;
float amp = 0.65f;

struct Req {
  ChirpKind kind;
  uint16_t hz;    // tone only
  uint16_t ms;    // tone only
  float level;    // tone only, 0..1 of full scale
};

QueueHandle_t queue = nullptr;
volatile bool enabled = true;
volatile uint8_t volume = 6;  // 0..10

// ---- synthesis, same recipes as the StackChan ----
void seg(float f0, float f1, uint32_t ms) {
  size_t n = (size_t)ms * RATE / 1000;
  if (len + n > MAX_SAMPLES) n = MAX_SAMPLES - len;
  if (n == 0) return;
  // Square wave at full scale is harsh on the Voice PE speaker; 0.3 of full
  // scale times the dial volume keeps the loudest setting below clipping.
  float scale = 32767.0f * 0.3f * amp * (volume / 10.0f);
  for (size_t i = 0; i < n; i++) {
    float f = f0 + (f1 - f0) * (float)i / (float)n;
    if (f < F_MIN) f = F_MIN;
    if (f > F_MAX) f = F_MAX;
    uint32_t inc = (uint32_t)(f * (4294967296.0 / RATE));
    float env = 1.0f;
    if (i < RAMP) env = (float)i / RAMP;
    if (n - i <= RAMP) env = fminf(env, (float)(n - i) / RAMP);
    float v = (phase & 0x80000000u) ? 1.0f : -1.0f;
    buf[len++] = (int16_t)(v * scale * env);
    phase += inc;
  }
}
void gap(uint32_t ms) {
  size_t n = (size_t)ms * RATE / 1000;
  if (len + n > MAX_SAMPLES) n = MAX_SAMPLES - len;
  for (size_t i = 0; i < n; i++) buf[len++] = 0;
}
void note(float f, uint32_t ms) { seg(f, f, ms); }
void phrase2(int k, int nUp, int nDown) {
  seg(k, k + 2.0f * nUp, (uint32_t)(nUp * STEP_MS));
  seg(k, k - 10.0f * nDown, (uint32_t)(nDown * STEP_MS));
}
void beeps(int count) {
  const int K = 2000;
  for (int i = 0; i < count; i++) {
    note(K + random(-1700, 2000), random(70, 170));
    gap(random(0, 30));
  }
}

void build(ChirpKind kind) {
  len = 0;
  phase = 0;
  amp = 0.65f;
  switch (kind) {
    case CHIRP_WAKE: {
      int k = random(900, 1400);
      seg(k, k + 1500, 220);
      gap(20);
      note(k + 1800, 60);
      break;
    }
    case CHIRP_ATTENTION: {
      phrase2(random(1200, 2000), random(200, 500), random(60, 150));
      gap(40);
      beeps(random(4, 7));
      break;
    }
    case CHIRP_HAPPY: {
      int base = random(1200, 1800), n = random(5, 9);
      for (int i = 0; i < n; i++) {
        note(base + i * 250, random(60, 90));
        gap(15);
      }
      break;
    }
    case CHIRP_LISTEN: {
      int k = random(800, 1200);
      seg(k, k * 1.8f, 180);
      break;
    }
    case CHIRP_OK: {
      int f = random(1400, 1800);
      note(f, 90);
      gap(30);
      note(f * 0.7f, 110);
      break;
    }
    case CHIRP_NO: {
      int k = random(900, 1200);
      seg(k, k * 0.5f, 250);
      break;
    }
    case CHIRP_CURIOUS: {
      int k = random(900, 1300);
      note(k, 90);
      gap(40);
      seg(k * 1.3f, k * 1.9f, 160);
      break;
    }
    case CHIRP_TICK: {
      amp = 0.5f;
      note(2400, 18);
      break;
    }
  }
}

// Diagnostic: a sine at 48 kHz straight to the bus, no 16 kHz step.
void writeTone(uint16_t hz, uint16_t ms, float level) {
  int16_t chunk[480];
  uint32_t total = (uint32_t)ms * 48;
  float ph = 0, inc = TWO_PI * hz / 48000.0f;
  for (uint32_t done = 0; done < total;) {
    uint32_t n = total - done > 480 ? 480 : total - done;
    for (uint32_t j = 0; j < n; j++) {
      chunk[j] = (int16_t)(sinf(ph) * 32767.0f * level);
      ph += inc;
      if (ph > TWO_PI) ph -= TWO_PI;
    }
    sbI2sSpeakerWriteMono(chunk, n);
    done += n;
  }
}

void writeUpsampled() {
  int16_t chunk[96 * 3];
  size_t i = 0;
  while (i < len) {
    size_t n = len - i > 96 ? 96 : len - i;
    for (size_t j = 0; j < n; j++) {
      int16_t s = buf[i + j];
      chunk[j * 3] = s;
      chunk[j * 3 + 1] = s;
      chunk[j * 3 + 2] = s;
    }
    sbI2sSpeakerWriteMono(chunk, n * 3);
    i += n;
  }
  // Flush the DMA ring with silence so the amplifier turns off on zeros,
  // not on the last sample of the phrase.
  static const int16_t silence[480] = {0};
  for (int k = 0; k < 6; k++) sbI2sSpeakerWriteMono(silence, 480);
}

void play(const Req &r) {
  if (r.kind == CHIRP_TONE) {
    writeTone(r.hz, r.ms, r.level);
  } else {
    build(r.kind);
    writeUpsampled();
  }
}

void task(void *) {
  for (;;) {
    Req r;
    if (xQueueReceive(queue, &r, portMAX_DELAY) != pdTRUE) continue;
    bool tone = r.kind == CHIRP_TONE;
    if (!tone && (!enabled || volume == 0)) continue;
    uint32_t e0, b0, e1, b1;
    sbI2sSpeakerStats(&e0, &b0);
    uint32_t t0 = millis();
    play(r);
    // Play what arrived meanwhile before the amplifier goes off.
    while (xQueueReceive(queue, &r, 0) == pdTRUE) {
      if (r.kind == CHIRP_TONE || enabled) play(r);
    }
    sbI2sSpeakerStats(&e1, &b1);
    Serial.printf("[chirp] played in %lu ms: %lu bytes to i2s, %lu write errors, amp=%d\n", millis() - t0,
                  (unsigned long)(b1 - b0), (unsigned long)(e1 - e0), digitalRead(SB_PIN_AMP_ENABLE));
  }
}

}  // namespace

void chirpBegin() {
  buf = (int16_t *)heap_caps_malloc(MAX_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!buf) buf = (int16_t *)malloc(MAX_SAMPLES * sizeof(int16_t));
  queue = xQueueCreate(4, sizeof(Req));
  // The amplifier stays on, as in ESPHome: switching it per phrase clicks.
  if (buf && queue) xTaskCreatePinnedToCore(task, "chirp", 4096, nullptr, 2, nullptr, 0);
  Serial.printf("[chirp] %s\n", (buf && queue) ? "ready" : "FAILED");
}

void chirpSetEnabled(bool on) { enabled = on; }

void chirpSetVolume(uint8_t level) { volume = level > 10 ? 10 : level; }

void chirpPlay(ChirpKind kind) {
  if (!queue || !enabled || volume == 0) return;
  Req r{kind, 0, 0, 0};
  xQueueSend(queue, &r, 0);
}

void chirpTone(uint16_t hz, uint16_t ms, float level) {
  if (!queue) return;
  Req r{CHIRP_TONE, hz, ms, level};
  xQueueSend(queue, &r, 0);
}

void chirpForceAmp(bool on) {
  digitalWrite(SB_PIN_AMP_ENABLE, on ? HIGH : LOW);
}
