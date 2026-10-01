// buddy on a stick: an M5StickS3 as a push-to-talk button for buddy, carried anywhere.
//
// The owner asked on 2026-09-30 for the stick to "connect to my iphone which would communicate to the daemon so i
// can talk to it from anywhere", with buddy's voice on the stick's own speaker. The stick is a Bluetooth LE
// peripheral; the Buddy Link app on the iPhone (ios/BuddyLink) relays each press to the daemon's call endpoint
// (/api/call, bridge/src/cc_buddy_bridge/phone_call.py) and relays the reply back. docs/stick-link.md has the
// whole path; tools/stick_link/adpcm.py is the reference for the wire.
//
// * Hold the front button (A) to talk, let go to send. A press while buddy talks stops it (the daemon's rule).
// * Buddy's reply plays on the stick. A quiet tick while buddy thinks, until the first sound.
// * Side button (B): a click cycles the volume; held three seconds it forgets the paired phone.
// * Held B at power-on: the self-test. A records, release plays it back, no phone involved (from m5-atom-puck).
// * Pairing shows a six-digit code here that the phone asks for, so nobody else nearby can pair with the mic.
//
// Mic and speaker share the codec's I2S pins, so the stick is half duplex: the speaker stops for a press.
#include <M5Unified.h>
#include <ArduinoJson.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLESecurity.h>
#include <BLE2902.h>
#include <host/ble_store.h>

#include "link_codec.h"
#include "pcm_ring.h"
#include "power_policy.h"

#ifndef BUDDY_GIT_SHA
#define BUDDY_GIT_SHA "dev"
#endif

// The link's GATT service. Keep in step with ios/BuddyLink/BuddyLink/StickLink.swift and tools/stick_link/probe.py.
static const char* SERVICE_UUID = "6a5ad52d-ec26-4b36-9b2d-0e4ef9d2f2bc";
static const char* UP_UUID = "19a1cbb3-15c5-4596-bcae-65985cfe7226";     // stick → phone: notify
static const char* DOWN_UUID = "dfc25597-3956-43b0-8c38-a7e5dc928505";   // phone → stick: write, no response

static constexpr uint32_t RATE = 24000;                // the call's rate end to end (phone_call.SAMPLE_RATE)
static constexpr size_t MIC_CHUNK = 240;               // 10 ms per mic job
static constexpr size_t MIC_JOBS = 4;                  // M5.Mic queues two; four buffers leave slack
static constexpr size_t MAX_PAYLOAD = 244;             // one value, whatever the MTU grows to
static constexpr size_t RING_SAMPLES = RATE * 90;      // 90 s of reply in PSRAM (4.3 MB)
static constexpr size_t PLAY_CHUNK = 1200;             // 50 ms per speaker job
static constexpr uint8_t REPLY_CH = 0, TONE_CH = 1;
static constexpr uint32_t PING_EVERY_MS = 1000;        // keeps the phone's app awake while a turn is live
static constexpr uint32_t AWAKE_AFTER_MS = 20000;      // and this long after the last one
// Battery (power_policy.h; sources in docs/stick-link.md, Battery). The CPU runs at 80 MHz: BLE needs no more
// (its lock holds APB at 80 MHz), I2S audio runs off its own 160 MHz PLL, and idle drops from 33 mA to 22 mA.
static constexpr uint32_t CPU_MHZ = 80;
// BLE timing, in Apple's rules (Accessory Design Guidelines R31, 58.6): fast 15-30 ms for a turn; idle 150-180 ms
// with 4 events of latency (900 ms worst case, under 6 s; supervision 6 s, over 3 x 900 ms).
static constexpr uint16_t FAST_MIN = 12, FAST_MAX = 24, FAST_LAT = 0;         // 1.25 ms units
static constexpr uint16_t IDLE_MIN = 120, IDLE_MAX = 144, IDLE_LAT = 4;
static constexpr uint16_t LINK_TIMEOUT = 600;                                // 10 ms units: 6 s
// Advertising while no phone is connected: 417.5 ms, one of Apple's listed intervals (58.5); 0.625 ms units.
static constexpr uint16_t ADV_INTERVAL = 668;
static constexpr float MOTION_G = 0.08f;               // what counts as "moved" for the screen and staying on
// Picking the stick up says a press is coming: a "wake" lets the phone open the call before it, so the first press
// is transcribed live (measured 2026-09-30: 1.43 s by upload on a call opened by the press, 0.94 s live on an open
// one). A change in acceleration over this much, at most once per WAKE_EVERY_MS.
static constexpr float WAKE_G = 0.12f;
static constexpr uint32_t WAKE_EVERY_MS = 20000;
static const uint8_t VOLUMES[] = {90, 150, 210, 255};
static constexpr uint8_t DAC_GAIN = 0xBF + 24;           // ES8311 DAC volume: +12 dB over M5Unified's 0 dB

enum class Ui : uint8_t { NoPhone, Pairing, Ready, Listening, Thinking, Speaking, Offline, SelfTest };

// ---- shared with the BLE host task (guarded by `lock`) ----------------------------------------------------
static SemaphoreHandle_t lock;
static int16_t* ringStore;
static PcmRing* ring;
static PlayGate gate;
static stick::GapCounter downGaps;
static uint32_t samplesPlayed = 0;
static volatile bool connected = false, secured = false, helloWanted = false;
static volatile uint16_t payload = 20;                 // MTU - 3 until the phone raises it
static volatile uint32_t passkey = 0;
static String inbox[8];                                // JSON from the phone, handled in loop()
static uint8_t inboxHead = 0, inboxCount = 0;

