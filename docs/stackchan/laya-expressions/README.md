# Live Laya expressions

Laya controls **eyes only**. Buddy's original chirps, caption babble, sound settings, and phase sounds are preserved. The owner explicitly requested reverting the experimental Laya chirps after trying them.

The model runs in the Mac bridge; the ESP32 renders its temporary expression overlay during speech and idle/explore. Spoken user turns, stable clauses in Buddy's streamed replies, and diary thoughts selected for the screen feed one dedicated worker. One pending event replaces older work; inference stays off the daemon event loop. Events are sent at most every 1.2 seconds, discarded after four seconds, and independently expired by the board.

Laya chooses eleven expressions: calm, happy, curious, affection, surprised, sad, worried, skeptical, frustrated, excited, and wink. Each has a distinct combination of eyelid shape, size, symmetry, rounding and blink tempo. A wink fully replaces the left eye with a thick curved line for 650 ms, once per event, while the other eye stays open; it then reopens. Listening, permission prompts, error phases, screen-off and clock display retain priority. The firmware expression handler has no sound or motor control. Caption sounds follow their original behavior even while a Laya eye overlay is active.

## Model and evidence

This is **experimental expression selection, not calibrated emotion recognition**. It selects Buddy's reaction from transcript meaning, not the person's hidden emotional state. Recent user and Buddy turns are speaker-labelled and retained for up to 90 seconds, so a reply can be interpreted in context. Listening still keeps the attentive eyes; the reaction appears when the speaking phase permits it.

The original 322M multilingual Laya weights remain unchanged. The separate older six-label tuning experiment did worse on fresh scenarios ([research and sources](../laya-emotion/README.md)). For this broader vocabulary, each candidate is a binary Laya question; the worker batches eleven questions and ranks their yes-versus-no logits. Normalized scores are relative support, not calibrated probabilities. No keyword animation script or new trained weights are used.

Four early prompt formulations scored 27/36, 27/39, 24/39 and 29/39 on authored examples (retained as `context-attempt-*.json`). The final binary formulation scored **34/39**, including all three wink cases. Those cases were reused during prompt selection, so this is a development measurement, not held-out accuracy or human validation. Errors include an ordinary time question becoming curious, some factual replies becoming surprised, and some ambiguity between sadness/frustration. Current measured results and provenance are in `context-results.json`.

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

`--check` exercises the actual local model and board with happy, sad, skeptical, excited and wink cues during speaking and a suppressed affection cue during listening. It verifies eye application, expiry, one wink animation for the wink event, no Laya sound request, and an unchanged sound setting. It does not mute or unmute Buddy. Board telemetry confirms the eye-render path ran; it is not a camera measurement of the screen.

Verification is recorded in [GATES.md](GATES.md) and `device-results.json`. Earlier tests of the superseded sound-enabled build are historical only. The final gate run targets eyes-only behavior and includes regression tests that normal caption chirps are preserved while Laya is enabled.
