# Live Laya expressions

The local Laya checkpoint now chooses brief eye expressions and nonverbal sounds for Buddy. It runs in the Mac bridge; the ESP32 renders the eyes and synthesizes chirps. The model does not run on the board.

Spoken user turns, stable clauses in Buddy's streamed replies, and diary thoughts selected for the screen feed a dedicated model worker. One pending event replaces older pending work; inference never runs on the daemon's event loop. The worker sends at most one cue every 1.2 seconds and discards results older than four seconds. The firmware independently expires each cue and restores its normal face. An eye cue works during speaking as well as idle/explore.

Laya chooses calm, happy, curious, affection, surprised, or startled. Textual startle is reduced to calm because a sentence about an impact is not a physical impact. Happy/affection use smiling eyes and a warble, curiosity uses curious eyes and a question chirp, surprise widens the eyes and chirps, and calm has no chirp. Sounds have an eight-second cooldown and do not interrupt an occupied speaker. Mute, listening, permission prompts, error phases, screen-off, and clock display retain priority. These commands contain no motor targets.

## Model and evidence

This is **experimental top-choice selection, not calibrated emotion recognition**. It chooses Buddy's expression from text; it does not measure the person's internal emotional state. The original 322M multilingual Laya checkpoint scored 24/36 on the fresh authored confirmation set; the tuned head scored 22/36. We use the original checkpoint. The earlier research threshold rejected all predictions and did not pass the quality gate. Enabling this live mode is a separate, explicit owner choice; it does not change those results. See [research and sources](../laya-emotion/README.md).

Model: `~/.config/cc-buddy-bridge/models/laya-multilingual-mlx`. Dependencies: the bridge's `fast` extra (`laya-mlx` on Apple silicon). Override with `CC_BUDDY_EXPRESSION_MODEL`. Enabling persists in `~/.config/cc-buddy-bridge/expressions.json`; new installations default off. Model load/inference errors leave ordinary phase expressions available.

## Controls

From the repository root:

```sh
bridge/.venv/bin/python bridge/tools/expression_live.py on
bridge/.venv/bin/python bridge/tools/expression_live.py status
bridge/.venv/bin/python bridge/tools/expression_live.py audition "I passed my final exam!"
bridge/.venv/bin/python bridge/tools/expression_live.py off
```

`react "text"` sends a cue without changing conversation phase. `audition` temporarily uses the speaking phase for six seconds, refuses an active conversation or pending prompt, and restores the previous phase. `--phase listening` checks that listening wins. `cc-buddy-bridge sound off` keeps eyes active and silences sounds.

`--check` exercises the real local model and connected board in speaking, muted-speaking and listening cases. It temporarily toggles sound, restores the original mute choice, and records results in `.test-artifacts/laya-live-device.json`. Board telemetry records actual entry into the eye-render path, speaker activity after chirp start, and expiry. It is not a camera or microphone measurement of human-visible/audible output.

Verification results are recorded in [GATES.md](GATES.md).

## Bench verification — 2026-09-21

The first integrated device run passed all three cases: happy eyes plus an actual speaker-start ACK during speaking; curious eyes with no sound while muted; and an affection proposal suppressed while listening. All three expired. Event-to-command latency was 79.5–100.1 ms (model time 32.5–67.2 ms) with the daemon running; these are three bench samples, not a latency distribution. Host regression coverage passed 204 tests; the acceptance suite passed 108 tests and the C++ firmware arbitration checks. A final clean-build run is retained in `device-results.json`.