// ---- loop() only -----------------------------------------------------------------------------------------
static BLECharacteristic* up;
static BLEServer* server;
static volatile uint16_t connHandle = 0xFFFF;
static Ui ui = Ui::NoPhone;
static bool uiDirty = true, selfTest = false, recording = false, speakerOn = false;
static String heardText, noteText;
static uint8_t volumeIdx = 2;
static int16_t micBuf[MIC_JOBS][MIC_CHUNK];
static size_t micQueued = 0, micDone = 0;              // jobs handed to M5.Mic / jobs consumed
static int16_t frameBuf[2 * (MAX_PAYLOAD - stick::HEADER)];
static size_t frameFill = 0;
static stick::State upState;
static uint8_t upSeq = 0;
static int32_t pressPeak = 0;
// The codec's start-up pop: measured 2026-09-30, the first 100 ms of a press peaked at full scale (12 samples
// clipped) and the rest never did. The owner starts talking after the press tone, so 60 ms is dropped.
static constexpr uint32_t POP_SAMPLES = RATE * 60 / 1000;
static uint32_t popLeft = 0;
static uint32_t pressSamples = 0, lastPing = 0, lastActive = 0, lastInput = 0;
static uint32_t lastMotion = 0, lastSound = 0;
static bool displayOn = true, linkFast = true;
static int16_t* playBuf[3];
static uint8_t playNext = 0;
static WaitTick waitTick;
static int16_t* selfTestPcm;                           // self-test: 10 s in PSRAM
static size_t selfTestLen = 0;

static void markDirty() { uiDirty = true; }
static void setUi(Ui next) { if (ui != next) { ui = next; markDirty(); } }

// ---- sending --------------------------------------------------------------------------------------------
static void sendValue(const uint8_t* data, size_t len) {
  if (!connected || !secured || !up) return;
  up->setValue(const_cast<uint8_t*>(data), len);
  up->notify();
}

static void sendJson(JsonDocument& doc) {
  uint8_t buf[MAX_PAYLOAD];
  buf[0] = stick::KIND_JSON;
  size_t n = serializeJson(doc, (char*)buf + 1, sizeof(buf) - 1);
  if (n && n + 1 <= payload) sendValue(buf, n + 1);
  else Serial.printf("[link] message of %u bytes does not fit %u\n", unsigned(n + 1), unsigned(payload));
}

static void sendEvent(const char* t) {
  JsonDocument doc;
  doc["t"] = t;
  sendJson(doc);
}

static void sendHello() {
  JsonDocument doc;
  doc["t"] = "hello";
  doc["fw"] = BUDDY_GIT_SHA;
  doc["rate"] = RATE;
  doc["batt"] = M5.Power.getBatteryLevel();
  sendJson(doc);
}

static void sendStat() {
  JsonDocument doc;
  doc["t"] = "stat";
  xSemaphoreTake(lock, portMAX_DELAY);
  doc["played"] = samplesPlayed;
  doc["buffered"] = ring->size();
  doc["lost"] = downGaps.lost;
  xSemaphoreGive(lock);
  doc["payload"] = payload;
  sendJson(doc);
}

// ---- BLE callbacks (host task) -------------------------------------------------------------------------
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*, ble_gap_conn_desc* desc) override {
    connHandle = desc->conn_handle;
    linkFast = true;                                   // a fresh connection starts at the phone's own interval
    connected = true;
    Serial.println("[link] phone connected");
  }
  void onDisconnect(BLEServer*, ble_gap_conn_desc*) override {
    connHandle = 0xFFFF;
    connected = false;
    secured = false;
    payload = 20;
    Serial.println("[link] phone disconnected");
  }
  void onMtuChanged(BLEServer*, ble_gap_conn_desc*, uint16_t mtu) override {
    size_t p = mtu > 3 ? mtu - 3 : 20;
    payload = p > MAX_PAYLOAD ? MAX_PAYLOAD : p;
    Serial.printf("[link] mtu %u, values up to %u bytes\n", unsigned(mtu), unsigned(payload));
  }
  void onConnParamsUpdate(uint16_t, uint16_t interval, uint16_t latency, uint16_t timeout, uint8_t status) override {
    Serial.printf("[pwr] link interval %u.%02u ms, latency %u, timeout %u ms, status %u\n", unsigned(interval * 5 / 4),
                  unsigned(interval * 125 % 100), unsigned(latency), unsigned(timeout * 10), unsigned(status));
  }
};

class SecurityCallbacks : public BLESecurityCallbacks {
  void onPassKeyNotify(uint32_t key) override {
    passkey = key;
    Serial.println("[link] pairing: code on screen");
  }
  bool onConfirmPIN(uint32_t) override { return false; }
  bool onSecurityRequest() override { return true; }
  void onAuthenticationComplete(ble_gap_conn_desc* desc) override {
    secured = desc->sec_state.encrypted && desc->sec_state.authenticated;
    passkey = 0;
    Serial.printf("[link] pairing %s\n", secured ? "done" : "refused");
  }
};

class DownCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    if (!secured) return;                              // nothing from a phone that has not paired
    const uint8_t* data = c->getData();
    size_t len = c->getLength();
    if (!len) return;
    if (data[0] == stick::KIND_AUDIO) {
      int16_t pcm[2 * MAX_PAYLOAD];
      uint8_t seq;
      int n = stick::decodeFrame(data, len, pcm, sizeof pcm / sizeof pcm[0], &seq);
      if (n <= 0) return;
      xSemaphoreTake(lock, portMAX_DELAY);
      downGaps.see(seq);
      ring->push(pcm, size_t(n));
      gate.arrived(millis());
      xSemaphoreGive(lock);
    } else if (data[0] == stick::KIND_JSON) {
      String text;
      text.reserve(len);
      for (size_t i = 1; i < len; ++i) text += char(data[i]);
      xSemaphoreTake(lock, portMAX_DELAY);
      if (text.indexOf("\"flush\"") >= 0) {            // at once, so no queued audio outlives it
        ring->clear();
        gate.reset();
      }
      if (inboxCount < 8) {
        inbox[(inboxHead + inboxCount) % 8] = text;
        ++inboxCount;
      }
      xSemaphoreGive(lock);
    }
  }
};

// ---- audio ------------------------------------------------------------------------------------------------
// The ES8311 off, per M5Unified's own microphone-disable sequence (analog off, ADC/DAC modulator off, CSM off):
// M5.Speaker.end() only turns the amplifier off and leaves the codec at about 8 mA. Speaker.begin() rewrites the
// codec's whole start-up sequence, so speakerUp() brings it back.
static void codecOff() {
  M5.In_I2C.writeRegister8(0x18, 0x0D, 0xFC, 100000);
  M5.In_I2C.writeRegister8(0x18, 0x0E, 0x6A, 100000);
  M5.In_I2C.writeRegister8(0x18, 0x00, 0x00, 100000);
}

static void speakerDown() {
  if (!speakerOn) return;
  M5.Speaker.end();
  codecOff();
  speakerOn = false;
  Serial.println("[pwr] audio off");
}

static void speakerUp() {
  if (speakerOn) return;
  M5.Mic.end();
  speakerOn = M5.Speaker.begin();
  M5.Speaker.setVolume(VOLUMES[volumeIdx]);
  lastSound = millis();
  // The owner, 2026-09-30: "I can hear the sound but it's really quiet". M5Unified leaves the ES8311 DAC at 0 dB
  // (reg 0x32 = 0xBF; 0.5 dB a step, 0xFF = +32 dB). +12 dB is about four times louder and still leaves speech
  // its headroom; more than that clips a loud reply on a 1 W speaker.
  if (speakerOn) M5.In_I2C.writeRegister8(0x18, 0x32, DAC_GAIN, 100000);
  Serial.printf("[spk] begin=%d volume=%u\n", int(speakerOn), unsigned(VOLUMES[volumeIdx]));
  static bool dumped = false;
  if (!dumped) {   // once: what the amplifier switch (M5PM1 GPIO3) and the codec's output path actually hold
    dumped = true;
    auto rd = [](uint8_t addr, uint8_t reg) { return int(M5.In_I2C.readRegister8(addr, reg, 100000)); };
    Serial.printf("[spk] pm1 0x10=%02x 0x11=%02x 0x13=%02x 0x16=%02x | es8311 00=%02x 0d=%02x 12=%02x 13=%02x 32=%02x 37=%02x\n",
                  rd(0x6e, 0x10), rd(0x6e, 0x11), rd(0x6e, 0x13), rd(0x6e, 0x16),
                  rd(0x18, 0x00), rd(0x18, 0x0d), rd(0x18, 0x12), rd(0x18, 0x13), rd(0x18, 0x32), rd(0x18, 0x37));
  }
}

// The owner, 2026-09-30: "transcription is terrible". Presses talked into up close peaked at full scale (32752 of
// 32767) at M5Unified's default x16 digital gain, so loud syllables clipped; x8 is 6 dB of headroom, and speech
// still sat around -20 dBFS on the bench, plenty for transcription.
static constexpr uint8_t MIC_GAIN = 8;

static void micUp() {
  M5.Speaker.stop();
  M5.Speaker.end();
  speakerOn = false;
  auto cfg = M5.Mic.config();
  cfg.magnification = MIC_GAIN;
  M5.Mic.config(cfg);
  M5.Mic.begin();
}

static void beep(uint16_t hz, uint32_t ms) {
  if (recording) return;                               // the speaker is down for a press
  speakerUp();
  if (!speakerOn) return;
  lastSound = millis();
  M5.Speaker.setChannelVolume(TONE_CH, 120);
  M5.Speaker.tone(hz, ms, TONE_CH);
}

static void stopReply() {
  xSemaphoreTake(lock, portMAX_DELAY);
  ring->clear();
  gate.reset();
  xSemaphoreGive(lock);
  if (speakerOn) M5.Speaker.stop(REPLY_CH);
}

static void flushFrame(bool final) {
  if (final && frameFill % 2) {                        // the codec takes pairs: repeat the last sample
    frameBuf[frameFill] = frameBuf[frameFill - 1];
    ++frameFill;
  }
  if (!frameFill) return;
  uint8_t out[MAX_PAYLOAD];
  size_t len = stick::encodeFrame(upState, upSeq, frameBuf, frameFill, out);
  sendValue(out, len);
  frameFill = 0;
}

static void consumeMic(const int16_t* pcm, size_t n) {
  if (popLeft) {
    size_t skip = std::min<size_t>(n, popLeft);
    popLeft -= skip;
    pcm += skip;
    n -= skip;
    if (!n) return;
  }
  for (size_t i = 0; i < n; ++i) {
    int32_t a = pcm[i] < 0 ? -int32_t(pcm[i]) : pcm[i];
    if (a > pressPeak) pressPeak = a;
  }
  pressSamples += n;
  if (selfTest) {
    size_t take = std::min(n, size_t(RATE) * 10 - selfTestLen);
    memcpy(selfTestPcm + selfTestLen, pcm, take * sizeof(int16_t));
    selfTestLen += take;
    return;
  }
  size_t spf = stick::samplesPerFrame(payload);
  for (size_t i = 0; i < n; ++i) {
    frameBuf[frameFill++] = pcm[i];
    if (frameFill >= spf) flushFrame(false);
  }
}

