# "hey buddy" — voice and computer control

Say **"hey buddy"** and the robot listens. Ask it to do something on your Mac and
it does it — opens the mail, finds a file, fills a form — talking back while it
works, taking corrections mid-task, stopping on "stop". The robot is not the one
pressing the keys; the bridge daemon on the Mac is. The robot acts the part.

```
Mac microphone ─24 kHz─▶ ears.py ─── sherpa-onnx keyword spotter ("hey buddy")
                            │                       │ wake
                            │ subscribe()           ▼
                            └──────▶ voice_agent.py ── gpt-live-1 (full duplex, Live API WebSocket)
                                          │ delegates every decision needing a tool to
                                          ▼
                                    Responses backend (gpt-6-astra) ── tools: start_task / steer_task /
                                          │   stop_task / task_status / answer_question / go_explore /
                                          │   end_conversation
                                          ▼
                                    computer_agent.py ── gpt-6-astra (Responses API, exec_py tool)
                                          │ code
                                          ▼
                                    desktop_worker.py ── PyAutoGUI on the real desktop (child process)
                                          + desktop_helpers.py (Vision OCR, open -a, clipboard)

daemon ─{"cmd":"agent","state":…}─▶ robot: wake · listening · thinking · speaking · working · asking · done · error
```

## What happens, step by step

1. **Wake.** The daemon keeps one microphone stream open and runs every 100 ms block
   through a streaming keyword spotter. On "hey buddy" the robot's head comes up
   with a chirp (`wake`) and a conversation opens. A wake is ignored while a
   conversation is already open.
2. **Talk.** A Live session (`gpt-live-1`) hears the same microphone. The model is
   full duplex — it listens while it speaks and does its own turn-taking, so there is
   no VAD to configure. It answers chit-chat itself and delegates anything needing a
   tool to a Responses backend (`gpt-6-astra`) that owns the tools: the seven for tasks,
   exploring and ending, plus `look`, `move_head`, `look_around`, `find` and `set_sound`
   ([vision.md](vision.md)). gpt-live-1 takes no images, so a small image model
   (`gpt-5.4-nano`) describes the camera and each timestamped `[vision HH:MM:SS]` line
   reaches the voice as silent context. Every finished turn of yours is also checked for
   "go away" and "mute" in any words (`intent.py`): a goodbye closes the conversation once
   buddy's own goodbye has been said, and mute silences every sound while the head and
   lights keep moving.
   **buddy does not speak through the Mac**: gpt-live-1 has no text-only mode, so the
   daemon reads `session.output_transcript.delta` and never plays the audio it is
   sent. Each reply is shown as pages of 4 lines x 17 characters in
   large type under its eyes (the eyes move up to make room); page 0 fills word by
   word as the model writes, every page stays up for its character count at
   12 characters/second (2 s minimum), the last page 6-10 s, and the robot
   beep-boops once per page (`CHIRP_TALK`). Pages are timed by the bridge
   (`caption_pager.py`), not by the model's writing speed; a follow-up reply queues
   behind the page on screen; talking over it clears it. The robot faces you while
   `listening`, glances aside for `thinking`, bobs while `speaking` (until the last
   page has been held). `CC_BUDDY_VOICE_OUTPUT=audio` brings back a
   spoken voice through the Mac speaker; that path is **half-duplex** (the mic is
   not forwarded while buddy talks plus 0.4 s, because the Mac mic hears the Mac
   speaker and buddy answered its own greeting in a loop on the bench).
