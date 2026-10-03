#pragma once
#include <stddef.h>
#include <stdint.h>

// The first 100 ms at 24 kHz contained the measured codec startup pop (2026-09-30).
// Count captured samples rather than wall time: queued mic jobs may arrive in bursts.
class MicStartup {
 public:
  static constexpr uint32_t SAMPLES = 24000 * 100 / 1000;
  void reset() { remaining_ = SAMPLES; }
  size_t skip(size_t captured) {
    size_t n = captured < remaining_ ? captured : remaining_;
    remaining_ -= n;
    return n;
  }

 private:
  uint32_t remaining_ = 0;
};
