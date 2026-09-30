#pragma once
// Buddy's reply on its way to the speaker: a ring of 16-bit samples, and the rule for when to start playing.
// Plain C++ (no Arduino), so test/run.sh checks it on the Mac. The caller holds the lock around every call.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

class PcmRing {
 public:
  PcmRing(int16_t* storage, size_t capacity) : buf_(storage), cap_(capacity) {}

  size_t size() const { return count_; }
  size_t room() const { return cap_ - count_; }

  // Appends what fits and returns how many samples were taken; the rest is dropped (a reply longer than the
  // ring is cut, never wrapped over itself).
  size_t push(const int16_t* in, size_t n) {
    size_t take = n < room() ? n : room();
    for (size_t i = 0; i < take; ++i) buf_[(head_ + count_ + i) % cap_] = in[i];
    count_ += take;
    return take;
  }

  size_t pop(int16_t* out, size_t n) {
    size_t take = n < count_ ? n : count_;
    size_t first = cap_ - head_ < take ? cap_ - head_ : take;
    memcpy(out, buf_ + head_, first * sizeof(int16_t));
    memcpy(out + first, buf_, (take - first) * sizeof(int16_t));
    head_ = (head_ + take) % cap_;
    count_ -= take;
    return take;
  }

  void clear() { head_ = count_ = 0; }

 private:
  int16_t* buf_;
  size_t cap_;
  size_t head_ = 0;
  size_t count_ = 0;
};

// When the speaker may start: once enough is buffered to ride out Bluetooth's bursts, or once the phone has
// gone quiet (the end of a short reply). After an underrun it waits to refill the same way, so a gap mid-reply
// is one pause rather than a chopped sentence.
class PlayGate {
 public:
  static constexpr size_t START_SAMPLES = 24000 * 3 / 10;   // 300 ms at 24 kHz
  static constexpr uint32_t QUIET_MS = 200;

  void arrived(uint32_t now) { lastArrival_ = now; seen_ = true; }
  void reset() { playing_ = false; seen_ = false; }
  void underrun() { playing_ = false; }
  bool playing() const { return playing_; }

  bool mayPlay(size_t buffered, uint32_t now) {
    if (playing_) return buffered > 0;
    if (buffered == 0) return false;
    if (buffered >= START_SAMPLES || (seen_ && uint32_t(now - lastArrival_) >= QUIET_MS)) playing_ = true;
    return playing_;
  }

 private:
  bool playing_ = false;
  bool seen_ = false;
  uint32_t lastArrival_ = 0;
};

// A quiet tick while buddy thinks (after m5-atom-puck's WaitingFeedback): first 600 ms after release, then every
// 700 ms, until the first sound of the reply. Clock-wrap safe.
class WaitTick {
 public:
  static constexpr uint32_t FIRST_MS = 600;
  static constexpr uint32_t EVERY_MS = 700;
  void start(uint32_t now) { active_ = true; last_ = now; delay_ = FIRST_MS; }
  void stop() { active_ = false; }
  bool active() const { return active_; }
  bool due(uint32_t now) {
    if (!active_ || uint32_t(now - last_) < delay_) return false;
    last_ = now;
    delay_ = EVERY_MS;
    return true;
  }

 private:
  bool active_ = false;
  uint32_t last_ = 0;
  uint32_t delay_ = FIRST_MS;
};
