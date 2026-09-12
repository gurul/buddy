<p align="center">
  <img src="docs/assets/buddy-sidekick.png" alt="buddy, your desktop sidekick: a cut-paper illustration of the buddy robot saying Hey there!" width="100%">
</p>

<p align="center"><strong>Built for the OpenAI, Open Router, CopilotKit Agents, Everywhere: Bots, Channels, &amp; More — Global Hackathon.</strong></p>

**buddy is a small robot that lives on your desk.** It listens, has moods, keeps a diary,
uses your Mac when you ask, and now helps kids learn math without handing them the answer.
Say *"hey buddy"* and it turns to you. Leave it alone and it looks around the room, gets
curious, gets bored, is glad when you come back, and writes down what it thought.

buddy is an [M5StackChan](https://docs.m5stack.com/en/products/sku/K151) head (a camera,
two servos, twelve LEDs, a speaker and a touch pad) running its own firmware, plus a
daemon on your Mac that gives it ears, hands and a memory.

- [What it does](#what-it-does)
- [It teaches](#it-teaches)
- [Get one running](#get-one-running)
- [Under the hood](#under-the-hood)
- [Credits](#credits)

## What it does

**It listens.** A keyword spotter on your Mac hears "hey buddy". Nothing leaves the Mac
until you speak. Its head comes up with a chirp and its back glows blue.

**It answers on its own screen.** Replies appear as captions under its eyes while it
beeps and boops. No voice comes out of your Mac.

**It looks things up and thinks hard.** Questions about today (weather, scores, prices)
go to web search. Math, code and planning questions go to a slower, stronger model.

**It works your computer.** "Open a new tab and find the weather": its head drops to the
desk, its eyes go quick, the LEDs ripple, the screen says *on it…*, and a `gpt-6-astra`
loop drives the real mouse and keyboard (screenshot, a few lines of PyAutoGUI, look
again, up to 25 steps). It asks before anything consequential, takes "actually, use
Safari" mid-task, and stops on "stop". Then it nods and tells you.

**It sees and moves.** It follows your face, learns where you usually sit, and knows you
from a stranger. Ask "where's my mug?" and it turns to find it. It can dance (sway, nod,
wiggle, figure-8) when you run `cc-buddy-bridge move dance`, and a pat on the head gets a
pink heartbeat.

**It has moods.** While it explores, an affect engine on the board (valence, arousal, a
social drive, a stimulation drive) decides how it feels, and every channel shows it: eye
openness and blink rate, LED colour and animation, how fast and how wide it looks
around, which chirp it makes. Curious is a scanner dot and two rising notes. Startled is
a red flash and a head jerk. Bored is half-closed eyes and a slow sigh.

**It remembers.** Every look it takes becomes a memory, and it keeps only the thoughts
that are specific and interesting. At night it sleeps on the day: it rewrites what it
knows about the room and about you. Every conversation is distilled into a few lines
(never the words), and the next one opens with one thing it still owes you. Say
*"remember that"* and it keeps the fact for good.

**It takes pictures of things it finds cool.** A first, someone in the room, its own
things moved: those get a full-resolution photo, on its own judgment, and never the same
thing twice.

**It takes notes for you.** Run `cc-buddy-bridge take-notes start` and it transcribes the
room until you stop it, then writes up the decisions, the actions and what was left open.

**It shows you.** A macOS desktop widget carries its latest thoughts and current feeling.
Click it for the whole diary: what it saw, how it felt over time, its profile of you, its
dreams.

**It knows when you are busy.** It mirrors Claude Code: asleep while you are idle, busy
while Claude works, head up and searching for you when a session is waiting on you. Tap
it and it raises that terminal.

## It teaches

<p align="center">
  <img src="docs/assets/buddy-lesson.png" alt="A buddy math lesson: the problem Solve 2x + 3 = 11, a whiteboard, buddy's hint and one step, and the Give me a hint, Check my work and Show one step buttons" width="80%">
</p>

buddy has math lessons for kids. The rule is simple: **buddy helps you think, and never
hands over the whole answer.**

1. **Pick a way in.** Learn a topic at your level, and buddy makes a problem to match. Or
   bring your own problem: paste a screenshot or write it out, and buddy reads it back to
   check it understood.
2. **Work on the whiteboard** with a mouse or a pen.
3. **Ask for the help you need:**
   - **Give me a hint:** a nudge that never finishes a step.
   - **Check my work:** buddy checks that it can read your writing, that the steps are
     right, and that you're finished, and it points only to the first slip.
   - **Show one step:** buddy writes exactly one step, explains it, and keeps it apart
     from your work.
4. **Wrap up.** buddy sums up the method and checks that you understood it.

Lessons and every whiteboard revision are saved to a dashboard, with a card on the Mac
widget. You can start and steer a lesson by voice ("okay buddy, I want to do a math
lesson") or from a terminal with `cc-buddy-bridge lesson start --mode learn --topic
"adding fractions" --level "Grade 4"`, then `lesson hint`, `lesson check`, `lesson step`.
When `EXA_API_KEY` is set, new lessons draw on practice references from Khan Academy,
OpenStax, Math Is Fun and similar sources.

**Why:** a UC Berkeley mathematics professor reports that 71% of her Calculus I students
tested ready or nearly ready in 2018–2020, and 44% in 2021–2023. Her office hours turn
into lessons on fractions (Zvezdelina Stankova, op-ed in the San Francisco Standard,
reported by the California Post, Aug 16, 2026). Fraction gaps don't fix themselves.

> **Status:** lessons are on the
> [`buddy-lesson-tool`](https://github.com/gurul/buddyTinkerer/tree/buddy-lesson-tool)
> branch and not merged into `main` yet. Setup, the offline demo and every option are in
> [docs/learning.md](https://github.com/gurul/buddyTinkerer/blob/buddy-lesson-tool/docs/learning.md)
> on that branch.

## Get one running

### Math lessons and saved whiteboards

Buddy now has a learning workspace: learn a topic or bring a screenshot/written
problem, put down your ideas, ask for hints, check your work, or reveal exactly
one next step at a time. Whiteboard revisions and tutor explanations are saved
to a searchable dashboard, linked from the macOS widget. The existing voice
agent launches and controls lessons with `math_lesson`, and
`cc-buddy-bridge lesson <action>` (`open`, `start`, `ideas`, `hint`, `check`,
`step`, `status`, `recap`, `end`, `listen`, `stop-listening`) drives the same lesson from a terminal while
the daemon runs.

Try the offline browser demo from this repository (Python 3.11+):

```bash
python tools/start_learning.py --demo --data-dir .demo-data --port 48767
```

Click **Play workflow demo**, or work through an example yourself. This demo
uses fixed examples; live handwriting/image tutoring uses OpenAI
(`OPENAI_API_KEY`) by default; OpenRouter is opt-in via
`CC_BUDDY_LEARNING_PROVIDER=openrouter`. Put the key in `~/.config/cc-buddy-bridge/env` and restart without `--demo`. In WSL,
use `--env-file /mnt/c/Users/<you>/.config/cc-buddy-bridge/env` to read the
Windows file; Linux and Windows home directories are separate. If the launcher
says port 48766 is already in use, open the existing dashboard at
`http://127.0.0.1:48766/` instead of starting it again.
See [setup, workflow, and widget integration](docs/learning.md) and the
[recorded browser demo](docs/learning-demo/buddy-learning-demo.webm).
Add `EXA_API_KEY` to the same environment file to find practice references
when generating live lessons. Reference links are saved with each lesson;
search failures fall back to model-generated problems.

Host (macOS, Python 3.12):

```bash
cd bridge && python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/cc-buddy-bridge install                                              # Claude Code hooks
.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'  # the daemon, at login
mkdir -p ~/.config/cc-buddy-bridge/models && curl -L \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2 \
  | tar xj -C ~/.config/cc-buddy-bridge/models                                   # wake-word model, 19 MB
printf 'OPENAI_API_KEY=sk-...\n' > ~/.config/cc-buddy-bridge/env && chmod 600 ~/.config/cc-buddy-bridge/env
```

Keys live only in `~/.config/cc-buddy-bridge/env`. Add `EXA_API_KEY=...` there for lesson
practice references.

Robot:

```bash
arduino-cli core install esp32:esp32        # tested with 3.3.10
./tools/flash_stackchan.sh                  # compile, archive the ELF, flash, restart the daemon
```

Grant the daemon's Python **Microphone**, **Accessibility** and **Screen Recording** in
System Settings → Privacy & Security (the daemon logs the exact binary and asks for the
Screen Recording dialog on first start). Claude Code's permission prompts always stay in
the terminal; the board never gates a tool call.

Then:

```bash
.venv/bin/cc-buddy-bridge ears-check                                  # say "hey buddy"
tail -f ~/Library/Logs/cc-buddy-bridge.log | grep -E "ears|agent|voice|diary"
```

Try *"hey buddy, what time is it?"*, *"hey buddy, open a new tab and search for the
weather"*, *"actually use Bing"*, *"stop"*. Leave it for ten minutes and it starts
exploring, and the widget fills up. Or send it off yourself: *"hey buddy, go explore"*
(or `cc-buddy-bridge explore` from a shell).

## Under the hood

```
you ─"hey buddy"─▶ Mac mic ─▶ daemon ─▶ gpt-live-1 (understands you) ─▶ gpt-6-astra + PyAutoGUI (hands)
                               │ NDJSON over USB serial
                               ▼
                             buddy ── persona · affect engine · conversation phases · captions · eyes · LEDs · chirps
camera frames ◀──────────────▶ macOS Vision (faces) · diary (memory + vision LLM) ─▶ desktop widget
```

| | Where | Read |
|---|---|---|
| Ears, voice, web search, deep reasoning, computer control | `bridge/src/cc_buddy_bridge/ears.py`, `voice_agent.py`, `think.py`, `computer_agent.py`, `desktop_worker.py` | [voice.md](docs/stackchan/voice.md) |
| Seeing, turning its head, goodbye, mute | `bridge/src/cc_buddy_bridge/scene.py`, `head.py`, `intent.py`, `sound.py`; `firmware/claude_pet_stackchan/src/hostlook.h` | [vision.md](docs/stackchan/vision.md) |
| Feelings, phases, the diary | `firmware/claude_pet_stackchan/src/mood.cpp`, `body.cpp`, `eyes.cpp`; `bridge/src/cc_buddy_bridge/diary.py` | [personality.md](docs/stackchan/personality.md) |
| Taking notes on the room | `bridge/src/cc_buddy_bridge/notes.py` | [voice.md](docs/stackchan/voice.md#taking-notes-on-the-room) |
| Motion: named rhythms the board runs | `bridge/src/cc_buddy_bridge/motion.py`, `firmware/claude_pet_stackchan/src/motion.h` | [build.md](docs/stackchan/build.md#named-motion) |
| Memory of conversations, and starring by voice | `bridge/src/cc_buddy_bridge/recall.py`, `chat_memory.py` | [voice.md](docs/stackchan/voice.md#what-buddy-remembers-of-talking-with-you) |
| Math lessons, whiteboard, tutor (on `buddy-lesson-tool`) | `bridge/src/cc_buddy_bridge/learning/` | [learning.md](https://github.com/gurul/buddyTinkerer/blob/buddy-lesson-tool/docs/learning.md) |
| Widget and diary window | `widget/` | [widget.md](docs/stackchan/widget.md) |
| The robot itself: build, wire protocol, gaze, bench notes | `firmware/claude_pet_stackchan` | [build.md](docs/stackchan/build.md), [DESIGN.md](DESIGN.md) |
| Daemon commands and every knob | `bridge/` | [bridge/README.md](bridge/README.md) |

## Credits

Built for the OpenAI, Open Router, CopilotKit Agents, Everywhere: Bots, Channels, & More — Global Hackathon. Math lessons and the whiteboard began with Swetank
Griyage's `smartboard` work.

The firmware began as [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy)
and the daemon as [SnowWarri0r/cc-buddy-bridge](https://github.com/SnowWarri0r/cc-buddy-bridge),
both MIT, and both remain MIT here. Eyes by [FluxGarage RoboEyes](https://github.com/FluxGarage/RoboEyes);
wake word by [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx); the computer-use loop follows
[openai/openai-cua-sample-app](https://github.com/openai/openai-cua-sample-app); chirps after Marcelo
Larios' R2D2 sound generator. Practice references by [Exa](https://exa.ai). Fonts in the lesson app
and widget are Grandstander, Andika, Gochi Hand and VT323 (SIL Open Font License). The research
behind the feelings and the diary is cited in [personality.md](docs/stackchan/personality.md).
The boards buddy grew out of are kept in [past-experiments](past-experiments/README.md).
