// The firmware's own link_codec.h and pcm_ring.h, on the Mac under ASan and UBSan (run.sh).
// The codec must match tools/stick_link/vectors.txt byte for byte: the Python reference wrote it.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "../link_codec.h"
#include "../pcm_ring.h"
#include "../power_policy.h"

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
      ++failures;                                                       \
    }                                                                   \
  } while (0)

static std::vector<int> ints(const std::string& line) {
  std::istringstream in(line);
  std::vector<int> v;
  int x;
  while (in >> x) v.push_back(x);
  return v;
}

static std::vector<uint8_t> unhex(const std::string& s) {
  std::vector<uint8_t> v;
  for (size_t i = 0; i + 1 < s.size(); i += 2) v.push_back(uint8_t(std::stoi(s.substr(i, 2), nullptr, 16)));
  return v;
}

static int vectors(const char* path) {
  std::ifstream f(path);
  CHECK(f.good());
  std::string head, pcmLine, hexLine, decLine, endLine;
  int seen = 0;
  while (std::getline(f, head)) {
    if (head.empty() || head[0] == '#') continue;
    std::getline(f, pcmLine);
    std::getline(f, hexLine);
    std::getline(f, decLine);
    std::getline(f, endLine);
    std::istringstream h(head);
    std::string name;
    int pred, index, count;
    h >> name >> pred >> index >> count;
    std::vector<int> pcmI = ints(pcmLine), decI = ints(decLine), end = ints(endLine);
    std::vector<uint8_t> want = unhex(hexLine);
    CHECK(int(pcmI.size()) == count && int(decI.size()) == count && want.size() == size_t(count / 2));
    std::vector<int16_t> pcm(pcmI.begin(), pcmI.end());

    stick::State s;
    s.pred = int16_t(pred);
    s.index = uint8_t(index);
    std::vector<uint8_t> got(count / 2);
    stick::encode(s, pcm.data(), pcm.size(), got.data());
    if (got != want) std::fprintf(stderr, "encode differs: %s\n", name.c_str());
    CHECK(got == want);
    CHECK(s.pred == end[0] && s.index == end[1]);

    stick::State d;
    d.pred = int16_t(pred);
    d.index = uint8_t(index);
    std::vector<int16_t> out(count);
    stick::decode(d, want.data(), want.size(), out.data());
    CHECK(std::vector<int>(out.begin(), out.end()) == decI);
    ++seen;
  }
  return seen;
}

static void frames() {
  // Framed round trip: each frame decodes alone to the same samples the plain codec gives.
  std::vector<int16_t> pcm(1000);
  for (size_t i = 0; i < pcm.size(); ++i) pcm[i] = int16_t((i * 997) % 20000 - 10000);
  const size_t payload = 180, spf = stick::samplesPerFrame(payload);
  CHECK(spf == 350);
  stick::State enc;
  uint8_t seq = 254;
  std::vector<int16_t> joined;
  stick::GapCounter gaps;
  for (size_t at = 0; at < pcm.size(); at += spf) {
    size_t n = std::min(spf, pcm.size() - at);
    uint8_t buf[payload];
    size_t len = stick::encodeFrame(enc, seq, pcm.data() + at, n, buf);
    CHECK(len <= payload);
    int16_t out[spf];
    uint8_t got = 0;
    int k = stick::decodeFrame(buf, len, out, spf, &got);
    CHECK(k == int(n));
    gaps.see(got);
    joined.insert(joined.end(), out, out + (k > 0 ? k : 0));
  }
  CHECK(seq == uint8_t(254 + 3));           // wrapped past 255
  CHECK(gaps.lost == 0);
  stick::State plain;
  std::vector<uint8_t> codes(pcm.size() / 2);
  stick::encode(plain, pcm.data(), pcm.size(), codes.data());
  stick::State pd;
  std::vector<int16_t> back(pcm.size());
  stick::decode(pd, codes.data(), codes.size(), back.data());
  CHECK(joined == back);

  // Malformed frames are refused; a good one passes (the control).
  int16_t out[400];
  const uint8_t good[] = {1, 0, 0, 0, 0, 0x12};
  CHECK(stick::decodeFrame(good, sizeof good, out, 400, nullptr) == 2);
  const uint8_t shortF[] = {1, 0, 0, 0, 0};
  const uint8_t json[] = {2, '{', '}', 0, 0, 0};
  const uint8_t badIdx[] = {1, 0, 0, 0, 89, 0};
  CHECK(stick::decodeFrame(shortF, sizeof shortF, out, 400, nullptr) == -1);
  CHECK(stick::decodeFrame(json, sizeof json, out, 400, nullptr) == -1);
  CHECK(stick::decodeFrame(badIdx, sizeof badIdx, out, 400, nullptr) == -1);
  CHECK(stick::decodeFrame(good, sizeof good, out, 1, nullptr) == -1);   // no room

  // Lost frames are counted across the wrap.
  stick::GapCounter g;
  g.see(250); g.see(251); g.see(254); g.see(1);
  CHECK(g.lost == 2 + 2);
}

