<p align="center">
  <img src="docs/assets/buddy-sidekick.png" alt="buddy, your desktop sidekick: a cut-paper illustration of the buddy robot saying Hey there!" width="100%">
</p>

# buddy

**A small robot that lives on your desk, with a Mac as its host.** buddy listens for
“hey buddy,” follows the person talking to it, helps with computer tasks, and keeps a
diary of what it notices. Its eyes, head, LEDs and chirps express what it is doing
and how it feels. It also has a learning workspace: a whiteboard and a tutor that
helps you work through a problem one step at a time.

The current robot is an **M5StackChan K151 (CoreS3 / ESP32-S3)** running custom
Arduino firmware, connected over USB serial to a Python daemon. A native macOS app
and desktop widget show its diary, memories and lessons. You can try the learning
workspace without the robot, and its offline demo needs no API key.

- [What buddy does](#what-buddy-does)
- [Learning workspace](#learning-workspace)
- [Get started](#get-started)
- [Technical summary](#technical-summary)
- [Data and controls](#data-and-controls)
- [Repository guide](#repository-guide)
- [Development](#development)
- [Credits](#credits)

## What buddy does

- **Listens and responds.** Local wake-word detection starts a voice session. By
  default, replies appear as captions on the robot while it chirps; spoken audio
  through the Mac is optional. Web search and a reasoning backend handle questions
  that need more than a quick reply.
- **Uses your Mac.** Simple app launches and web searches run through code shortcuts.
  Labelled controls can be handled through macOS Accessibility; more complex tasks
  use a model-driven screenshot and Python loop. You can redirect a task while it
  runs or say “stop.” Consequential actions are routed to the planner, which is
  instructed to ask before proceeding.
- **Sees and moves.** Host-side face detection helps it follow a conversation
  partner, distinguish its owner, and remember where they usually sit. It can look
  for an object, explore the room, take a photo on request, or dance.
- **Has moods and a diary.** An on-board affect engine drives expressions, movement,
  LED patterns and chirps. The host keeps observations, selected thoughts and
  photos, a room/person profile, and nightly reflections. Conversation memories
  are stored separately as distilled notes; “remember that” promotes a spoken fact.
- **Takes notes.** Start a room-note session with `cc-buddy-bridge take-notes start`;
  stopping it produces a write-up of decisions, actions and open questions.
- **Mirrors Claude Code.** Session hooks and transcript updates make buddy sleep,
  work, celebrate, or ask for attention. A tap can focus a waiting terminal;
  Claude Code permission prompts remain in that terminal.
- **Shows its day.** The macOS menu-bar app, diary window and WidgetKit extension
  expose thoughts, photos, feelings, conversation notes and saved lessons, with
  microphone and daemon power controls.

## Learning workspace

<p align="center">
  <img src="docs/assets/buddy-lesson.png" alt="A buddy lesson with a problem, whiteboard, hint, and controls to check work or show one step" width="80%">
</p>

Learn a topic at a level you describe (“Grade 4” or “I know Python, new to Rust”),
or bring a written problem or screenshot. buddy confirms an imported problem
before you start. Work on the **tldraw whiteboard**, type your ideas, or think out
loud through the robot's voice session.

The tutor is prompted to give a hint, check the first incorrect step, or show
exactly one next step. Its explanations stay beside your work. A final step may
finish the problem; the workflow is designed to avoid dumping the whole solution
at once. Lessons, board revisions and feedback are saved to a searchable dashboard.

The browser, voice agent and CLI share the same lesson when the daemon is running:

```bash
cc-buddy-bridge lesson start --mode learn --topic "adding fractions" --level "Grade 4"
cc-buddy-bridge lesson ideas --text "I think I add the tops"
cc-buddy-bridge lesson hint
cc-buddy-bridge lesson check
cc-buddy-bridge lesson step
```

Say “lesson” to open the workspace, or ask “hey buddy, teach me something.” Live
lessons use OpenAI by default, with OpenRouter opt-in and optional Exa practice
references. The offline demo uses fixed math examples and does not recognize
handwriting or images. See [the learning guide](docs/learning.md) for the complete
workflow, think-out-loud mode, configuration and current limits.

## Get started

### Try the learning workspace without hardware

From the repository root, with **Python 3.11+**:

```bash
python3 tools/start_learning.py --demo --data-dir .demo-data --port 48767
```

Open [the demo](http://127.0.0.1:48767/) and choose **Play workflow demo**, or work
through an example yourself. No bridge installation or API key is required.
There is also a [recorded walkthrough](docs/learning-demo/buddy-learning-demo.webm).

For live tutoring, put `OPENAI_API_KEY=...` in
`~/.config/cc-buddy-bridge/env`, then run:

```bash
python3 tools/start_learning.py
```

This serves [the learning dashboard](http://127.0.0.1:48766/). If the bridge daemon
already serves that port, open its dashboard instead of starting a second server.
Standalone mode provides tutoring and the board; robot voice requires the daemon.
Use `--env-file /path/to/env` if the settings file is elsewhere, including in WSL.

### Set up the Mac host

The full robot experience targets **macOS**; the bridge declares Python 3.11+,
with Python 3.12 used in the setup below. macOS Vision, Accessibility and the native
widget are platform-specific. The bridge also contains Linux/Windows service and
legacy BLE support; those do not provide the complete Mac experience.

Run these commands from the repository root:

```bash
python3.12 -m venv bridge/.venv
bridge/.venv/bin/python -m pip install -e ./bridge

mkdir -p ~/.config/cc-buddy-bridge/models
curl -fL \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2 \
  | tar xj -C ~/.config/cc-buddy-bridge/models

touch ~/.config/cc-buddy-bridge/env
chmod 600 ~/.config/cc-buddy-bridge/env
```

Edit that environment file to add your credentials and settings, preserving any
existing entries:

```dotenv
OPENAI_API_KEY=your-key-here
# Optional: practice references for live lessons
# EXA_API_KEY=your-key-here
```

Then install the Claude Code hooks and the login service:

```bash
bridge/.venv/bin/cc-buddy-bridge install
bridge/.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'
```

Grant the daemon's Python **Microphone**, **Accessibility** and **Screen Recording**
in System Settings → Privacy & Security. The daemon logs the binary that needs
permission. Restart the daemon after changing its environment file.

### Build and connect the robot

Use the [firmware build guide](docs/stackchan/build.md) to install the Arduino
libraries and back up the factory firmware before the first flash. The build uses
ESP32 core **3.3.10**, M5Unified/M5GFX, RoboEyes, AnimatedGIF and StackChan-BSP.
**StackChan-BSP must include commit `8d4d6fc`**: the `1.1.0` tag lacks
`TouchSensor.recalibrate()` and does not compile this firmware. Reference clones
under `vendor/` are gitignored; see [dependency setup](docs/stackchan/repos.md).

Once those prerequisites are installed, from the repository root:

```bash
arduino-cli core install esp32:esp32@3.3.10
./tools/flash_stackchan.sh
```

The flash script compiles the firmware, archives its ELF, releases the serial port
from the login service, uploads, and restarts that service if it was running.
Connect the robot by USB, then check the wake word:

```bash
bridge/.venv/bin/cc-buddy-bridge ears-check
# Say “hey buddy”.
tail -f ~/Library/Logs/cc-buddy-bridge.log
```

The speaker amplifier stays off between chirps to prevent idle hiss or whine.
If an older build makes noise between beeps, reflash with the command above.

Try “hey buddy, what time is it?”, “hey buddy, open Safari”, or “hey buddy, go
explore.” For the desktop widget and diary app, follow the separate
[widget build and signing instructions](docs/stackchan/widget.md) (macOS 14+).
If buddy wakes but stalls, see the [voice troubleshooting guide](docs/stackchan/voice.md#when-buddy-hears-you-and-then-sits-still).

## Technical summary

buddy splits real-time embodiment from host-side perception, storage and model
calls. The firmware keeps the face and body responsive; the Python daemon
coordinates voice, tasks, lessons and memory.

```text
Mac microphone → local wake-word detector → voice session + reasoning backend
                                                   │
Claude Code hooks / CLI ── local IPC ──→ Python daemon
                                                   ├─ task router → desktop worker
                                                   ├─ learning service → browser whiteboard
                                                   ├─ diary / conversation memory → macOS app + widget
                                                   └─ memory bus → optional rosbridge / claude-mem
                                                   │
                                             USB serial (NDJSON)
                                                   │
                                      StackChan firmware: eyes, head,
                                      LEDs, chirps, touch and camera
                                                   │
                                      camera frames → host vision / diary
```

| Component | Implementation and responsibility |
|---|---|
| Robot firmware | Arduino C++ on ESP32-S3; M5StackChan BSP and M5Unified for hardware, RoboEyes for the face. Local state machines handle gaze, affect, conversation phases, motion and synthesized chirps. |
| Host bridge | Python with `asyncio`; `cc-buddy-bridge` is the CLI and daemon entry point. Hooks and CLI commands use local JSON IPC; the current robot link uses newline-delimited JSON over USB serial. |
| Voice and reasoning | sherpa-onnx keyword spotting with sounddevice audio input; the configured defaults are `gpt-live-1` for voice and `gpt-6-astra` for its reasoning backend. Captions are the default output. |
| Desktop control | A tiered request router, macOS Accessibility inspection, and a separate worker process for PyAutoGUI/Pillow operations. The fallback planner uses `gpt-6-astra`, with a default limit of 25 turns and 180 seconds. |
| Vision and memory | macOS Vision for face detection, host-side identity/following logic, model-assisted scene observations and reflections, plus separate diary and conversation stores. |
| Learning | Python HTTP service on `127.0.0.1:48766`, SQLite persistence, and a React/TypeScript tldraw canvas built with Vite. Tutor responses use a validated JSON shape for problems, feedback, steps and completion state. |
| Native UI | SwiftUI menu-bar app and diary window, with a WidgetKit extension. The helper mirrors local data into an App Group snapshot for the widget. |
| Memory integrations | In-process publish/subscribe bus; optional rosbridge-compatible WebSocket endpoint and claude-mem sink/recall integration. Neither external integration is required. |

### How a computer request is routed

1. **Code shortcuts** handle recognized app launches and web searches (`task_router.py`).
2. **Accessibility routing** handles requests fully described by a labelled UI control
   (`lane_router.py` over `fast_lane.py` and `ax_candidates.py`).
3. **Plan once, execute with Jev** (optional, `CC_BUDDY_PLAN_EXEC=1`): the planner is asked
   once for a typed plan (`plan_contract.py`), and `plan_executor.py` walks it with no planner
   turn between steps or at the end. Each click is grounded on a fresh Accessibility snapshot
   by the keyword gate and hosted Jev (`typed_ask.ask_jev_step`). Consequential steps stop and
   ask you; a plan cannot approve them.
4. **The planner** handles remaining work turn by turn using screenshots and Python desktop
   helpers. It is also the floor under tier 3: a plan that cannot be made or finished falls
   back to it with a note of what was already done.

The first two routes are enabled by default. Tier 3 and the separate planner-delegated fast
lane (`decider.py`, the `[fast]` extra) are **off by default**. Optional typed-decision backends include **local Laya
via MLX** on Apple silicon and **hosted Jev**. Jev can also be enabled for narrowly
scoped launch routing and spoken head movements. These options have different
capabilities and data flows; they are not required for the default setup.
A **browser lane** (`browser_lane.py`, off by default) gives web goals a better body: Playwright
drives buddy's own Chromium, the page's controls become the candidates, and the same
plan executor, keyword gate and Jev decide each step ([routing](docs/stackchan/routing.md#the-browser-lane-playwright-as-the-hands-jev-as-the-judge)).
See [routing](docs/stackchan/routing.md) for the switches and measured evaluations,
and [voice and computer control](docs/stackchan/voice.md) for worker details.

Away from the desk, the same agent can be reached by text: `telegram.py` long-polls
the Telegram Bot API (outbound HTTPS only, no open port) and lets one allowlisted
numeric Telegram id chat with buddy, start and stop a Mac task, answer a task's
question and receive a photo from the robot. "claude on" turns the chat into a Claude
Code terminal: what it runs, says and asks streams to the phone, what you text is typed
in, and tool calls go through without asking, as in bypass mode. It is **off by
default** and needs a switch, a bot token and an owner id together. With `CC_BUDDY_RECORDS=1` the text brain
also gets a memory layer (`records.py`): typed, git-tracked markdown records the owner
can edit, a profile one-pager, and a read-only keyword search — written only by a nightly
reconcile, never by the agent. See [text buddy through Telegram](docs/stackchan/telegram.md).

A [local Laya expression-tuning study](docs/stackchan/laya-emotion/README.md)
tests context-sensitive eyes and chirps. It includes trained experimental weights,
reproducible evaluations, and research references. It remains offline: tuning did
not improve fresh-scenario accuracy, and its confidence gate abstains.

## Data and controls

Wake-word detection runs locally. Live voice, tutoring, reasoning and scene
analysis use configured model providers; relevant audio, text, board images or
camera frames are sent for those requests. The desktop planner receives screenshots.
Local storage does not make those features offline. With the Telegram door on, what
you text and what buddy replies also pass through Telegram's servers.

Default persistent data lives under `~/.config/cc-buddy-bridge/`:

| Location | Contents |
|---|---|
| `env` | API credentials and runtime settings |
| `notes/` | Diary Markdown, observation JSONL, profiles, highlights and photo records |
| `debrief/` | Distilled conversation memories and promoted spoken facts; with records on, a git repository holding `records/` |
| `learning/` | `lessons.sqlite3` and saved lesson/whiteboard data |
| `agent-runs/` | Desktop-task run logs |

`cc-buddy-bridge mic off` disables microphone capture until re-enabled. The
menu-bar app and desktop widget also provide a persistent **Turn buddy off / on**
control for the daemon.

Set `CC_BUDDY_ROSBRIDGE=1` to expose memory events at `ws://127.0.0.1:9090`, or
`CC_BUDDY_CLAUDE_MEM=1` to save them to a local claude-mem worker. Both are off by
default. The event bus excludes conversation transcripts and learner input such
as ideas, strokes and images; lesson events contain metadata and buddy's feedback.
This is separate from the context sent to the live tutor. The rosbridge endpoint
has no authentication and defaults to loopback. See [memory-bus.md](docs/memory-bus.md)
for topics, recall, delivery limits and configuration.

## Repository guide

| Path | What is here | Documentation |
|---|---|---|
| `firmware/claude_pet_stackchan/` | Active StackChan firmware and C++ host tests | [Build and protocol](docs/stackchan/build.md), [design](DESIGN.md) |
| `bridge/src/cc_buddy_bridge/` | Daemon, CLI, voice, desktop control, vision and memory | [Bridge reference](bridge/README.md), [voice](docs/stackchan/voice.md), [routing](docs/stackchan/routing.md), [Telegram](docs/stackchan/telegram.md) |
| `bridge/src/cc_buddy_bridge/learning/` | Lesson service, tutor, SQLite store and served web assets | [Learning guide](docs/learning.md) |
| `bridge/web-canvas/` | React/TypeScript whiteboard source and browser checks | [Whiteboard details](docs/learning.md#the-whiteboard-tldraw) |
| `bridge/tests/`, `bridge/tools/` | Python tests, fixtures and routing/desktop evaluation tools | [Routing evaluations](docs/stackchan/routing.md) |
| `widget/` | SwiftUI app, shared data readers, WidgetKit extension and Xcode project | [Widget setup](docs/stackchan/widget.md) |
| `tools/` | Firmware flashing, standalone learning launcher and demo utilities | [Build](docs/stackchan/build.md), [learning](docs/learning.md) |
| `docs/stackchan/` | Hardware notes, personality, vision and integration details | [Personality](docs/stackchan/personality.md), [vision](docs/stackchan/vision.md), [Claude Code](docs/stackchan/claude-code-integration.md) |
| `docs/launch-video/` | Launch script, reference material and Remotion video project | [Video project](docs/launch-video/remotion/README.md) |
| `past-experiments/` | Earlier boards, e-ink firmware and enclosure experiments | [Archive overview](past-experiments/README.md) |

## Development

Install the Python development tools and run the bridge checks from the repository root:

```bash
bridge/.venv/bin/python -m pip install -e './bridge[dev]'
(cd bridge && .venv/bin/pytest -q)
(cd bridge && .venv/bin/ruff check src/ tests/)
```

The served whiteboard bundle is checked in. To rebuild it after changing the
React/TypeScript source:

```bash
(cd bridge/web-canvas && npm ci && npm run build)
```

The build writes the bundle into `bridge/src/cc_buddy_bridge/learning/web/canvas/`.
Firmware host tests live in `firmware/claude_pet_stackchan/host/`; the
[build guide](docs/stackchan/build.md) covers hardware setup and bench conventions.
Browser and live-tutor checks are described in the [learning guide](docs/learning.md).
Hardware, live APIs and macOS-specific integrations require their own setup beyond
the offline demo.

## Credits

The lessons were built for the OpenAI, OpenRouter and CopilotKit *Agents,
Everywhere: Bots, Channels & More* global hackathon by **Gurucharan Lingamallu,
Swetank Griyage and Emaha Tekle**. The whiteboard began with Swetank's `smartboard`
work; the tutor, voice integration and widget cards were developed on buddy and
merged back here. The hackathon fork, launch film and presentation are in
[gurul/buddyTinkerer](https://github.com/gurul/buddyTinkerer). The cut-paper
illustration above also comes from that project.

The firmware began as [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
and the bridge as [SnowWarri0r/cc-buddy-bridge](https://github.com/SnowWarri0r/cc-buddy-bridge).
Other foundations include FluxGarage RoboEyes, sherpa-onnx, M5Stack's libraries,
and OpenAI's computer-use sample; the chirps draw on Marcelo Larios' R2D2 sound
generator. Practice references use Exa. Research behind the affect engine and diary
is cited in [personality.md](docs/stackchan/personality.md).

See the [bridge license](bridge/LICENSE), [canvas attribution](bridge/web-canvas/LICENSE.md),
[tldraw license](bridge/web-canvas/TLDRAW-LICENSE.md), and bundled font license
notices for the respective components.

### Live Laya expressions

Buddy can now use local Laya to choose eleven temporary eye expressions—including a wink—from conversation and diary text, including while speaking. The Mac runs the model and the board renders the cues. Original chirps and caption babble are preserved; Laya controls eyes only. This owner-enabled experimental mode uses the original checkpoint because the tuned head performed worse on fresh examples. See [controls, limitations, and device verification](docs/stackchan/laya-expressions/README.md).
