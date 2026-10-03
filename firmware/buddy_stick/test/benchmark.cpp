// Host-only comparison with the previous per-sample ring insertion. No ESP32 timing claims.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "../pcm_ring.h"

class PreviousRing {
 public:
  PreviousRing(int16_t* p, size_t n) : buf_(p), cap_(n) {}
  void push(const int16_t* in, size_t n) {
    for (size_t i = 0; i < n; ++i) buf_[(head_ + i) % cap_] = in[i];
  }
  void pop(int16_t* out, size_t n) {
    const size_t first = cap_ - head_ < n ? cap_ - head_ : n;
    memcpy(out, buf_ + head_, first * sizeof(int16_t));
    memcpy(out + first, buf_, (n - first) * sizeof(int16_t));
    head_ = (head_ + n) % cap_;
  }
 private:
  int16_t* buf_;
  size_t cap_, head_ = 0;
};

struct Result { double ms; uint64_t checksum; };
template<class Ring> __attribute__((noinline)) Result run(size_t capacity) {
  std::vector<int16_t> storage(capacity), input(480), output(480);
  for (size_t i = 0; i < input.size(); ++i) input[i] = int16_t(i * 17);
  Ring ring(storage.data(), capacity);
  uint64_t checksum = 0;
  const auto start = std::chrono::steady_clock::now();
  for (size_t i = 0; i < 200000; ++i) {
    ring.push(input.data(), input.size());
    ring.pop(output.data(), output.size());
    // Both algorithms have the same externally observable memory work.
    asm volatile("" : : "g"(storage.data()) : "memory");
    checksum += uint16_t(output[i % output.size()]);
  }
  return {std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count(), checksum};
}

int main(int argc, char** argv) {
  const size_t capacity = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 1200;
  if (capacity < 480) return 1;
  for (int trial = 0; trial < 3; ++trial) {
    const auto before = run<PreviousRing>(capacity);
    const auto after = run<PcmRing>(capacity);
    if (before.checksum != after.checksum || before.checksum == 0) return 1;
    std::printf("ring trial=%d before_ms=%.3f after_ms=%.3f checksum=%llu\n", trial + 1, before.ms, after.ms,
                static_cast<unsigned long long>(after.checksum));
  }
}
