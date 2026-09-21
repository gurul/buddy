#pragma once
#include <stdint.h>
#include <string.h>
#include "ticks.h"

// Transient semantic eyes/sound only. No pose or actuator fields.
namespace expression {
enum Kind : uint8_t { None, Calm, Happy, Curious, Affection, Surprised, Startled };
inline const char* name(Kind k) {
  static const char* names[] = {"none", "calm", "happy", "curious", "affection", "surprised", "startled"};
  return k <= Startled ? names[k] : "none";
}
inline Kind parse(const char* s) {
  if (s) for (unsigned i = Calm; i <= Startled; ++i)
    if (!strcmp(s, name((Kind)i))) return (Kind)i;
  return None;
}
struct Request { uint32_t id = 0; Kind kind = None; uint32_t ttl = 4000; bool chirp = false; };
struct State {
  Request request;
  uint32_t at = 0, lastChirp = 0;
  bool pending = false, hasChirped = false;
  bool active(uint32_t now) const {
    return request.kind != None && ticks::elapsedMs(now, at) < request.ttl;
  }
  bool accept(Request next, uint32_t now) {
    if (!next.id || next.kind > Startled || (request.id && (int32_t)(next.id - request.id) <= 0)) return false;
    bool changed = !active(now) || next.kind != request.kind;
    next.ttl = next.ttl < 500 ? 500 : next.ttl > 6000 ? 6000 : next.ttl;
    request = next; at = now;
    pending = changed && next.chirp && next.kind > Calm;
    return true;
  }
  // Blocked cues are discarded; an occupied speaker may defer until expiry.
  bool wantsChirp(uint32_t now, bool allowed, bool muted, bool busy) {
    if (!active(now) || !allowed || muted) pending = false;
    return pending && !busy && (!hasChirped || ticks::elapsedMs(now, lastChirp) >= 8000);
  }
  void played(uint32_t now) { pending = false; hasChirped = true; lastChirp = now; }
};
inline bool allowed(bool hidden, bool attention, bool listening, bool asking, bool error) {
  return !hidden && !attention && !listening && !asking && !error;
}
}
