#pragma once
#include <stdint.h>
#include <string.h>
#include "ticks.h"

// Transient semantic eyes only. No pose or actuator fields.
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
struct Request { uint32_t id = 0; Kind kind = None; uint32_t ttl = 4000; };
struct State {
  Request request;
  uint32_t at = 0;
  bool active(uint32_t now) const {
    return request.kind != None && ticks::elapsedMs(now, at) < request.ttl;
  }
  bool accept(Request next, uint32_t now) {
    if (!next.id || next.kind > Startled || (request.id && (int32_t)(next.id - request.id) <= 0)) return false;
    next.ttl = next.ttl < 500 ? 500 : next.ttl > 6000 ? 6000 : next.ttl;
    request = next; at = now;
    return true;
  }

};
inline bool allowed(bool hidden, bool attention, bool listening, bool asking, bool error) {
  return !hidden && !attention && !listening && !asking && !error;
}
}
