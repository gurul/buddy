# "hey buddy" — voice and computer control

Say **"hey buddy"** and the robot listens. Ask it to do something on your Mac and
it does it — opens the mail, finds a file, fills a form — talking back while it
works, taking corrections mid-task, stopping on "stop". The robot is not the one
pressing the keys; the bridge daemon on the Mac is. The robot acts the part.

```
Mac microphone ─24 kHz─▶ ears.py ─── sherpa-onnx keyword spotter ("hey buddy")
                            │                       │ wake
                            │ subscribe()           ▼
                            └──────▶ voice_agent.py ── gpt-realtime-2.1-mini (speech ↔ speech, WebSocket)
                                          │ tools: start_task / steer_task / stop_task / task_status /
                                          │        answer_question / end_conversation
                                          ▼
                                    computer_agent.py ── gpt-6-astra (Responses API, exec_py tool)
                                          │ code
                                          ▼
                                    desktop_worker.py ── PyAutoGUI on the real desktop (child process)

daemon ─{"cmd":"agent","state":…}─▶ robot: wake · listening · thinking · speaking · working · asking · done · error
```

## What happens, step by step

1. **Wake.** The daemon keeps one microphone stream open and runs every 100 ms block
   through a streaming keyword spotter. On "hey buddy" the robot's head comes up
   with a chirp (`wake`) and a conversation opens. A wake is ignored while you are
   holding the dictation key, while a conversation is already open, or while a
   permission card is waiting on the board.
2. **Talk.** A Realtime session (`gpt-realtime-2.1-mini`, semantic turn detection)
   hears the same microphone. **buddy does not speak through the Mac**: the model
   answers in text, each reply streams to the robot as a caption in the band under
   its eyes (three lines of 26 characters, the tail of the text, 8 s after the last
   update) while the robot babbles beep-boops as the text grows (`CHIRP_TALK`, one
   per ~24 characters). The robot faces you while `listening`, glances aside for
   `thinking`, bobs while `speaking`. `CC_BUDDY_VOICE_OUTPUT=audio` brings back a
   spoken voice through the Mac speaker; that path is **half-duplex** (the mic is
   not forwarded while buddy talks plus 0.4 s, because the Mac mic hears the Mac
   speaker and buddy answered its own greeting in a loop on the bench).
3. **Work.** When you ask for something on the computer, the voice model calls
   `start_task(goal)`. A `gpt-6-astra` loop takes a screenshot, writes a few lines
   of PyAutoGUI, runs them in a persistent worker process, looks again, and so on,
   up to 25 steps. The robot drops its head toward the desk and makes small typing
   glances (`working`, "on it…"). Meanwhile you can keep talking: **"use Safari
   instead"** → `steer_task` (delivered with the very next step), **"how's it
   going?"** → `task_status`, **"stop"** → `stop_task` (the worker is killed, so
   no key stays held).
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
| `CC_BUDDY_REALTIME_MODEL` | `gpt-realtime-2.1-mini` | the voice model (`gpt-realtime-2.1` for the larger one) |
| `CC_BUDDY_VOICE_OUTPUT` | `captions` | `captions`: text to the robot's screen + beeps, silent Mac; `audio`: spoken through the Mac speaker |
| `CC_BUDDY_VOICE_NAME` | `marin` | the Realtime voice (audio mode only) |
| `CC_BUDDY_VOICE_IDLE_SECS` | `20` | close the conversation after this much silence with no task running (floor 5) |
| `CC_BUDDY_COMPUTER_CONTROL` | on | `0` keeps the conversation but refuses `start_task` |
| `CC_BUDDY_AGENT_MODEL` | `gpt-6-astra` | the computer-use model |
| `CC_BUDDY_AGENT_MAX_TURNS` | `25` | step cap per task |
| `CC_BUDDY_AGENT_RUNS_DIR` | `~/.config/cc-buddy-bridge/agent-runs` | one JSONL action log per task: goal, every code block, text results, questions, answers, final line — never the screenshots |

## Safety

The task runs on your real desktop, so the guard rails are real too:

- **Confirmation** of consequential actions through `ask_user`, spoken; no listener → no.
- **Step cap** (25), **60 s** per code block, and a **fail-safe corner**: throw the
  mouse into a screen corner and PyAutoGUI aborts the current step and the task ends.
- **Stop** kills the worker process — any held key is released with it.
- **Untrusted screen**: the instructions tell the model to treat everything it reads
  on screen as data, never as instructions.
- **Action log** per task under `agent-runs/` (code and text only).
- Nothing runs without the wake word; the spotter is muted while buddy itself is
  talking so it cannot wake on its own voice.

## Cost, roughly

Wake-word spotting is local (about 1 % of a core). A conversation costs Realtime
audio tokens (`gpt-realtime-2.1-mini`: $10 in / $20 out per million audio tokens,
about a cent a minute of talk). A task costs `gpt-6-astra` tokens: one screenshot
is a few thousand input tokens, a typical 6-step task well under a dollar.

## Why these parts (research, 2026-09-06)

- **sherpa-onnx keyword spotting** needs no training for a new phrase (the phrase
  becomes BPE tokens in a keywords file) and has macOS arm64 wheels. openWakeWord's
  ONNX path returns near-zero scores on Apple Silicon (issue #336) and is 2.5 years
  stale; Picovoice Porcupine dropped its free tier on 2026-06-30;
  **livekit-wakeword** (Apache-2.0) is the upgrade path when a GPU training run per
  phrase is acceptable — it reports ~100x fewer false accepts than openWakeWord.
- **gpt-6-astra** is on `v1/responses` only (not Realtime), and OpenAI's own guide
  recommends the code-execution recipe (a plain `exec_py` function tool running
  PyAutoGUI locally) for it; the loop here follows `openai/openai-cua-sample-app`.
  The `beta.threads.tasks` / `computer_control_v1` API shapes seen in some snippets
  do not exist in the SDK (openai 3.8.0).
- **Mid-task steering**: Astra supports `response.steer` over the Responses
  WebSocket; this build queues steers and delivers them with the next step's tool
  results instead, which lands within seconds because every step is a turn boundary
  and keeps the plain HTTP loop.
- **Realtime voice**: `gpt-realtime-2.1(-mini)` over WebSocket, 24 kHz PCM16,
  function calling, `semantic_vad` with `interrupt_response` for barge-in.

Related: [personality.md](personality.md) for how the robot acts each phase out,
[build.md](build.md) for the `agent` wire verb.