static void ring() {
  int16_t store[8];
  PcmRing r(store, 8);
  int16_t a[6] = {1, 2, 3, 4, 5, 6}, out[8] = {};
  CHECK(r.push(a, 6) == 6);
  CHECK(r.pop(out, 4) == 4 && out[0] == 1 && out[3] == 4);
  CHECK(r.push(a, 6) == 6);                 // wraps
  CHECK(r.push(a, 6) == 0 && r.size() == 8);  // full: dropped, not overwritten
  CHECK(r.pop(out, 8) == 8 && out[0] == 5 && out[1] == 6 && out[2] == 1 && out[7] == 6);
  CHECK(r.size() == 0 && r.pop(out, 1) == 0);
  r.push(a, 3);
  r.clear();
  CHECK(r.size() == 0);
}

static void gate() {
  PlayGate g;
  CHECK(!g.mayPlay(0, 0));
  g.arrived(1000);
  CHECK(!g.mayPlay(100, 1100));             // little buffered, still arriving
  CHECK(g.mayPlay(100, 1000 + PlayGate::QUIET_MS));   // the phone went quiet: play what there is
  CHECK(g.mayPlay(5, 1300));
  g.underrun();
  g.arrived(2000);
  CHECK(!g.mayPlay(10, 2010));
  CHECK(g.mayPlay(PlayGate::START_SAMPLES, 2010));
  g.reset();
  CHECK(!g.mayPlay(10, 999999));            // after a reset nothing has arrived yet

  WaitTick t;
  CHECK(!t.due(0));
  t.start(0xFFFFFF00u);                     // across the clock wrap
  CHECK(!t.due(0xFFFFFF00u + 100));
  CHECK(t.due(0xFFFFFF00u + WaitTick::FIRST_MS));
  CHECK(!t.due(0xFFFFFF00u + WaitTick::FIRST_MS + 100));
  CHECK(t.due(0xFFFFFF00u + WaitTick::FIRST_MS + WaitTick::EVERY_MS));
  t.stop();
  CHECK(!t.due(0xFFFFFF00u + 100000));
}

static void power() {
  using P = PowerPolicy;
  PowerInputs idle{};
  idle.now = 10u * 60 * 60 * 1000;               // ten hours after boot, nothing recent
  idle.onBattery = true;
  PowerPlan p = P::plan(idle);
  CHECK(!p.displayOn && !p.audioOn && !p.linkFast && p.powerOff);          // all off, and off entirely

  PowerInputs in = idle;
  in.lastMotion = in.now - 1000;                 // just picked up
  p = P::plan(in);
  CHECK(p.displayOn && p.linkFast && !p.powerOff && !p.audioOn);           // screen and link up; no sound yet

  in = idle;
  in.lastSound = in.now - (P::AUDIO_MS - 1);
  CHECK(P::plan(in).audioOn);
  in.lastSound = in.now - P::AUDIO_MS;
  CHECK(!P::plan(in).audioOn);                                              // 4 s after the last sound: off
  in.soundPending = true;
  CHECK(P::plan(in).audioOn && !P::plan(in).powerOff);                      // a reply still to play keeps it

  in = idle;
  in.live = true;                                                           // a turn: everything on, never off
  p = P::plan(in);
  CHECK(p.displayOn && p.audioOn && p.linkFast && !p.powerOff);

  in = idle;
  in.onBattery = false;                                                     // on USB: never powers off
  CHECK(!P::plan(in).powerOff);
  in = idle;
  in.pairing = true;                                                        // a code on screen stays on
  CHECK(P::plan(in).displayOn && !P::plan(in).powerOff);

  in = idle;
  in.lastActive = in.now - (P::LINK_MS - 1);
  CHECK(P::plan(in).linkFast);
  in.lastActive = in.now - P::LINK_MS;
  CHECK(!P::plan(in).linkFast);                                             // back to the idle interval
  in.lastMotion = in.now - (P::OFF_MS - 1);
  CHECK(!P::plan(in).powerOff);                                             // moved 29:59 ago: stays on

  in = idle;                                                                // across the millis() wrap
  in.now = 5000;
  in.lastMotion = 0xFFFFFFFFu - 1000;
  CHECK(P::plan(in).displayOn && !P::plan(in).powerOff);
}

int main(int argc, char** argv) {
  int n = vectors(argc > 1 ? argv[1] : "tools/stick_link/vectors.txt");
  CHECK(n == 8);
  frames();
  ring();
  gate();
  power();
  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("host tests: %d vectors and all checks passed\n", n);
  return 0;
}
