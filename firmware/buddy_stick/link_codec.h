#pragma once
// The stick link's wire: IMA ADPCM audio frames and JSON frames, one GATT value each.
//
// The reference is tools/stick_link/adpcm.py; this copy and the iPhone app's (ios/BuddyLink/BuddyLink/ADPCM.swift)
// must match it byte for byte, which test/run.sh checks against tools/stick_link/vectors.txt. Plain C++ with no
// Arduino headers, so the same file compiles on the Mac under sanitizers.
//
// A frame:  [kind] then for audio [seq][predictor int16 LE][step index] [codes, low nibble first]
//           or for JSON the UTF-8 text.
#include <stddef.h>
#include <stdint.h>

namespace stick {

constexpr uint8_t KIND_AUDIO = 0x01;
constexpr uint8_t KIND_JSON = 0x02;
constexpr size_t HEADER = 5;

static const int16_t STEPS[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767};
static const int8_t INDEX_ADJUST[8] = {-1, -1, -1, -1, 2, 4, 6, 8};

struct State {
  int16_t pred = 0;
  uint8_t index = 0;
};

inline void step(State& s, uint8_t code) {
  int32_t st = STEPS[s.index];
  int32_t diff = st >> 3;
  if (code & 4) diff += st;
  if (code & 2) diff += st >> 1;
  if (code & 1) diff += st >> 2;
  int32_t p = (code & 8) ? int32_t(s.pred) - diff : int32_t(s.pred) + diff;
  s.pred = int16_t(p < -32768 ? -32768 : p > 32767 ? 32767 : p);
  int idx = int(s.index) + INDEX_ADJUST[code & 7];
  s.index = uint8_t(idx < 0 ? 0 : idx > 88 ? 88 : idx);
}

inline uint8_t encodeSample(State& s, int16_t sample) {
  int32_t st = STEPS[s.index];
  int32_t diff = int32_t(sample) - s.pred;
  uint8_t code = 0;
  if (diff < 0) { code = 8; diff = -diff; }
  if (diff >= st) { code |= 4; diff -= st; }
  if (diff >= (st >> 1)) { code |= 2; diff -= st >> 1; }
  if (diff >= (st >> 2)) code |= 1;
  step(s, code);
  return code;
}

// n samples (even) into n/2 code bytes.
inline void encode(State& s, const int16_t* pcm, size_t n, uint8_t* out) {
  for (size_t i = 0; i + 1 < n; i += 2) {
    uint8_t lo = encodeSample(s, pcm[i]);
    uint8_t hi = encodeSample(s, pcm[i + 1]);
    out[i / 2] = uint8_t(lo | (hi << 4));
  }
}

inline void decode(State& s, const uint8_t* codes, size_t len, int16_t* out) {
  for (size_t i = 0; i < len; ++i) {
    step(s, codes[i] & 0x0F);
    out[2 * i] = s.pred;
    step(s, codes[i] >> 4);
    out[2 * i + 1] = s.pred;
  }
}

// How many samples a frame of `payload` bytes carries.
constexpr size_t samplesPerFrame(size_t payload) { return payload > HEADER ? 2 * (payload - HEADER) : 0; }

// Writes one audio frame of n (even) samples into out, which holds HEADER + n/2 bytes. Advances seq and state.
inline size_t encodeFrame(State& s, uint8_t& seq, const int16_t* pcm, size_t n, uint8_t* out) {
  out[0] = KIND_AUDIO;
  out[1] = seq++;
  out[2] = uint8_t(uint16_t(s.pred) & 0xFF);
  out[3] = uint8_t(uint16_t(s.pred) >> 8);
  out[4] = s.index;
  encode(s, pcm, n, out + HEADER);
  return HEADER + n / 2;
}

// Decodes one audio frame into out (room for 2 * (len - HEADER) samples). Returns the sample count, or -1 when
// the frame is malformed. The sequence number goes to *seq.
inline int decodeFrame(const uint8_t* in, size_t len, int16_t* out, size_t outCap, uint8_t* seq) {
  if (len < HEADER + 1 || in[0] != KIND_AUDIO || in[4] > 88) return -1;
  size_t n = 2 * (len - HEADER);
  if (n > outCap) return -1;
  State s;
  s.pred = int16_t(uint16_t(in[2]) | (uint16_t(in[3]) << 8));
  s.index = in[4];
  decode(s, in + HEADER, len - HEADER, out);
  if (seq) *seq = in[1];
  return int(n);
}

// Counts lost frames from the sequence numbers: the gap since the last one seen, mod 256.
struct GapCounter {
  bool started = false;
  uint8_t last = 0;
  uint32_t lost = 0;
  void see(uint8_t seq) {
    if (started) lost += uint8_t(seq - last - 1);
    started = true;
    last = seq;
  }
  void reset() { started = false; lost = 0; }
};

}  // namespace stick