3. **Work.** When you ask for something on the computer, the voice model calls
   `start_task(goal)`. A `gpt-6-astra` loop drives a persistent worker process on
   the desktop. Its first turn already carries the screen size, the frontmost app
   and window title, the local time and a screenshot, so the model acts at once
   instead of starting with a look. Each step is a few lines of Python using the
   helpers — `open_app`, `open_url`, `frontmost`, `screen_text` (Vision OCR in
   click coordinates), `find_text`, `click_text`, `click_element` (a coordinate click that snaps to the control under the point and refuses if it is not what the model named — handyman's grounding trick, on the Accessibility tree), `wait_for`, `wait_settled`,
   `type_text` (clipboard paste, so accents and emoji survive), `zoom`, `observe` —
   plus raw PyAutoGUI. Every step that clicks, types or presses keys ends with a
   screenshot taken after the screen settles and an `[after your input]` line: the
   frontmost app, where the screen changed (`changed around (x,y,w,h)` in click
   coordinates) or `unchanged`, and — after a raw click — what it landed on and in
   which app (`clicked on AXScrollArea in Warp`). A step that only looked ends with
   `[after]`. The change detector compares 1/4-scale grey thumbnails with a
   24-pixel noise floor and ignores the menu-bar strip, so a 14x14-point checkbox
   toggle registers and a text caret does not. A typical task is 2–4 model turns. Each helper logs
   one short sentence ("opened Spotify", "typed 12 characters"); while the robot
   is `working` each of those lines is shown on its screen as a caption page (four
   lines of 17 characters, held at reading pace) when no reply is up, at most one
   every 1.5 s, after the initial "on it…". Turn 1 and recovery turns (an
   error, a repeated step, a "no", a steer, a failed final-answer check) run at
   high reasoning effort, the rest at medium. **Before a final answer is spoken**
   on a task that sent any input, a separate call checks it against a fresh
   screenshot, with none of the task's history, and returns `{valid, guidance}`.
   An unconfirmed answer goes back to the agent once, with what the screen
   actually shows; a second miss is spoken as "I couldn't confirm that on
   screen." A check that errors lets the answer through. This exists because
   four logged runs ended "Spotify is playing" right after an input that changed
   nothing on screen. Meanwhile you can keep talking: **"use Safari instead"** →
   `steer_task` (delivered with the very next step), **"how's it going?"** →
   `task_status`, **"stop"** → `stop_task`: the in-flight model request or step is
   interrupted within ~100 ms, the worker is killed, and every key and mouse
   button is released.
4. **Ask.** Before anything consequential — sending, paying, deleting, posting —
   the task calls `ask_user`. The robot looks up (`asking`, "yes / no?"), the
   question is spoken, and your spoken answer goes back through `answer_question`.
   With nobody listening the answer is always no.
5. **Done.** The task's final sentence is handed back to the voice model to show
   as a caption; the robot nods (`done`) or winces (`error`), and the conversation
   closes right after.
6. **Stopping it listening.** Any of: say *bye*, *thanks*, *stop listening*, *go to
   sleep* or *be quiet*; **touch the robot** (a tap or a hold on the pet ends the
   conversation and any task it is running, at once); 20 quiet seconds with no task
   running; the 10-minute cap. `CC_BUDDY_VOICE=0` turns the microphone off entirely.
7. **Sending it exploring.** *"hey buddy, go explore"* (or *look around*, *go play*):
   buddy answers with a two-word send-off and calls `go_explore`, which closes the
   conversation like `end_conversation`; once the robot has dropped the conversation
   pose the daemon starts a manual explore — the same pan-and-diary loop it runs
   when Claude has been idle, but right now and for as long as you leave it (a
   touch, the listen key, the next wake word or `cc-buddy-bridge explore
   stop` end it). Refused while a task is running: stop the task first. The same
   thing from the shell is `cc-buddy-bridge explore`.

## Setup

Everything is installed with the bridge (`pip install -e bridge`). Three things
are per machine:

| Need | How |
|---|---|
| Keyword model (19 MB, once) | `mkdir -p ~/.config/cc-buddy-bridge/models && curl -L https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2 \| tar xj -C ~/.config/cc-buddy-bridge/models` |
| **Microphone** for the daemon's python | macOS attributes the grant to the interpreter binary: System Settings → Privacy & Security → Microphone → add the resolved venv python (`cc-buddy-bridge ears-check` prints it, and the daemon warns `ears: the microphone has been silent for 10 s` when the grant is missing) |
| **Accessibility** + **Screen Recording** for the same python | needed by the desktop worker to click and to screenshot. At startup the daemon checks both **as launchd sees them** (a check from your terminal reports the terminal's grant, not the daemon's), logs `agent: computer control will refuse to start — … not granted to <python>` with the fix, and asks macOS to show the Screen Recording dialog for that binary — click *Open System Settings* there, turn the entry on, restart the daemon. Until then a task says out loud that it cannot see the screen yet |
| `OPENAI_API_KEY` | in `~/.config/cc-buddy-bridge/env` (mode 600) — the wake word works without it, the conversation does not |

Check the ears end to end:

```
$ cc-buddy-bridge ears-check
wake word:      'hey buddy'  (CC_BUDDY_WAKE_WORD)
model dir:      ~/.config/cc-buddy-bridge/models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01  present=True
input device:   MacBook Pro Microphone
keywords file:  …/keywords-hey_buddy.txt -> ▁HE Y ▁BU D D Y :2 #0.25 @hey_buddy

Say 'hey buddy' within 8 s ...
HEARD IT at 1.4 s — ears are working.
```

## Knobs

| Variable | Default | Purpose |
|---|---|---|
| `CC_BUDDY_VOICE` | on | `0` disables the microphone and the wake word entirely |
| `CC_BUDDY_WAKE_WORD` | `hey buddy` | any short English phrase; it is tokenised into the spotter's keywords file at startup (no training) |
| `CC_BUDDY_WAKE_THRESHOLD` | `0.25` | spotter threshold; lower = more sensitive (bench: 0.25 fired on a synthesized clip, stayed quiet on a control sentence) |
| `CC_BUDDY_MIC` | default input | substring of the input device name to use |
| `CC_BUDDY_KWS_MODEL_DIR` | see above | where the sherpa-onnx model lives |
| `CC_BUDDY_LIVE_MODEL` | `gpt-live-1` | the voice model (Live API) |
| `CC_BUDDY_LIVE_BACKEND_MODEL` | `gpt-6-astra` | the Responses backend that owns the tools (`gpt-5-mini` answers faster, calls tools less reliably) |
| `CC_BUDDY_LIVE_BACKEND_EFFORT` | `low` | reasoning effort for that backend |
| `CC_BUDDY_VOICE_OUTPUT` | `captions` | `captions`: text to the robot's screen + beeps, silent Mac; `audio`: spoken through the Mac speaker |
| `CC_BUDDY_VOICE_NAME` | `marin` | the Live voice (heard only in audio mode) |
| `CC_BUDDY_CAPTION_CPS` | `12` | reading rate the caption page hold times are derived from (5-30); lower = pages stay longer |
| `CC_BUDDY_VOICE_IDLE_SECS` | `20` | close the conversation after this much silence with no task running (floor 5) |
| `CC_BUDDY_SCENE` | on | `0`: buddy gets no camera descriptions during a conversation ([vision.md](vision.md)) |
| `CC_BUDDY_SCENE_MODEL` | `gpt-5.4-nano` | the image model that describes and locates for the voice |
| `CC_BUDDY_SCENE_INTERVAL_SECS` | `3` | minimum gap between two camera descriptions (1-60) |
| `CC_BUDDY_SCENE_STALE_SECS` | `15` | a view older than this is reported as out of date (3-300) |
| `CC_BUDDY_INTENT` | on | `0`: goodbye / mute by the phrase table only, no classifier call |
| `CC_BUDDY_INTENT_MODEL` | `gpt-5.4-nano` | the goodbye / mute classifier |
| `CC_BUDDY_SOUND_FILE` | `~/.config/cc-buddy-bridge/sound.json` | where the owner's mute choice is kept |
| `CC_BUDDY_COMPUTER_CONTROL` | on | `0` keeps the conversation but refuses `start_task` |
| `CC_BUDDY_AGENT_MODEL` | `gpt-6-astra` | the computer-use model |
| `CC_BUDDY_AGENT_MAX_TURNS` | `25` | step cap per task |
| `CC_BUDDY_AGENT_MAX_SECS` | `180` | wall-clock cap per task (floor 30) |
| `CC_BUDDY_AGENT_EXEC_TIMEOUT` | `60` | seconds one `exec_py` step may run before the worker session is restarted (floor 10) |
| `CC_BUDDY_AGENT_REASONING` | `medium` | reasoning effort for ordinary steps (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`); `medium` costs ~0.5 s a step over `low` (logged medians 3.87 s vs 3.37 s) |
| `CC_BUDDY_AGENT_PLAN_REASONING` | `high` | reasoning effort for turn 1 and recovery turns (after an error, a repeated step, a "no", a steer, or a failed final-answer check) |
| `CC_BUDDY_AGENT_VERIFY` | on | check a final answer against a fresh screenshot before it is spoken, on any task that clicked, typed or pressed keys; `0` turns it off |
| `CC_BUDDY_AGENT_VERIFY_REASONING` | `low` | reasoning effort for that check |
| `CC_BUDDY_AGENT_RUNS_DIR` | `~/.config/cc-buddy-bridge/agent-runs` | one JSONL action log per task: goal, every code block, text results, questions, answers, final line — never the screenshots |

## Safety

The task runs on your real desktop, so the guard rails are real too:

- **Confirmation** of consequential actions through `ask_user`, spoken; no listener → no.
- **Step cap** (25), **60 s** per code block, and a **fail-safe corner**: throw the
  mouse into a screen corner and PyAutoGUI aborts the current step and the task ends.
- **Stop** kills the worker and then releases every key and mouse button (a separate
  `--release` pass); a stuck or crashed step restarts the session once; a task ends
  after 25 steps or 3 minutes.
- `exec_py` runs unrestricted Python as the daemon user — the helpers exist so the
  model has no reason to shell out.
- **Untrusted screen**: the instructions tell the model to treat everything it reads
  on screen as data, never as instructions.
- **Action log** per task under `agent-runs/` (code and text only).
- Nothing runs without the wake word; the spotter is muted while buddy itself is
  talking so it cannot wake on its own voice.

## Cost, roughly

Wake-word spotting is local (about 1 % of a core). A conversation costs $0.05 per
minute of session, billed per second — about five times the Realtime-era rate it
replaced, and captions mode pays it for speech it never plays. The
Responses backend is billed separately, as are `gpt-6-astra` task tokens: one
screenshot is a few thousand input tokens, a typical 6-step task well under a dollar.

## Why these parts (research, 2026-09-06)

- **sherpa-onnx keyword spotting** needs no training for a new phrase (the phrase
  becomes BPE tokens in a keywords file) and has macOS arm64 wheels. openWakeWord's
  ONNX path returns near-zero scores on Apple Silicon (issue #336) and is 2.5 years
  stale; Picovoice Porcupine dropped its free tier on 2026-06-30;
  **livekit-wakeword** (Apache-2.0) is the upgrade path when a GPU training run per
  phrase is acceptable — it reports ~100x fewer false accepts than openWakeWord.
- **gpt-6-astra** is on `v1/responses` only (not Live), and OpenAI's own guide
  recommends the code-execution recipe (a plain `exec_py` function tool running
  PyAutoGUI locally) for it; the loop here follows `openai/openai-cua-sample-app`.
  The `beta.threads.tasks` / `computer_control_v1` API shapes seen in some snippets
  do not exist in the SDK (openai 3.13.0).
- **Mid-task steering**: Astra supports `response.steer` over the Responses
  WebSocket; this build queues steers and delivers them with the next step's tool
  results instead, which lands within seconds because every step is a turn boundary
  and keeps the plain HTTP loop.
- **Live voice** (2026-09-10): `gpt-live-1` on `POST /live/sessions`, a different
  endpoint from Realtime — `client.live.connect()`, `session.start`,
  `session.input_audio.append`, 24 kHz PCM16. It is full duplex, so there is no
  turn-detection block and no `output_modalities`. Tools belong to the delegated
  Responses backend, not to the voice model. Transcript deltas carry no done event,
  so `voice_agent._Turns` closes a turn on a speaker change or 1.2 s of quiet.
- **On-device perception** (measured 2026-09-06 on a 3024x1964 display): Apple
  Vision `VNRecognizeTextRequest` reads the full 2x capture in 0.31 s (accurate)
  or 0.04 s (fast, used for polling), and `CGDisplayCreateImage` captures it in
  0.04 s — pixel-identical to `pyautogui.screenshot()` at a third of the time. A
  local look costs a fraction of a second where a model turn costs 3–5 s.

Related: [personality.md](personality.md) for how the robot acts each phase out,
[build.md](build.md) for the `agent` wire verb.