static void startPress() {
  stopReply();
  waitTick.stop();
  beep(880, 40);
  delay(60);                                           // the press tone, before the speaker goes away
  micUp();
  recording = true;
  micQueued = micDone = 0;
  frameFill = 0;
  upState = stick::State();
  pressPeak = 0;
  pressSamples = 0;
  popLeft = POP_SAMPLES;
  selfTestLen = 0;
  if (!selfTest) sendEvent("talk");
  setUi(selfTest ? Ui::SelfTest : Ui::Listening);
}

static void pumpMic() {
  // record() blocks while both of M5.Mic's slots are busy; when it returns, the job two back is complete.
  if (M5.Mic.record(micBuf[micQueued % MIC_JOBS], MIC_CHUNK, RATE)) ++micQueued;
  while (micQueued - micDone > 2) {
    consumeMic(micBuf[micDone % MIC_JOBS], MIC_CHUNK);
    ++micDone;
  }
}

static void endPress() {
  while (M5.Mic.isRecording()) delay(1);
  while (micDone < micQueued) {
    consumeMic(micBuf[micDone % MIC_JOBS], MIC_CHUNK);
    ++micDone;
  }
  recording = false;
  speakerUp();
  beep(660, 40);
  Serial.printf("[mic] %u ms, peak %d (%.1f dBFS)\n", unsigned(pressSamples * 1000 / RATE), int(pressPeak),
                pressPeak ? 20.0 * log10(pressPeak / 32767.0) : -120.0);
  if (selfTest) {
    M5.Speaker.playRaw(selfTestPcm, selfTestLen, RATE, false, 1, REPLY_CH);
    setUi(Ui::SelfTest);
    return;
  }
  flushFrame(true);
  sendEvent("done");
  waitTick.start(millis());
  lastActive = millis();
  setUi(Ui::Thinking);
}

static void pumpSpeaker() {
  if (recording) return;
  if (waitTick.due(millis())) beep(520, 18);
  if (!speakerOn) {
    xSemaphoreTake(lock, portMAX_DELAY);
    bool waiting = ring->size() > 0;
    xSemaphoreGive(lock);
    if (!waiting) return;
    speakerUp();                                       // reply audio arrived while the path was off
    if (!speakerOn) return;
  }
  if (M5.Speaker.isPlaying(REPLY_CH) >= 2) return;
  xSemaphoreTake(lock, portMAX_DELAY);
  size_t buffered = ring->size();
  bool go = gate.mayPlay(buffered, millis());
  size_t n = 0;
  if (go) n = ring->pop(playBuf[playNext], PLAY_CHUNK);
  else if (gate.playing() && !buffered && !M5.Speaker.isPlaying(REPLY_CH)) gate.underrun();
  xSemaphoreGive(lock);
  if (!n) return;
  waitTick.stop();
  M5.Speaker.setChannelVolume(REPLY_CH, 255);
  bool queued = M5.Speaker.playRaw(playBuf[playNext], n, RATE, false, 1, REPLY_CH);
  lastSound = millis();
  static uint32_t lastSpkLog = 0;
  if (!queued || uint32_t(millis() - lastSpkLog) > 1000) {
    lastSpkLog = millis();
    int32_t peak = 0;
    for (size_t i = 0; i < n; ++i) peak = std::max<int32_t>(peak, abs(int32_t(playBuf[playNext][i])));
    Serial.printf("[spk] playRaw %u samples queued=%d peak=%d playing=%d buffered=%u\n", unsigned(n), int(queued),
                  int(peak), int(M5.Speaker.isPlaying(REPLY_CH)), unsigned(ring->size()));
  }
  playNext = (playNext + 1) % 3;
  xSemaphoreTake(lock, portMAX_DELAY);
  samplesPlayed += n;
  xSemaphoreGive(lock);
  lastActive = millis();
}

// ---- messages from the phone ------------------------------------------------------------------------------
static String ascii(const char* s, size_t cap) {
  String out;
  for (; *s && out.length() < cap; ++s) out += (uint8_t(*s) >= 32 && uint8_t(*s) < 127) ? *s : '?';
  return out;
}

static void handle(const String& text) {
  JsonDocument doc;
  if (deserializeJson(doc, text)) return;
  const char* t = doc["t"] | "";
  lastActive = millis();
  if (!strcmp(t, "hi")) {
    helloWanted = true;
  } else if (!strcmp(t, "state")) {
    const char* s = doc["s"] | "";
    noteText = ascii(doc["note"] | "", 120);
    if (!strcmp(s, "thinking")) setUi(Ui::Thinking);
    else if (!strcmp(s, "speaking")) setUi(Ui::Speaking);
    else if (!strcmp(s, "offline")) { waitTick.stop(); setUi(Ui::Offline); }
    else if (!strcmp(s, "listening") || !strcmp(s, "ready")) {
      if (noteText.length()) waitTick.stop();
      if (ui != Ui::Listening) setUi(Ui::Ready);
    }
    markDirty();
  } else if (!strcmp(t, "heard")) {
    heardText = ascii(doc["text"] | "", 160);
    markDirty();
  } else if (!strcmp(t, "flush")) {
    if (speakerOn) M5.Speaker.stop(REPLY_CH);
  } else if (!strcmp(t, "stat")) {
    sendStat();
  } else if (!strcmp(t, "end")) {
    waitTick.stop();
    setUi(Ui::Ready);
  }
}

