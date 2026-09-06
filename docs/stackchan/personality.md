# Personality: how buddy feels, and how you can tell

Two things make the robot read as alive while it explores: an **affect engine**
on the board (valence, arousal, two drives) that colours its eyes, LEDs, head and
chirps, and a **diary** on the host with a memory of everything it has seen, so
what it writes is specific and its own. A third layer — **agent phases** — makes
the robot act out the voice conversation and the computer-use task the daemon is
really running (see [voice.md](voice.md)).

## 1. The affect engine (`firmware/claude_pet_stackchan/src/mood.cpp`)

The model is the lightweight one most social robots use — a point in the
valence/arousal circumplex plus a few homeostatic drives — because it fits an
ESP32 in a handful of floats and reads clearly to people.

**State**

| Variable | Range | Timescale | What it is |
|---|---|---|---|
| `v`, `a` | −1..1 | relaxes toward the mood, τ 60 s | the feeling right now (Mini: k = 0.05) |
| `moodV`, `moodA` | −0.5..0.5 | drifts toward the feeling, τ 1 h | the slow baseline; starts slightly cheerful (personality) |
| `social` | 0..1 | +0.0004/s alone | rises with time without a face or a touch; satisfied fast by company |
| `stimulation` | 0..1 | +0.001/s quiet | rises when nothing moves and no view is new; satisfied by motion or a new view |

**Stimuli** are fixed deltas (MiRo, Mini): motion in view raises arousal (+0.2 per
second of solid motion); the owner's face once per visit +0.4 valence, +0.2 arousal;
a stranger a little interest and a little wariness; a touch +0.2 valence; arriving
at a new view +0.1 arousal (a small surprise if already keen); a **sudden big
motion while calm is a startle** (+0.7 arousal, −0.35 valence, 20 s cool-down,
never asleep). The host's diary appraisal (`{"cmd":"emote","dv","da"}`) nudges
the feeling by at most ±0.3 — it can colour, not hijack.

**Drives** past their regime threshold pull the feeling toward a target rather
than pushing without bound: stimulation > 0.6 pulls arousal toward −0.6 (bored),
social > 0.7 pulls valence toward −0.7 (lonely), both with τ 20 s — stronger than
the relaxation toward the mood on purpose, so the robot settles at "bored" or
"lonely" instead of at the rail. A quiet room reads as bored after two or three
minutes and lonely after a quarter of an hour alone.

**Kind** (one active at a time, winner-take-all with recent-event priority):

| Kind | When | Eyes | LEDs | Head | Chirp | Word |
|---|---|---|---|---|---|---|
| startled | a jolt < 1.5 s ago, or a > 0.6 with v < −0.2 | wide, horizontal flicker | red flash, fades over 1.5 s | back and up, fast | sharp double blip | `eek!` |
| surprised | a new view while keen, < 2.5 s ago | wide, curious | white flash, fades | pitch +10 | one high blip | `!` |
| affection | the owner seen < 20 s ago, v > 0.2 | curious, smile | pink heartbeat from the middle of each row outward | — | soft warble | `<3` |
| happy | v > 0.4, a > −0.2 | HAPPY mood (lower-lid smile) | green sparkle | quicker wander | soft warble | `happy` |
| curious | a > 0.2, v ≥ −0.2 | curious, quick blinks and saccades | scanner dot bouncing along each row, faster with arousal | wide, quick wander | two rising notes | `curious...` |
| lonely | v < −0.4, a < 0.2 | TIRED mood, droopy | only the middle two LEDs, dim purple, slow | pitch −10 | slow falling sigh | `lonely...` |
| bored | a < −0.35 | half-closed, slow blinks and saccades | only the middle two LEDs, dim, 0.25 Hz | narrow, slow, pitch −10 | slow falling sigh | `bored...` |
| calm | otherwise | default | breathing wave travelling along the rows, in the feeling's colour and pulse rate | normal | — | `exploring...` |

**The back LEDs** are twelve WS2812 in two rows of six (0–5 left, 6–11 right).
Every state is an animation over the rows — `body.cpp` `patWave` (a glow that
travels along the row), `patScanner`, `patHeartbeat`, `patSparkle`, `patDroop`,
`patAlternate`, `patFlashFade` — composed at 20 Hz; only the LEDs that changed
are written to the PY32 expander.

