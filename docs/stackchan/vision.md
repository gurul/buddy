# Buddy sees, turns, leaves and hushes

**TL;DR.** During a conversation buddy can answer "what do you see?" from its own camera,
turn its head wherever you describe, pan the room, look for a thing, stop listening when
you mean goodbye in any words, and go silent on "mute" while it keeps moving. The voice
model cannot take images, so a small image model looks for it. Nothing is recorded or kept.

## Why a second model

The voice is `gpt-live-1` on the Live API. Its model page lists input modalities as audio
and text, with **image and video unsupported**, and the Live API in the installed `openai`
SDK (3.13) has no image input type at all — checked in the SDK source, with the Realtime
API's `input_image` type as the positive control. The Live session's Responses backend
(`gpt-6-astra`) could take images, but it is the slow, expensive half and should not see
every frame.

So `scene.py` sends camera frames to `gpt-5.4-nano` (image input, $0.20 per 1M input
tokens, reasoning effort none by default) and keeps the newest answer as text. **It is
pulled, never pushed**: the voice sees a view only when the backend calls `look`, which
happens when you ask. An earlier build pushed every view into the voice as silent context,
and buddy narrated the room unprompted (owner report, 2026-09-10).

```
 board camera ─frame 5/s─▶ daemon ─newest frame, ≤1 per 3 s─▶ gpt-5.4-nano (describe)
      ▲                      │                                     │ {"scene","change"}
      │ {"cmd":"look"}       │                                     ▼
      │                      │          scene.latest: [vision 16:43:05] A person holds a green mug.
   head.py ◀─move_head/find/look_around── gpt-6-astra backend ◀─look()─┘  (on request only)
                                              ▲
                                              └──── delegates ── gpt-live-1 (the voice)
```

Measured on this machine (`cc-buddy-bridge scene-test docs/assets/hero.png --find "the robot"`,
2026-09-10): a describe took 2.96 s, a locate 1.83 s.

## What the watcher keeps, and what the voice can pull

Only while a conversation is open, the watcher journals these lines for itself (`notes`;
the log records the tag and the kind, never the text):

| Line | When |
|---|---|
| `[vision 16:43:05] A person at a desk holding a green mug.` | the first view of a conversation |
| `[vision 16:43:14] … Change: they picked up the mug` | the describer says the view changed |
| `[vision 16:43:32] No change since 16:43:05: …` | at most every 20 s, and only when a newer view confirmed it |
| `[vision 16:44:00] Camera lost: you cannot see anything right now. Your last view, from 16:43:32, may be out of date.` | no frame for 3 s, or the board is gone (once) |
| `[vision …] You cannot make out the view right now.` | describes keep failing and the last view is over 15 s old (once) |

Every time is when the frame was **seen**, never when the answer came back, and a
"no change" line is stamped with the view that confirmed it, never with "now".

None of this reaches the voice on its own. "What do you see?" / "what am I holding?" goes to
the backend, which calls `look`: it returns the newest view with its `seen_at`, `age_secs`
and `stale` flag, reusing a view younger than 4 s or asking for a fresh one and waiting at
most one describe timeout (8 s). With the camera lost it answers that nothing can be seen.
A slow answer is dropped, never queued: at most one describe runs, and only the newest
frame waits.

## Turning the head

The backend reads your words and picks the numbers. One robot-centred frame, shared with
the firmware:

- **yaw** 0 faces you; negative is the robot's own left, positive its right; ±120 is as far
  as the neck turns, so "look behind you" is 120. You face the robot, so "my left" is its right.
- **pitch** 45 level, 5 down at the desk, 85 up at the ceiling.
- "a bit" ≈ 15°, "left" ≈ 70°, "all the way" 120; "more" / "back a little" are relative
  moves from the pose the board echoes on every frame.