// ---- screen -----------------------------------------------------------------------------------------------
static void draw() {
  auto& d = M5.Display;
  d.startWrite();
  d.fillScreen(TFT_BLACK);
  d.setTextDatum(top_left);
  d.setFont(&fonts::Font2);
  d.setTextColor(connected ? TFT_GREENYELLOW : TFT_DARKGREY);
  d.drawString(connected ? (secured ? "phone" : "phone (pairing)") : "no phone", 6, 4);
  d.setTextColor(TFT_LIGHTGREY);
  d.setTextDatum(top_right);
  d.drawString(String(M5.Power.getBatteryLevel()) + "%  vol " + String(volumeIdx + 1), d.width() - 6, 4);

  const char* word = "";
  uint16_t color = TFT_WHITE;
  switch (ui) {
    case Ui::NoPhone: word = "open Buddy Link"; color = TFT_DARKGREY; break;
    case Ui::Pairing: word = "pair code"; color = TFT_CYAN; break;
    case Ui::Ready: word = "hold to talk"; color = TFT_WHITE; break;
    case Ui::Listening: word = "listening"; color = TFT_RED; break;
    case Ui::Thinking: word = "thinking"; color = TFT_ORANGE; break;
    case Ui::Speaking: word = "speaking"; color = TFT_SKYBLUE; break;
    case Ui::Offline: word = "buddy offline"; color = TFT_MAGENTA; break;
    case Ui::SelfTest: word = recording ? "self-test: rec" : "self-test"; color = TFT_YELLOW; break;
  }
  d.setTextDatum(middle_center);
  d.setTextColor(color);
  if (ui == Ui::Pairing && passkey) {
    d.setFont(&fonts::Font7);
    char code[8];
    snprintf(code, sizeof code, "%06lu", (unsigned long)passkey);
    d.drawString(code, d.width() / 2, 62);
  } else {
    d.setFont(&fonts::FreeSansBold12pt7b);
    d.drawString(word, d.width() / 2, 52);
  }
  d.setFont(&fonts::Font2);
  d.setTextDatum(top_left);
  d.setTextColor(TFT_LIGHTGREY);
  const String& line = noteText.length() ? noteText : heardText;
  if (line.length()) {
    d.setCursor(6, 84);
    d.setTextWrap(true);
    d.print(line.length() > 90 ? line.substring(0, 87) + "..." : line);
  }
  d.endWrite();
  uiDirty = false;
}

// ---- power ------------------------------------------------------------------------------------------------
static void displayWake() {
  if (displayOn) return;
  M5.Display.wakeup();
  M5.Display.setBrightness(120);
  displayOn = true;
  markDirty();
}

static void displaySleep() {
  if (!displayOn) return;
  M5.Display.sleep();                                  // backlight 0 and the ST7789's own sleep command
  displayOn = false;
}

static void setLink(bool fast) {
  if (fast == linkFast || !server || connHandle == 0xFFFF || !secured) return;
  linkFast = fast;
  if (fast) server->updateConnParams(connHandle, FAST_MIN, FAST_MAX, FAST_LAT, LINK_TIMEOUT);
  else server->updateConnParams(connHandle, IDLE_MIN, IDLE_MAX, IDLE_LAT, LINK_TIMEOUT);
  Serial.printf("[pwr] asking for the %s link\n", fast ? "fast" : "idle");
}

static uint8_t pm1Read(uint8_t reg) { return M5.In_I2C.readRegister8(0x6E, reg, 100000); }
static void pm1Write(uint8_t reg, uint8_t v) { M5.In_I2C.writeRegister8(0x6E, reg, v, 100000); }
static void imuWrite(uint8_t reg, uint8_t v) { M5.In_I2C.writeRegister8(0x68, reg, v, 400000); }

// Off until the stick is moved (or the power button): M5Stack's StickS3 low-power guide (docs.m5stack.com/en/
// arduino/m5sticks3/m5pm1) keeps the IMU's LDO through the M5PM1's shutdown and wakes on its GPIO4, which the
// BMI270's INT1 drives. Registers from the M5PM1 library (github.com/m5stack/M5PM1, src/M5PM1.h) and Bosch's
// BMI270 API (github.com/boschsensortec/BMI270_SensorAPI): any-motion is feature page 1, 0x3C..0x3F.
static void imuArmMotion();
static void pm1ArmWakeAndOff();

static void powerOffUntilMoved() {
  Serial.println("[pwr] lying still on battery: off until moved or the power button");
  Serial.flush();
  speakerDown();
  M5.Display.sleep();
  imuArmMotion();
  pm1ArmWakeAndOff();
}

