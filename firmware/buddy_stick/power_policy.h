#pragma once
// What the stick keeps powered, decided from timestamps alone (plain C++, host-tested in test/host_test.cpp).
//
// The owner, 2026-09-30: "optimize the battery life of the stick". Measured sources (docs/stick-link.md, Battery):
// the ESP32-S3 idles at 33 mA at 240 MHz and 22 mA at 80 MHz; the AW8737 amplifier draws 10-15 mA while enabled
// and the ES8311 codec about 8 mA; the stock Arduino core cannot light-sleep while BLE is connected. So the stick
// keeps each part on only while it is used: the screen while it is looked at, the audio path while there is sound,
// a fast BLE interval while a turn is live, and powers off (about 14 uA) once it has lain still for a long time.
#include <stdint.h>

struct PowerInputs {
  uint32_t now;
  uint32_t lastInput;        // a button
  uint32_t lastMotion;       // the IMU saw the stick move
  uint32_t lastActive;       // a press, a reply, a message from the phone
  uint32_t lastSound;        // the speaker last played anything
  bool live;                 // a turn is on: recording, thinking or speaking
  bool soundPending;         // reply audio is buffered or playing
  bool pairing;              // a code is on screen
  bool onBattery;            // no USB power
};

struct PowerPlan {
  bool displayOn;
  bool audioOn;
  bool linkFast;
  bool powerOff;
};

class PowerPolicy {
 public:
  static constexpr uint32_t DISPLAY_MS = 20000;            // screen off after 20 s with nothing new on it
  static constexpr uint32_t AUDIO_MS = 4000;               // codec and amplifier off 4 s after the last sound
  static constexpr uint32_t LINK_MS = 20000;               // the fast BLE interval for 20 s after a turn
  static constexpr uint32_t OFF_MS = 30UL * 60 * 1000;     // off after 30 min lying still, on battery

  static bool within(uint32_t now, uint32_t since, uint32_t span) { return uint32_t(now - since) < span; }

  static PowerPlan plan(const PowerInputs& in) {
    PowerPlan p{};
    bool recent = within(in.now, in.lastInput, DISPLAY_MS) || within(in.now, in.lastMotion, DISPLAY_MS) ||
                  within(in.now, in.lastActive, DISPLAY_MS);
    p.displayOn = in.live || in.pairing || recent;
    p.audioOn = in.soundPending || in.live || within(in.now, in.lastSound, AUDIO_MS);
    p.linkFast = in.live || in.pairing || within(in.now, in.lastActive, LINK_MS) ||
                 within(in.now, in.lastInput, LINK_MS) || within(in.now, in.lastMotion, LINK_MS);
    bool still = !within(in.now, in.lastMotion, OFF_MS) && !within(in.now, in.lastInput, OFF_MS) &&
                 !within(in.now, in.lastActive, OFF_MS);
    p.powerOff = in.onBattery && still && !in.live && !in.soundPending && !in.pairing;
    return p;
  }
};
