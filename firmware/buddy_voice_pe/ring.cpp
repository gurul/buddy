#include "ring.h"

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <math.h>

#include "sb_config.h"

namespace {

constexpr uint8_t N = SB_LED_RING_COUNT;
Adafruit_NeoPixel pixels(N, SB_PIN_LED_RING, NEO_GRB + NEO_KHZ800);

RingLook look = LOOK_OFFLINE;
uint32_t lookAtMs = 0;
uint8_t levelShown = 0;
uint32_t levelUntil = 0;
bool muted = false;
uint32_t lastRender = 0;

struct Rgb {
  float r, g, b;
};

Rgb frame[N];

void fill(Rgb c, float k) {
  for (uint8_t i = 0; i < N; i++) frame[i] = {c.r * k, c.g * k, c.b * k};
}

// A bright head with a fading tail, `heads` evenly spaced.
void comet(Rgb c, uint32_t periodMs, uint8_t heads, uint32_t now) {
  float pos = (float)(now % periodMs) / periodMs * N;
  for (uint8_t i = 0; i < N; i++) {
    float best = 0;
    for (uint8_t h = 0; h < heads; h++) {
      float head = fmodf(pos + (float)h * N / heads, N);
      float d = fmodf(head - i + N, N);  // distance behind the head
      float k = d < 5 ? 1.0f - d / 5.0f : 0;
      if (k > best) best = k;
    }
    best = best * best;
    frame[i] = {c.r * best, c.g * best, c.b * best};
  }
}

float breathe(uint32_t periodMs, uint32_t now) {
  float p = (float)(now % periodMs) / periodMs;
  return 0.5f - 0.5f * cosf(p * TWO_PI);
}

void render(uint32_t now) {
  uint32_t age = now - lookAtMs;
  switch (look) {
    case LOOK_OFFLINE: {
      fill({0, 0, 0}, 0);
      uint8_t i = (now / 400) % N;
      frame[i] = {10, 10, 12};
      break;
    }
    case LOOK_IDLE:
      fill({60, 40, 20}, 0.08f + 0.17f * breathe(4000, now));
      break;
    case LOOK_WORKING:
      comet({0, 170, 150}, 1400, 1, now);
      break;
    case LOOK_THINKING:
      comet({150, 60, 255}, 1100, 2, now);
      break;
    case LOOK_LISTENING:
      fill({40, 110, 255}, 0.45f + 0.35f * breathe(2400, now));
      break;
    case LOOK_SPEAKING:
      fill({40, 110, 255}, 0.3f + 0.7f * breathe(600, now));
      break;
    case LOOK_WAITING:
      fill({255, 140, 0}, 0.15f + 0.85f * breathe(1500, now));
      break;
    case LOOK_DONE: {
      // One green sweep around the ring over 600 ms, then a fade.
      float lit = age < 600 ? (float)age / 600 * N : N;
      float fade = age < 600 ? 1.0f : fmaxf(0, 1.0f - (age - 600) / 900.0f);
      for (uint8_t i = 0; i < N; i++) frame[i] = i < lit ? Rgb{40 * fade, 220 * fade, 80 * fade} : Rgb{0, 0, 0};
      break;
    }
    case LOOK_ERROR:
      fill({200, 0, 0}, ((now / 150) % 2) ? 0.0f : 1.0f);
      break;
  }

  if (now < levelUntil) {
    uint8_t lit = (uint8_t)((levelShown * N + 5) / 10);
    for (uint8_t i = 0; i < N; i++) frame[i] = i < lit ? Rgb{120, 120, 120} : Rgb{0, 0, 0};
  }
  if (muted) {
    frame[0] = {180, 0, 0};
    frame[N - 1] = {180, 0, 0};
  }

  for (uint8_t i = 0; i < N; i++) {
    pixels.setPixelColor(i, pixels.Color((uint8_t)frame[i].r, (uint8_t)frame[i].g, (uint8_t)frame[i].b));
  }
  pixels.show();
}

}  // namespace

void ringBegin() {
  pinMode(SB_PIN_LED_POWER, OUTPUT);
  digitalWrite(SB_PIN_LED_POWER, HIGH);
  delay(5);
  pixels.begin();
  pixels.setBrightness(80);
  pixels.clear();
  pixels.show();
}

void ringSet(RingLook l) {
  if (l == look) return;
  look = l;
  lookAtMs = millis();
}

void ringShowLevel(uint8_t level) {
  levelShown = level;
  levelUntil = millis() + 1500;
}

void ringSetMuted(bool m) { muted = m; }

void ringUpdate() {
  uint32_t now = millis();
  if (now - lastRender < 20) return;
  lastRender = now;
  render(now);
}