// BMI270: accelerometer only, low power, any-motion on INT1 as an active-low push-pull pulse.
static void imuArmMotion() {
  imuWrite(0x7C, 0x00);                                // adv_power_save off while it is configured
  delay(2);
  imuWrite(0x2F, 0x01);                                // feature page 1
  uint8_t feat[16] = {0};
  M5.In_I2C.readRegister(0x68, 0x30, feat, sizeof feat, 400000);
  // Bench, 2026-09-30: at 125 mg for 100 ms footsteps on the floor woke it (the owner: "shakes on the ground wake
  // it up"). A pick-up is a bigger, longer move than a floor vibration: about 200 mg (0.488 mg a step) for 200 ms.
  const uint16_t duration = 10;                        // 10 x 20 ms over the threshold
  const uint16_t threshold = 0x1A0;
  uint16_t w0 = duration | (1u << 13) | (1u << 14) | (1u << 15);   // x, y, z
  uint16_t w1 = threshold | (1u << 15);                // enable
  feat[0x0C] = w0 & 0xFF; feat[0x0D] = w0 >> 8;
  feat[0x0E] = w1 & 0xFF; feat[0x0F] = w1 >> 8;
  M5.In_I2C.writeRegister(0x68, 0x30, feat, sizeof feat, 400000);
  imuWrite(0x2F, 0x00);
  // INT1: output on, OPEN-DRAIN, active low, with the M5PM1's own pull-up on GPIO4 (it stays powered through
  // shutdown). Push-pull woke the stick at once on the bench (wake source 0x20 with the stick lying still, while the
  // same setup stayed quiet when armed without powering off): the line fell as the rails switched. Open-drain only
  // goes low when the sensor pulls it, on a real move.
  imuWrite(0x53, 0x0C);
  imuWrite(0x55, 0x00);                                // not latched: a pulse per motion
  imuWrite(0x56, 0x40);                                // INT1 <- any-motion
  imuWrite(0x40, 0x17);                                // ACC_CONF: 50 Hz, averaging, filter_perf 0 (low power)
  imuWrite(0x7D, 0x04);                                // accelerometer only
  imuWrite(0x7C, 0x03);                                // adv_power_save on
  // Changing the sensor's mode can itself read as motion: let it settle, then clear whatever it raised (reading
  // INT_STATUS_0 clears it), so only a real move afterwards pulls INT1 low.
  delay(500);
  (void)M5.In_I2C.readRegister8(0x68, 0x1C, 400000);
}

static void pm1ArmWakeAndOff() {
  // M5PM1: keep the IMU's 3.3 V LDO through shutdown and wake on GPIO4 falling (GPIO3, the amplifier, shares
  // the wake line and stays off it).
  pm1Write(0x06, pm1Read(0x06) | (1u << 2) | (1u << 4));     // LDO_EN, LED_EN level (as M5Stack's example)
  pm1Write(0x07, pm1Read(0x07) | (1u << 5));                  // LDO power hold
  pm1Write(0x18, (pm1Read(0x18) | (1u << 4)) & ~(1u << 3));   // wake on GPIO4, not GPIO3
  pm1Write(0x19, pm1Read(0x19) & ~(1u << 4));                 // falling edge
  pm1Write(0x15, (pm1Read(0x15) & ~0x03u) | 0x01u);           // GPIO4 pull-up (INT1 is open-drain)
  pm1Write(0x05, 0x00);                                       // clear the wake flags
  delay(20);
  pm1Write(0x0C, 0xA1);                                       // shutdown
  delay(1000);
  Serial.println("[pwr] still on after the shutdown command");
}

// The battery's voltage every 5 minutes since boot, kept in RAM and printed on USB: a run on battery shows its
// discharge once the stick is plugged back in (the M5PM1 reports voltage, not current).
static constexpr size_t PWR_LOG = 288;                 // 24 hours
static uint16_t pwrLog[PWR_LOG];
static size_t pwrLogLen = 0;
static uint8_t bootWake = 0;                           // M5PM1 0x05 at boot: what powered the stick on
static uint32_t offSoonAt = 0;                         // bench: power off this soon once lying still on battery
static void pwrLogPrint() {
  Serial.printf("[pwr] this boot woke by 0x%02x%s\n", bootWake,
                (bootWake & (1u << 5)) ? " (moved: the IMU)" : (bootWake & (1u << 2)) ? " (power button)" : "");
  Serial.printf("[pwr] battery mV every 5 min since boot (%u):", unsigned(pwrLogLen));
  for (size_t i = 0; i < pwrLogLen; ++i) Serial.printf(" %u", unsigned(pwrLog[i]));
  Serial.println();
}

// ---- setup / loop -----------------------------------------------------------------------------------------
static void startBle() {
  BLEDevice::init("buddy stick");
  BLEDevice::setMTU(256);
  BLESecurity* sec = new BLESecurity();
  sec->setPassKey(false);                              // a fresh random code each pairing, shown on screen
  sec->setCapability(ESP_IO_CAP_OUT);
  sec->setAuthenticationMode(true, true, true);        // bond, MITM (the code), secure connections
  BLEDevice::setSecurityCallbacks(new SecurityCallbacks());

  server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  server->advertiseOnDisconnect(true);
  BLEService* svc = server->createService(SERVICE_UUID);
  up = svc->createCharacteristic(UP_UUID, BLECharacteristic::PROPERTY_NOTIFY | BLECharacteristic::PROPERTY_READ |
                                              BLECharacteristic::PROPERTY_READ_AUTHEN);
  up->addDescriptor(new BLE2902());
  BLECharacteristic* down = svc->createCharacteristic(
      DOWN_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR |
                     BLECharacteristic::PROPERTY_WRITE_AUTHEN);
  down->setCallbacks(new DownCallbacks());
  svc->start();
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);
  adv->setMaxPreferred(0x12);
  adv->setMinInterval(ADV_INTERVAL);
  adv->setMaxInterval(ADV_INTERVAL);
  BLEDevice::startAdvertising();
}

