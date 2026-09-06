# 🤖 buddy — a desk robot that talks, feels, and runs your Mac

![buddy on a desk, eyes on, saying working...](docs/assets/thumbnail.png)

**buddy** is a small robot head that lives next to your terminal: an
[M5StackChan K151](https://docs.m5stack.com/en/products/sku/K151) (camera, two servos,
twelve LEDs, a speaker, a touch pad) running the firmware in
`firmware/claude_pet_stackchan`, and a Python daemon on your Mac in `bridge/`. Say
**"hey buddy"** and it listens; ask it to do something on the computer and a
`gpt-6-astra` computer-use loop does it through the real mouse and keyboard while
the robot acts the part. Left alone, it explores the room with a feeling of its
own — curious, startled, bored, lonely, happy to see you — and keeps a diary worth
reading. And it mirrors Claude Code: asleep when you are idle, busy while Claude
works, head up and searching for you when a session is blocked on you.

```
you ─"hey buddy"─▶ Mac mic ─▶ bridge daemon ─▶ gpt-realtime (voice) ─▶ gpt-6-astra + PyAutoGUI (hands)
Claude Code CLI ─hooks─▶ bridge daemon ─NDJSON over USB serial─▶ StackChan: persona · affect engine · agent phases
camera frames ◀─────────▶ macOS Vision (faces) · diary (memory + vision LLM) ─▶ desktop widget
```

## What buddy does

- **Talks, and runs your computer.** An on-device keyword spotter hears "hey buddy" (no
  cloud until you speak). A `gpt-realtime-2.1-mini` conversation answers you — as captions on
  the robot's own screen with beep-boops, not a voice from the Mac — and, when you
  ask for something on the Mac, hands a goal to `gpt-6-astra`, which screenshots, writes a
  few lines of PyAutoGUI, runs them, and looks again — up to 25 steps, asking out loud before
  anything consequential, taking corrections mid-task, stopping on "stop". The robot drops
  its head to the desk with quick eyes ("on it…"), nods when done, winces on an error.
  → [docs/stackchan/voice.md](docs/stackchan/voice.md)
- **Has feelings you can read.** An affect engine on the board — valence, arousal, a social
  and a stimulation drive — colours its eyes, LEDs, head tempo and chirps while it explores:
  curious, surprised, startled, happy, affectionate when it sees you, bored when nothing
  moves, lonely when nobody comes. → [docs/stackchan/personality.md](docs/stackchan/personality.md)
- **Keeps a diary.** Every look becomes a memory record; each new thought is written with
  the whole memory in context, picked for novelty, gated for interest, and consolidated each
  night into "dreams" and a profile of you and the room. Star a thought and buddy never
  forgets it. → [docs/stackchan/personality.md § 3](docs/stackchan/personality.md#3-the-diary-bridgesrccc_buddy_bridgediarypy)
- **Shows it on the desktop.** A macOS widget shows the newest thoughts and how buddy felt;
  click it for the full diary — observations, what changed, feelings over time, profile,
  dreams. → [docs/stackchan/widget.md](docs/stackchan/widget.md)
- **Looks at you.** The board streams camera frames; macOS Vision finds your face and learns
  where you sit; hold Option while facing it to enrol as its owner.
- **Mirrors Claude Code.** Hooks plus a transcript tailer drive sleep / busy / attention; tap
  the robot to raise the blocked terminal. → [docs/experiments/claude-code-integration.md](docs/experiments/claude-code-integration.md)

## Install

Host (macOS, Python 3.12):

```bash
cd bridge && python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/cc-buddy-bridge install                                    # Claude Code hooks
.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'   # the launchd daemon
```

Models and keys, once:

```bash
mkdir -p ~/.config/cc-buddy-bridge/models && curl -L \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2 \
  | tar xj -C ~/.config/cc-buddy-bridge/models                         # the wake-word model (19 MB)
printf 'OPENAI_API_KEY=sk-...\n' > ~/.config/cc-buddy-bridge/env && chmod 600 ~/.config/cc-buddy-bridge/env
```

Robot:

```bash
arduino-cli core install esp32:esp32                                 # tested with 3.3.10
./tools/flash_stackchan.sh                                           # compile → archive ELF → flash → restart daemon
```

Grant the daemon's Python **Microphone**, **Accessibility** and **Screen Recording** in
System Settings → Privacy & Security; the daemon logs the exact binary and asks macOS for
the Screen Recording dialog on first start. Put `CC_BUDDY_MONITOR_ONLY=1` in the service
environment so permission cards stay in the terminal (taps only focus it).

## Quick start

```bash
.venv/bin/cc-buddy-bridge ears-check        # say "hey buddy" — HEARD IT at 1.4 s
tail -f ~/Library/Logs/cc-buddy-bridge.log | grep -E "ears|agent|voice|diary"
```

Then talk to it: *"hey buddy … what time is it?"* — *"hey buddy, open a new tab and search
for the weather"* — *"actually use Bing"* — *"stop"*. Leave it alone for ten minutes and it
starts exploring; the widget fills with thoughts; `cc-buddy-bridge notes --last 5` prints
them.

## How it works

| Piece | Where | Doc |
|---|---|---|
| Wake word, voice, computer control | `bridge/src/cc_buddy_bridge/ears.py`, `voice_agent.py`, `computer_agent.py`, `desktop_worker.py` | [voice.md](docs/stackchan/voice.md) |
| Affect engine and agent phases | `firmware/claude_pet_stackchan/src/mood.cpp`, `body.cpp`, `eyes.cpp`; reference `bridge/src/cc_buddy_bridge/mood_model.py` | [personality.md](docs/stackchan/personality.md) |
| Exploring and the diary | `bridge/src/cc_buddy_bridge/explore.py`, `diary.py` | [personality.md § 3](docs/stackchan/personality.md) |
| Widget and diary window | `widget/` (WidgetKit + SwiftUI) | [widget.md](docs/stackchan/widget.md) |
| Robot build, wire protocol, gaze policy, bench notes | `firmware/claude_pet_stackchan`, `tools/flash_stackchan.sh` | [build.md](docs/stackchan/build.md), [DESIGN.md](DESIGN.md) |
| Daemon commands and knobs | `bridge/` | [bridge/README.md](bridge/README.md) |

The daemon is board-agnostic: anything that speaks the NDJSON contract over a serial
port is a valid client — [DESIGN.md](DESIGN.md) has the exact contract.

## History

buddy grew out of three earlier builds — a touch-screen desk pet on a Freenove
ESP32-S3, an e-ink monitor-and-control dock, and a read-only e-ink agent wallboard —
plus a 3D-printed shell. They still work and share the same daemon; they live in
[docs/experiments/](docs/experiments/README.md), with their firmware under `firmware/`.

## Acknowledgements

- [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) — the 7-state pet the firmware started from (MIT).
- [SnowWarri0r/cc-buddy-bridge](https://github.com/SnowWarri0r/cc-buddy-bridge) — the host daemon this one extends (MIT).
- [FluxGarage RoboEyes](https://github.com/FluxGarage/RoboEyes) — the eyes; [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — keyword spotting;
  [openai/openai-cua-sample-app](https://github.com/openai/openai-cua-sample-app) — the exec_py computer-use loop; Marcelo Larios' R2D2 sound generator — the chirps.
- The research behind the feelings and the diary is cited in [personality.md § 4](docs/stackchan/personality.md).

## Licenses

Both upstreams are MIT (Anthropic PBC / Snow) and remain MIT here.