| Tool | Does |
|---|---|
| `move_head(yaw, pitch, relative, hold_secs)` | one pose, held up to 60 s (default 15) |
| `look_around()` | five stops from far left to far right, a view at each from a frame taken **after** the head settled, then face you |
| `find(target)` | check the current view, then sweep; centre on the first sighting (the locate answer's x/y mapped through the 66° field of view) |

The firmware keeps a voice-requested pose through the conversation's phase changes, so buddy
does not snap back to centre when it starts talking (`src/hostlook.h`, [build.md](build.md)).

The voice model often answers "Looking." without delegating (bench, 2026-09-10 17:43: the head
never moved). So the session routes head requests itself: `intent.py` recognises them (the phrase
table for "look to your left", "turn around", "look at me"; the classifier's `look` label for
"where did I leave my keys?"), and 0.6 s later the owner's words go to the backend as a
`[look request]` — unless the voice already delegated that turn, so a relative move never runs twice.

## Goodbye in any words

The voice model often answered "Goodbye, pal." without ending anything, and the session sat
open until the idle timer. Now every finished turn of yours is checked, cheapest first:

1. a phrase table (`intent.py`): "bye buddy", "stop listening", "I want you to leave", "go away"…
2. otherwise `gpt-5.4-nano` labels it `leave`, `mute`, `unmute` or `none` (confidence ≥ 0.6).
   Requests about the computer stay `none`: "leave the tab open", "mute Spotify", "tell Sam goodbye".

On `leave` buddy stops forwarding the mic, says a short goodbye, and the conversation closes
0.8 s after its last word (6 s at most). A running task is not killed: it finishes, its result
is said, then the conversation closes. `cc-buddy-bridge intent-test --expect leave "…"` runs the
real classifier.

## Mute

"Mute", "be quiet", "no sound" turn every sound off; the head and the LEDs keep moving and the
captions stay on screen. "Unmute" / "sound back on" reverses it. The choice is kept in
`~/.config/cc-buddy-bridge/sound.json` and sent to the board on every connect as
`{"cmd":"sound","on":false}`; the firmware stores it and gates every chirp and beep on it.
Board firmware older than this change has no sound command: until it is reflashed, only the
caption chirps are silenced (the host drops their `chirp` flag). `cc-buddy-bridge sound off`
does the same from a shell.

## Privacy

- Frames are looked at **only while a conversation is open**; `offer` ignores them otherwise,
  and closing the conversation drops the frame slot and every description.
- At most one frame is held, in memory: the next frame replaces it, a describe releases it,
  and closing the conversation drops it. Nothing in this path writes an image to disk.
- Every request sets `store=False`. A description reaches the voice only as a `look` /
  `look_around` / `find` result and nowhere else: the daemon log records that a view was
  described and when (`scene: [vision 16:43:05] view`), and whether a `look` / `find`
  worked — never what the camera saw.
- Separate features with their own switches do send frames outside conversations: the idle
  explorer's notes and the diary (`CC_BUDDY_EXPLORE`, [personality.md](personality.md)).
  `CC_BUDDY_SCENE=0` turns this feature off entirely.

## Knobs

| Variable | Default | Purpose |
|---|---|---|
| `CC_BUDDY_SCENE` | on | `0`: no camera descriptions for the voice |
| `CC_BUDDY_SCENE_MODEL` | `gpt-5.4-nano` | the image model that describes and locates |
| `CC_BUDDY_SCENE_INTERVAL_SECS` | `3` | minimum gap between two describes (1-60) |
| `CC_BUDDY_SCENE_STALE_SECS` | `15` | a view older than this is reported as stale (3-300) |
| `CC_BUDDY_INTENT` | on | `0`: the phrase table only, no classifier call |
| `CC_BUDDY_INTENT_MODEL` | `gpt-5.4-nano` | the goodbye / mute classifier |
| `CC_BUDDY_SOUND_FILE` | `~/.config/cc-buddy-bridge/sound.json` | where the mute choice is kept |

## Commands

```bash
cc-buddy-bridge scene-test photo.jpg --find "a mug"   # one real describe (+ locate), timed
cc-buddy-bridge intent-test --expect leave "I'm heading out" --expect stay "leave it open"
cc-buddy-bridge sound off        # mute; `on` to unmute, no argument to see which
```

## Needs a reflash

Head holds through a conversation, the ±120° range for voice requests, and the sound command
are firmware changes (`hostlook.h`, `body.cpp`, `gaze.cpp`, `xfer.h`). Flash with
`tools/flash_stackchan.sh`. Without it, `move_head` still sends the pose, but the old firmware
refuses it in some states, clamps it to ±60° and recentres on the next phase change.