void setup() {
  auto cfg = M5.config();
  cfg.fallback_board = m5::board_t::board_M5StickS3;
  M5.begin(cfg);
  setCpuFrequencyMhz(CPU_MHZ);
  Serial.begin(115200);
  Serial.printf("[boot] buddy_stick %s, cpu %u MHz\n", BUDDY_GIT_SHA, unsigned(getCpuFrequencyMhz()));
  {
    uint8_t why = pm1Read(0x05);                       // what powered it on: bit5 the IMU (moved), bit2 the button
    bootWake = why;
    Serial.printf("[boot] woke by 0x%02x%s\n", why, (why & (1u << 5)) ? " (moved)" : (why & (1u << 2)) ? " (button)" : "");
    pm1Write(0x05, 0x00);
    pm1Write(0x07, pm1Read(0x07) & ~(1u << 5));        // the LDO hold is only for being off
  }
  M5.Display.setRotation(1);
  M5.Display.setBrightness(120);

  Serial.printf("[boot] board %d, psram %u, imu %d\n", int(M5.getBoard()), unsigned(ESP.getPsramSize()),
                int(M5.Imu.isEnabled()));
  lock = xSemaphoreCreateMutex();
  ringStore = (int16_t*)ps_malloc(RING_SAMPLES * sizeof(int16_t));
  selfTestPcm = (int16_t*)ps_malloc(RATE * 10 * sizeof(int16_t));
  for (auto& b : playBuf) b = (int16_t*)ps_malloc(PLAY_CHUNK * sizeof(int16_t));
  if (!ringStore || !selfTestPcm || !playBuf[0] || !playBuf[1] || !playBuf[2]) {
    Serial.println("[fatal] PSRAM allocation failed");
    M5.Display.drawString("PSRAM failed", 10, 10);
    for (;;) delay(1000);
  }
  ring = new PcmRing(ringStore, RING_SAMPLES);

  M5.update();
  selfTest = M5.BtnB.isPressed();
  speakerUp();
  if (selfTest) {
    Serial.println("[boot] self-test: hold A to record, release to hear it");
    setUi(Ui::SelfTest);
  } else {
    startBle();
    Serial.println("[boot] advertising as \"buddy stick\"");
  }
  beep(523, 80);
  delay(100);
  beep(784, 100);
  lastInput = lastMotion = millis();
}