**Continuous expression** is read off (v, a) every frame, not looked up per kind:
eyelid openness 0.55 + 0.4·a; blink interval 1.5 s/(1 + a); saccade tempo
2 s/(1 + 1.5·a); LED hue from valence (orange − / green + / cyan calm-positive),
saturation 0.4 + 0.5·|v|, brightness 0.3 + 0.6·(a + 1)/2, pulse 0.25 / 0.5 /
2.5 Hz; look-around amplitude 30 + 15·a degrees, tempo 1 + 0.6·a. One chirp per
kind change, at most one every 8 s.

**Where it shows.** Only while exploring, and never over a permission card, the
listening pose, the attention state, or an agent phase. The engine integrates all
the time, so a startle right before a conversation is still felt afterwards.

**Reference model and parity.** `bridge/src/cc_buddy_bridge/mood_model.py` is a
line-for-line Python port; `tests/test_mood_model.py` checks the behaviour
(curious → relax, startle with cool-down, affection once per visit, bored then
lonely, clamped appraisal, one chirp per change) and a parity harness compiles
`mood.cpp` on the host with clang and runs both on a 40-minute scripted trace
(9,600 steps, max |Δv| < 1e-3, zero kind mismatches on the last run). Change a
constant in one file and the other.

## 2. Agent phases (`{"cmd":"agent","state":…}`)

| Phase | Head | Eyes | LEDs | Status word | Chirp |
|---|---|---|---|---|---|
| wake | up (+12) | open, curious | white flash, fades | `yeah?` | wake whistle |
| listening | faces you (last toucher side), pitch +15 | wide (110 px) | blue breathing wave | `listening...` | — |
| thinking | tilted 14° aside, slow side-to-side every 2.5–4 s | quick saccades | cyan scanner dot | `hmm...` | — |
| speaking | up (+10), small bobs every 0.5–0.8 s | HAPPY | white sparkle | caption pages (4 lines × 17, held at reading pace) | one beep-boop phrase per page |
| working | down at the desk (pitch 30), typing glances every 0.7–1.2 s | squint + curious, fast saccades | fast cyan ripple | `on it...` | — |
| asking | up (attention pitch) | wide | left row / right row alternating orange | `yes / no?` | "hm?" |
| done | nod | HAPPY + laugh | green sweep, then solid green | `done!` | beep-boop |
| error | side-to-side wobble | TIRED + flicker + confused | red double flash | `oops` | descending boop |
| idle | back to the persona state | — | — | — | — |

A phase older than 15 minutes with no follow-up (the daemon died mid-conversation)
falls back to the persona.

## 3. The diary (`bridge/src/cc_buddy_bridge/diary.py`)

The old note was a caption: "a desk with a monitor and a keyboard", once per
changed frame, with no memory. The diary gives the explorer both.

**Memory stream.** One record per frame in
`~/.config/cc-buddy-bridge/notes/memory.jsonl`: the thought, 3–6 concrete
observations, what changed, tags, novelty and importance (1–10), whether it was
written, the appraisal (valence, arousal, label), and when it was last retrieved.

**Profile.** `profile.md` with four blocks — ROOM (persistent objects and their
usual state), HUMAN (habits, with evidence ids), SELF (persona, running jokes, open
questions), RULES (what to ignore) — each capped at 2,000 characters.

**Retrieval for the next thought** (no LLM call, ~2k tokens): the profile; the last
three written thoughts; five older records scored by recency × importance ×
relevance (0.995 per hour decay, tag overlap, min-max normalised, equal weights);
two thoughts from the same hour on earlier days; today's unwritten candidates so
they are not repeated.

**One vision call per frame** returns observations, what changed, **three
candidate thoughts with a typicality probability each**, novelty, importance,
tags and the feeling. The daemon takes the least typical candidate that says
something outside the baseline vocabulary and is not a near-repeat (token Jaccard
> 0.6) of the last ten.

