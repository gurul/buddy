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

## Taking a photo on request

"Hey buddy, take a picture of this" (or *a photo*, *snap this*, *remember what this looks
like*) goes to the backend, which calls `take_photo` with the owner's words as the note. The
daemon asks the board for one full-size frame (`{"cmd":"snap"}`, the same request the diary
uses for a photo it finds cool) and falls back to the newest streamed frame when the board
cannot answer. The picture is kept by `DiaryTaker.keep`: **outside** the explorer's photo
budget and habituation gates, because the owner's judgment outranks buddy's, written to
today's diary at once as *"A picture you asked for: …"* with the image line under it, then
looked at properly so the caption names what is in it. The tool returns that caption and the
voice says it back ("Kept it: the red bike by the door"). With the robot unplugged, or no
frame within 20 s, it returns why and the voice says that instead of claiming a photo.
`cc-buddy-bridge photos open` shows the newest one.

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

## Following whoever is talking

Face detection runs on the Mac; camera frames and head motion run on the robot.
If frames arrive but every detection logs `NSInvalidArgumentException - key does
not exist`, update the bridge and restart its daemon. The 2026-09-22 fix passes
native `nil` options to Vision for both face detection and owner feature prints;
an empty Python dictionary triggered this exception on the current Mac.
`tests/test_vision_native.py` exercises both real Vision requests on macOS.

In a conversation buddy keeps its eyes on the person it is talking with, and works out where they
went when it loses them (`follow.py`). Before 2026-09-21 it did neither: the board live-tracks only
the **owner**, only in its attention and dictation states, and in a conversation it went to one
fixed "facing you" pose. Lean back, stand up or walk round the desk and it kept addressing the
chair; a guest was never followed at all.

Nothing new is sensed. The Mac already finds every face in every camera frame; the follower is the
policy for a conversation, on the host, and needs no reflash, because the board accepts a host pose
in any state while a conversation is open and that pose owns the head until its hold runs out
(`src/hostlook.h`).

**It follows a person, not a sighting.** The first version turned toward every face box, and on a real
face it swung ±20° around someone sitting still. The offsets logged with each move showed why: a frame
reaches the Mac about half a second after it was taken (JPEG on the board, 115200 baud, a Vision
request), so a frame "0.6 s after the move" was still a picture from mid-swing. So there is a track: a
position and a velocity in absolute head angles; nothing counts as evidence for 1.2 s after a move; a
sighting must agree (within 14°) with where the track expects the person, and one that does not is an
outlier until a second says the same; moves are at most 20°, aimed 0.35 s ahead along the velocity,
inside a 6° / 5° deadband, with a 4 s hold renewed every 2.5 s while the face sits still.

**When a conversation opens on an empty frame** it does not wait at the board's fixed pose hoping. The
owner sits 23° to buddy's left and a little below level, and from the fixed pose his face was in a
quarter of the frames, mostly at the edge. After 1.5 s with no confirmed face it looks, once, at the
place the map says people most often are, and follows from there. That look is not a search and is
never spent once someone has been followed.

**When it loses them it reasons, likeliest first:**

| It looks | Because |
|---|---|
| where they were heading, one field-of-view step and then another | the last sightings were moving (≥ 10°/s) or the face left by a frame edge; someone who leaves fast is followed after 0.8 s, not the ordinary 2.5 s |
| where they usually are | a decayed map of the angles at which faces have been followed (`~/.config/cc-buddy-bridge/presence.json`, 10° cells, a 14-day half-life, angles only), learned across conversations and restarts: at a desk, "the chair" and "standing" |
| either side of where they were | a sideways shift is the commonest way to leave a frame, and this is what works before the map has learned anything |
| up, then down, at the same heading | they stood up, or sat back |

Then it gives the head back, and it does not search again until it has really *seen* someone again:
one search per loss, at most six looks (about 10 s), at most three searches a conversation. The first
live run searched three times back to back at an empty chair, which is how an anxious robot behaves.

**What else it uses:** the conversation's phase (it follows in `wake`, `listening`, `asking`,
`speaking`, and hands the head back at once for `thinking`'s glance aside and `working`'s head-down,
so buddy keeps its own expressions); every face in the frame, not just the largest (it stays with the
one nearest its track, so a passer-by does not steal the gaze); and who else owns the head — "look
left", `look_around` and `find` for as long as they hold it, an explore, the dictation key, a lesson's
listening pose.

**It tells the memory bus.** Every change is one event on `/buddy/presence` — `seen`, `lost` (and
whether they were leaving), `found` and `how` ("where they were heading", "where they usually are",
"either side", "up and down"), with the angles and the speed — so Foxglove, roslibjs or
`cc-buddy-bridge memory tail` can watch buddy's sense of where its person is
([memory-bus.md](../memory-bus.md)). The control loop reads the local map, never the bus or
claude-mem: a head cannot wait on an HTTP recall. The topic is not stored in claude-mem; it is a
live signal, not a memory.

The log says what it did: `follow: → yaw -20 pitch 39 (face bx=-57 by=12; track -23/38 moving 2°/s)`,
`follow: lost them 0.9 s ago (moving 31°/s) — looking: where they were heading, then …`,
`follow: found them again (where they were heading) at yaw 64 pitch 47`, `follow: let go (phase thinking)`.

### The eyes lead the head (needs a reflash)

The neck acts about a second after you move; eyes need not. The board already receives the face's
offset in every frame, so `gazeEyeLeadYawDeg()` / `gazeEyeLeadPitchDeg()` (`src/gaze.cpp`) add twice
that offset to the head's own angles when the eyes are aimed (`main.cpp`): someone 10° off-centre
already crosses the eyes' ±20° band and gets a glance at once, the neck follows, and the lead falls
back to zero as the face comes to the centre. Any face, owner or guest; nothing for 700 ms with no
report. The eyes have compass positions (N, NE, E, W, NW and the default), not a continuous gaze, so
this is a glance to a side, not a pupil that tracks.

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
| `CC_BUDDY_HEAD_MODEL` | `off` | `jev`: spoken head poses are chosen by Jev in ~0.25 s, the backend remains the fallback ([routing.md](routing.md#head-moves)) |
| `CC_BUDDY_FOLLOW_SPEAKER` | on | `0`: in a conversation the head stays at the board's fixed pose instead of following the speaker's face |
| `CC_BUDDY_PRESENCE_FILE` | `~/.config/cc-buddy-bridge/presence.json` | where the follower keeps its map of where people usually are (angles only) |
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
