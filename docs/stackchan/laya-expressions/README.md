# Live Laya expressions

Laya controls **eyes only**. Buddy's original chirps, caption babble, sound settings, and phase sounds are preserved. The owner explicitly requested reverting the experimental Laya chirps after trying them.

The model runs in the Mac bridge; the ESP32 renders its temporary expression overlay during speech and idle/explore. Spoken user turns, stable clauses in Buddy's streamed replies, and diary thoughts selected for the screen feed one dedicated worker. One pending event replaces older work; inference stays off the daemon event loop. Events are sent at most every 1.2 seconds, discarded after four seconds, and independently expired by the board.

Laya chooses calm, happy, curious, affection, surprised, or startled. Textual startle becomes calm because a sentence about an impact is not a physical impact. Happy and affection smile; curiosity uses curious eyes; surprise widens the eyes. Listening, permission prompts, error phases, screen-off and clock display retain priority. The firmware expression handler has no sound or motor control. Caption sounds follow their original behavior even while a Laya eye overlay is active.

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

`--check` exercises the actual local model and board with happy/curious cues during speaking and a suppressed affection cue during listening. It verifies eye application, expiry, no Laya chirp request or playback, and an unchanged sound setting. It does not mute or unmute Buddy. Board telemetry confirms the eye-render path ran; it is not a camera measurement of the screen.

Verification is recorded in [GATES.md](GATES.md) and `device-results.json`. Earlier tests of the superseded sound-enabled build are historical only. The final gate run targets eyes-only behavior and includes regression tests that normal caption chirps are preserved while Laya is enabled.