**Write gate.** Written iff novelty ≥ 5 or importance ≥ 7 or the diary has been
silent for 3 h. Unwritten thoughts stay in memory and feed retrieval (the widget's
diary window shows them dimmed).

**Dreams.** Once a day from 21:00, or when the summed importance of new records
passes 150, one text-only call asks the three most salient questions about the day,
answers them as insights citing record ids, rewrites the profile (ADD/UPDATE/DELETE
per fact), and proposes up to two **★ candidates** — one-line claims worth keeping
forever. Insights and candidates land under `## Dreams` in the day's file; the
widget shows them on the Dreams tab.

**Stars.** `highlights.md` is the human's layer: press ★ on a thought in the diary
window and it is appended there, dated, forever. It is always in buddy's prompt
context ("starred by my human — never forget, never contradict") and the dreams
pass may not contradict it. buddy never stars anything itself; it only proposes.

The layers mirror a debrief memory system: `memory.jsonl` = episodes (raw,
immutable); `YYYY-MM-DD.md` = the day (episodic, immutable once written);
`profile.md` = the semantic layer (rewired nightly, never appended);
`highlights.md` = the starred layer (human-promoted, append-only).

**Appraisal → feeling.** The same call's valence/arousal/label goes to the board as
`{"cmd":"emote"}` and nudges the affect engine (±0.3 max), so a lonely-looking
empty room and the owner walking in feel different on the robot's face.

## 4. What the literature says (fetched 2026-09-06)

- **Model**: Breazeal 2003 (Kismet: arousal/valence/stance + drives),
  Mitchinson & Prescott (MiRo: one V/A point for emotion, mood and temperament;
  fixed stimulus transforms; light pulse 0.25/0.5/2.5 Hz for arousal),
  Fernández-Rodicio et al. 2022 (Mini: emotion decay k = 0.05, mood k = 0.001,
  modulation profiles), Paplu, Mishra & Berns 2022 (arXiv:2202.09813, motives with
  satisfaction thresholds, angle in V/A → 28 emotion words).
- **Curiosity as behaviour**: Houbre & Pieters 2024 (arXiv:2412.00152, habituation
  and inhibition of return), Oudeyer 2018 (arXiv:1802.10546, novelty and surprise
  as interest measures).
- **Expression**: Rogel et al. 2026 (arXiv:2605.12786, design for V/A legibility
  before naming emotions), Mishra et al. 2024 (arXiv:2410.14337, eyes alone are
  weaker than eyes + head + colour), Casso et al. 2022 (arXiv:2209.00983, idle
  tempo must track arousal), Ribeiro & Paiva 2019 (arXiv:1904.02898, layer and
  blend animation channels), Wilms & Oberfeld 2018 (colour: arousal rises with
  saturation and brightness; valence best at medium saturation, green/blue > red).
- **Fast/slow split**: Wang et al. 2026 (MistyPilot, arXiv:2603.03640) and
  Broekens et al. 2023 (arXiv:2309.01664, LLM appraisal): the MCU runs the fast
  loop, the host LLM appraises slowly and nudges.
- **Diary**: Park et al. 2023 (arXiv:2304.03442, memory stream + recency/
  importance/relevance retrieval + reflection), Packer et al. 2023 (MemGPT/Letta
  core memory blocks), Chhikara et al. 2025 (Mem0, ADD/UPDATE/DELETE consolidation),
  Zhang et al. 2025 (Verbalized Sampling, arXiv:2510.01171, candidates with
  probabilities beat mode collapse), Kapur et al. 2026 (arXiv:2601.04609,
  specificity as picking the target out of a contrast set), Kang et al. 2026
  (arXiv:2604.12081, store only novel or emotionally salient scenes), Ichikura
  et al. 2023 (arXiv:2309.01948, robot diaries from joint experience read as more
  intimate), Pointeau & Dominey 2017 (iCub autobiographical memory).
- **Open source borrowed from**: Anki Vector's leaked emotion engine (five axes
  decaying to zero, simple mood types for animation selection), FluxGarage
  RoboEyes 1.1.2 (moods, curiosity, idle saccades, flicker), M5Stack-Avatar's
  six-expression vocabulary, xiaozhi-esp32's `{"emotion": tag}` transport.