void loop() {
  M5.update();
  uint32_t now = millis();

  if (M5.BtnA.wasPressed() || M5.BtnB.wasPressed()) {
    lastInput = now;
    displayWake();
  }
  if (M5.BtnA.wasPressed()) {
    if (selfTest || (connected && secured)) startPress();
    else beep(220, 120);                              // no phone: nowhere to send it
  }
  if (recording) {
    pumpMic();
    const char* why = M5.BtnA.wasReleased() ? "released" : !M5.BtnA.isPressed() ? "not pressed"
                      : (!selfTest && !connected) ? "phone gone" : (!selfTest && !secured) ? "link not secured"
                      : pressSamples >= RATE * 115 ? "time cap" : nullptr;
    if (why) {
      Serial.printf("[press] ended: %s after %u ms\n", why, unsigned(pressSamples * 1000 / RATE));
      endPress();
    }
  }
  if (M5.BtnB.wasHold()) {                             // 3 s: forget the paired phone
    ble_store_clear();
    Serial.println("[link] bonds cleared");
    noteText = "paired phone forgotten";
    beep(330, 200);
    markDirty();
  } else if (M5.BtnB.wasClicked()) {
    volumeIdx = (volumeIdx + 1) % sizeof(VOLUMES);
    if (speakerOn) M5.Speaker.setVolume(VOLUMES[volumeIdx]);
    beep(660, 60);
    markDirty();
  }

  // Messages from the phone.
  for (;;) {
    String msg;
    xSemaphoreTake(lock, portMAX_DELAY);
    if (inboxCount) {
      msg = inbox[inboxHead];
      inbox[inboxHead] = String();
      inboxHead = (inboxHead + 1) % 8;
      --inboxCount;
    }
    xSemaphoreGive(lock);
    if (!msg.length()) break;
    handle(msg);
  }
  if (helloWanted && secured) {
    helloWanted = false;
    sendHello();
  }

  // Link state on screen.
  if (!selfTest) {
    static bool wasConnected = false, wasSecured = false;
    static uint32_t shownKey = 0;
    if (connected != wasConnected || secured != wasSecured || passkey != shownKey) {
      wasConnected = connected;
      wasSecured = secured;
      shownKey = passkey;
      if (!connected) {
        if (recording) endPress();
        stopReply();
        waitTick.stop();
        setUi(Ui::NoPhone);
      } else if (passkey && !secured) {
        setUi(Ui::Pairing);
        displayWake();
        lastInput = now;
      } else if (secured && (ui == Ui::NoPhone || ui == Ui::Pairing)) {
        setUi(Ui::Ready);
      }
      markDirty();
    }
  }

  pumpSpeaker();

  static uint32_t lastImu = 0, lastWake = 0;
  static float px = 0, py = 0, pz = 0;
  static bool imuPrimed = false;
  if (!selfTest && M5.Imu.isEnabled() && uint32_t(now - lastImu) >= 100) {
    lastImu = now;
    float ax, ay, az;
    if (M5.Imu.getAccel(&ax, &ay, &az)) {
      float moved = fabsf(ax - px) + fabsf(ay - py) + fabsf(az - pz);
      px = ax; py = ay; pz = az;
      if (imuPrimed && moved > MOTION_G) {
        lastMotion = now;
        displayWake();
      }
      if (imuPrimed && moved > WAKE_G && connected && secured && !recording &&
          (lastWake == 0 || uint32_t(now - lastWake) >= WAKE_EVERY_MS)) {
        lastWake = now;
        lastActive = now;                               // pings follow, so the phone stays awake for the press
        sendEvent("wake");
      }
      imuPrimed = true;
    }
  }

  // Keep the phone's app awake while a turn is live, so the reply is relayed with the screen locked.
  bool live = recording || ui == Ui::Thinking || ui == Ui::Speaking || uint32_t(now - lastActive) < AWAKE_AFTER_MS;
  if (!selfTest && connected && secured && live && uint32_t(now - lastPing) >= PING_EVERY_MS) {
    lastPing = now;
    sendEvent("ping");
  }
  if (!selfTest) {
    PowerInputs pin{};
    pin.now = now;
    pin.lastInput = lastInput;
    pin.lastMotion = lastMotion;
    pin.lastActive = lastActive;
    pin.lastSound = lastSound;
    pin.live = live && (recording || ui == Ui::Thinking || ui == Ui::Speaking);
    xSemaphoreTake(lock, portMAX_DELAY);
    pin.soundPending = ring->size() > 0;
    xSemaphoreGive(lock);
    pin.soundPending = pin.soundPending || (speakerOn && M5.Speaker.isPlaying());
    pin.pairing = ui == Ui::Pairing;
    static uint32_t lastVbus = 0;
    static bool onBattery = false;
    if (lastVbus == 0 || uint32_t(now - lastVbus) >= 5000) {
      lastVbus = now | 1;
      onBattery = M5.Power.getVBUSVoltage() < 4000;
    }
    pin.onBattery = onBattery;
    PowerPlan plan = PowerPolicy::plan(pin);
    if (plan.displayOn) displayWake(); else displaySleep();
    if (!plan.audioOn && !recording) speakerDown();
    if (connected && secured) setLink(plan.linkFast);
    // Bench ("offsoon" on USB, then unplug): off after 15 s lying still on battery, to test the wake on pick-up.
    bool soon = offSoonAt && onBattery && !pin.live && uint32_t(now - lastMotion) >= 15000 &&
                uint32_t(now - lastInput) >= 15000;
    if (plan.powerOff || soon) powerOffUntilMoved();
  }
  static uint32_t lastPwrLog = 0;
  if (uint32_t(now - lastPwrLog) >= 5UL * 60 * 1000 || lastPwrLog == 0) {
    lastPwrLog = now | 1;
    if (pwrLogLen < PWR_LOG) pwrLog[pwrLogLen++] = uint16_t(M5.Power.getBatteryVoltage());
    pwrLogPrint();
  }
  static uint32_t lastDraw = 0;
  if (displayOn && (uiDirty || uint32_t(now - lastDraw) > 30000)) {
    lastDraw = now;
    draw();
  }
  // Bench commands on USB: "off" powers off until moved (to test the wake), "pwr" prints the battery log.
  static String cmd;
  while (Serial.available()) {
    char c = char(Serial.read());
    if (c == '\n' || c == '\r') {
      if (cmd == "off") powerOffUntilMoved();
      else if (cmd == "pwr") pwrLogPrint();
      else if (cmd == "imutest") {
        // Arm the any-motion wake without powering off, and watch the line the M5PM1 wakes on (its GPIO4 input,
        // GPIO_IN 0x12 bit 4) for 10 s: it must stay high while the stick lies still and go low when it is moved.
        Serial.println("[pwr] imutest: arming; keep it still for 5 s, then move it");
        imuArmMotion();
        uint32_t t0 = millis(), lows = 0, samples = 0, firstLow = 0;
        uint8_t ints = 0;
        while (millis() - t0 < 10000) {
          bool high = pm1Read(0x12) & (1u << 4);
          ints |= M5.In_I2C.readRegister8(0x68, 0x1C, 400000);
          ++samples;
          if (!high) { ++lows; if (!firstLow) firstLow = millis() - t0; }
          if ((samples % 20) == 0) Serial.printf("[pwr] imutest %2us: lows %u of %u, int_status0 seen 0x%02x\n",
                                                 unsigned((millis() - t0) / 1000), unsigned(lows), unsigned(samples), ints);
          delay(50);
        }
        Serial.printf("[pwr] imutest done: first low at %u ms, lows %u of %u; restarting\n", unsigned(firstLow),
                      unsigned(lows), unsigned(samples));
        Serial.flush();
        ESP.restart();
      }
      else if (cmd == "offsoon") {
        offSoonAt = now | 1;
        Serial.println("[pwr] armed: unplug, set it down; off after 15 s still, pick it up to wake");
      }
      cmd = "";
    } else if (cmd.length() < 16) {
      cmd += c;
    }
  }
  static uint32_t lastBeat = 0;
  if (uint32_t(now - lastBeat) >= 5000) {              // the bench's view of a stick it cannot see
    lastBeat = now;
    Serial.printf("[hb] ui=%d phone=%d secured=%d payload=%u batt=%d%% %dmV screen=%d audio=%d link=%s\n", int(ui),
                  int(connected), int(secured), unsigned(payload), int(M5.Power.getBatteryLevel()),
                  int(M5.Power.getBatteryVoltage()), int(displayOn), int(speakerOn), linkFast ? "fast" : "idle");
  }
  // Recording paces itself on the mic; a turn needs the loop quick; idle, 20 ms is plenty for buttons and the IMU.
  if (!recording) delay((live || speakerOn) ? 2 : 20);
}
