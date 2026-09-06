---
title: StackChan build
icon: 🤖
order: 1
---

# StackChan build (M5StackChan K151)

`firmware/claude_pet_stackchan` is the buddy port for the **M5StackChan AI
Desktop Robot** (SKU K151, CoreS3 core). Same NDJSON protocol, same
`cc-buddy-bridge` daemon. The robot mirrors Claude Code's state with its head,
eyes, 12 LEDs, and R2D2-style chirps. With the Mac attached it also turns
toward you, learns your face, and looks around the room when Claude is idle.

See also [capabilities.md](capabilities.md) (hardware, bench facts),
[repos.md](repos.md) (vendored repos), [widget.md](widget.md) (desktop widget),
and `bridge/README.md` (Listen key, Host vision, Owner identity, Idle explorer).

## Hardware

| Part | Detail |
|---|---|
| Core | M5Stack CoreS3: ESP32-S3, 16 MB flash, QSPI PSRAM, native USB-Serial/JTAG (`/dev/cu.usbmodem*`, VID 0x303a PID 0x1001) |
| Screen | ILI9342C 320x240, used in landscape. FT6336U touch |
| Camera | GC0308, QVGA RGB565, big-endian per pixel (bench 2026-09-05) |
| Head | 2x Feetech SCS0009 serial servos on UART1 (G6/G7, 1 Mbaud). Yaw id 1, pitch id 2 |
| LEDs | 12x WS2812C on the top board, written as RGB565 through the PY32L020 expander (I2C 0x6F) |
| Top touch | Si12T three zones: front, middle, back |
| Power | 550 mAh body battery through the AXP2101 PMIC. INA226 monitor. Servo 5 V rail gated by the expander (`VM_EN`) |

Two USB-C ports carry data. M5Stack recommends the base port. Opening the CDC
port does **not** reset the sketch, so the daemon can hold it open.

## Toolchain

| Item | Version | Where |
|---|---|---|
| arduino-cli + esp32 core | 3.3.10 | `arduino-cli core install esp32:esp32` |
| FQBN | `esp32:esp32:m5stack_cores3:PartitionScheme=huge_app,PSRAM=enabled` | `tools/flash_stackchan.sh` |
| M5Unified | 0.2.21 | Library Manager |
| M5GFX | 0.2.28 | Library Manager |
| StackChan-BSP | 1.1.0 | symlink `~/Documents/Arduino/libraries/M5StackChan` → `vendor/stackchan/StackChan-BSP` |
| FluxGarage RoboEyes | 1.1.2 | symlink `~/Documents/Arduino/libraries/FluxGarage_RoboEyes` → `vendor/stackchan/RoboEyes` |
| IRremoteESP8266, M5Unit-NFC | — | BSP dependencies, Library Manager |

Re-clone the vendored repos with the loop in [repos.md](repos.md#re-clone).

## Flash

```bash
./tools/flash_stackchan.sh                        # compile → archive ELF → boot daemon out → upload → daemon back
./tools/flash_stackchan.sh /dev/cu.usbmodem3101   # explicit port
```

The script archives the exact ELF under `firmware/build-archive/`, boots the
launchd daemon out (it holds the port with `KeepAlive`), waits for the file
descriptor to close, uploads, and bootstraps the daemon back.

**Backup before the first flash.** The factory image is on
`firmware/build-archive/stackchan-factory-20260905.bin` (full 16 MB dump,
gitignored). Restore it, NVS included (WiFi, app pairing, servo zero offsets):

```bash
esptool -p /dev/cu.usbmodem* write-flash 0x0 firmware/build-archive/stackchan-factory-20260905.bin
```

Do not pass `-b`/`--baud` on this link: a baud switch stalls the
USB-Serial/JTAG transfer (bench: `-b 460800` died at 110 KB). Download mode:
hold RST about 3 s until the green LED next to it lights, then release.

## The face

RoboEyes (FluxGarage) on a 320x204 1-bit canvas pushed into the frame every
tick: two 96x96 eyes, radius 22, 36 px apart. The eye colour is a random HSV
hue per boot (printed as `[eyes] colour #RRGGBB`; `EYE_COLOUR_OVERRIDE` in
`src/eyes.h` fixes it). One status word sits under the eyes in the same
colour. No clock. The ASCII species and GIF renderer compile but are not
drawn. A permission card owns y >= 126, so the eyes park on the top row.

## States

`derive()` in `src/main.cpp` maps the heartbeat to a persona state. No daemon
connected → IDLE (awake, looking around).

| State | Trigger | Head | Eyes | LEDs | Chirp |
|---|---|---|---|---|---|
| SLEEP | connected, nothing running | pitch 10, torque releases at rest | TIRED, closed, a 600 ms peek every ~20 s | very dim blue breathe (4 s) | descending, quiet |
| BUSY | any session running | level (pitch 45), nod every 2.5 s | squint (67 px), curiosity | steady dim cyan | wake whistle when leaving sleep |
| ATTENTION | a session waits on a permission | pitch 70, then the gaze policy | ANGRY, horizontal flicker; sweat on a destructive prompt | orange pulse, 800 ms, all 12 | phrase + 4..6 beeps, again every 30 s |
| CELEBRATE | session completed | quick yaw shake, 0.6 s | HAPPY, laugh | green | trill |
| HEART | head pat (front zone) | tilt toward the toucher, pitch +8 | HAPPY, curiosity | pink | trill |
| DIZZY | (kept from the pet build) | 1.2 s yaw wobble | flicker, confused | off | wobble |
| Listening | Option held on the Mac, or a push-to-talk hold | toward a toucher seen in the last 30 s, else centre; pitch 60 | wide (110 px) | blue (mic live) | one short up-chirp |
| Explore | `{"cmd":"mode","explore":true}` | host `look`s when held; otherwise looks around on its own — amplitude, tempo and a pitch bias follow the affect engine (`mood.cpp`) | openness, smile/droop, curiosity, blink and saccade tempo from the feeling | the feeling's colour and pulse (0.25 / 0.5 / 2.5 Hz) | one chirp per feeling change |
| Agent phases | `{"cmd":"agent","state":..}` | listening: faces you · thinking: tilted, slow side-to-side · speaking: bobs · working: down at the desk, typing glances · asking: up · done: nod · error: wobble | per phase ([personality.md](personality.md) § 2) | animations over the two back rows: blue wave · cyan scanner · white sparkle · cyan ripple · orange alternating · green sweep · red flash | wake / talk babble / "hm?" / beep-boop / boop |

Head motion: the BSP runs a spring per servo. On top of it `body.cpp` glides
each target along `easeInOutCubic` and streams the pose at 25 Hz
(auto-angle-sync off, servo speed 100..600 scaled to the step; glide time
max(300 ms, 6 ms/deg)). While awake and not gliding, a two-sine micro-drift
(±1.5° yaw, ±1° pitch) keeps the head from looking parked. Pitch is clamped to
5..85, yaw to ±60. `PITCH_LEVEL` 45 / `PITCH_SLEEP` 10 / `PITCH_ATTENTION` 70
depend on the servo zero (NVS `servo/zero_pos_2`): bench-tune them.

Chirps (`src/chirp.cpp`) are synthesized to 8-bit PCM at 16 kHz into two 32 KB
PSRAM buffers and played through `M5.Speaker.playRaw()`; nothing blocks. Top
touch is armed 3 s after boot: the Si12T baseline is stale while the servo
rail comes up (the middle zone read pressed at boot, a phantom push-to-talk).

## Controls

| Input | No card up | Card showing |
|---|---|---|
| Swipe card right / left | — | approve / deny. Hold at the right edge 700 ms = ALWAYS |
| Tap the panel (attention only) | raise the blocked session's terminal | — |
| Hold the panel | push-to-talk: the daemon holds the dictation hotkey | — |
| Swipe down | Enter on the Mac | — |
| Swipe left / right | previous / next option in Claude Code's pickers | — |
| Bottom-right strip | scroll back through the transcript | — |
| **Front zone** tap (< 450 ms) | attention: focus terminal. Otherwise HEART one-shot | — |
| **Middle zone** hold (≥ 600 ms) | push-to-talk, same events as the panel hold | — |
| **Back zone** | same as the bottom-right strip | — |

With `CC_BUDDY_MONITOR_ONLY=1` (the setup on this machine) no card reaches the
robot, so only the "No card up" column applies.

## Gaze policy

`src/gaze.cpp` decides where the head looks. Sources, highest priority first:

1. **Host face** (`{"cmd":"face"}`, conf >= 40). Absolute angles use the
   yaw/pitch echoed in the cmd (the pose at capture), never the current one.
   `who:"owner"` trains the owner memory; `who:"unknown"` gets one glance in
   BUSY/IDLE and never trains it. Host silent for 3 s → on-board fallback.
2. **On-board detector** (`src/look.cpp`, core 0 task): motion centroid plus a
   skin-colour blob at ~10 fps; frames near a head move are quarantined.
3. **Owner memory** (`src/owner_model.h`): a habit histogram of where the owner
   tends to be, saved to NVS namespace `owner` (every 5 min or when confidence
   crosses 60, at most once per minute). A touch counts as an observation.

Owner wanted (ATTENTION or listening): live target → remembered spot →
**search sweep**, two rows (pitch 45 then 65, yaw -40..40, ~4.3 s), repeated
every 5 s while no live target is younger than 5 s. BUSY/IDLE only glance:
8° deadband, at most every 3 s, 3 s hold. SLEEP and the one-shots observe but
never move. `{"cmd":"look"}` is honoured in SLEEP/IDLE/BUSY only, never with a
card up or while the owner is wanted. `{"cmd":"owner","op":"reset"}` wipes the
memory. Geometry: camera HFOV assumed 66°, VFOV 3/4 of that; `kYawSign` +1
(bench: the head follows the hand); `kElevSign` is **unverified**.

## Wire additions

Every other verb is the pet build's (`time`, `status`, `permission`, `focus`, `key`, `voice`).

| Direction | Line | Purpose |
|---|---|---|
| host → board | `{"cmd":"listen","on":true\|false}` | Option key down/up on the Mac |
| host → board | `{"cmd":"cam","on":true,"fps":5,"w":160,"h":120}` / `{"cmd":"cam","on":false}` | start/stop the frame stream |
| board → host | `{"frame":{"seq":n,"w":160,"h":120,"fmt":"jpeg","b64":"...","yaw":Y,"pitch":P}}` | one line per frame, written from the camera task |
| host → board | `{"cmd":"face","seq":n,"bx":..,"by":..,"size":..,"conf":..,"yaw":Y,"pitch":P,"who":"owner"\|"unknown"}` | largest face; `conf:0` = no face |
| host → board | `{"cmd":"look","yaw":-60..60,"pitch":5..85,"hold":ms}` | absolute pose request (explorer) |
| host → board | `{"cmd":"mode","explore":true\|false}` | enter/leave explore mode |
| host → board | `{"cmd":"owner","op":"reset"}` | forget the owner memory |
| host → board | `{"cmd":"agent","state":"wake"\|"listening"\|"thinking"\|"speaking"\|"working"\|"asking"\|"done"\|"error"\|"idle"}` | the voice / computer-control conversation's phase; the robot acts it out ([personality.md](personality.md) § 2) |
| host → board | `{"cmd":"emote","dv":-100..100,"da":-100..100,"label":"curious"}` | the diary's appraisal of what the camera saw; nudges the affect engine by at most ±0.3 |
| host → board | `{"cmd":"caption","page":i,"of":n\|0,"lines":["..."],"hold_ms":ms,"chirp":bool,"final":bool}` / `{"cmd":"caption","clear":true}` | one page of buddy's reply, pre-wrapped by the host (≤ 4 lines × 17 chars, size-3 text at y 112); shown for `hold_ms` (+3 s grace), one talk chirp when `chirp` is true; `clear` removes it |

Frames pause while a character transfer owns the wire. On this machine: ~4 fps,
~2.5 KB JPEGs (quality 60), macOS Vision detects faces in 5–20 ms per frame.

## Daemon setup (this machine)

```bash
cd bridge && .venv/bin/cc-buddy-bridge install                       # hooks
.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'
.venv/bin/cc-buddy-bridge install --notes-widget                     # optional pyobjc panel at login
```

Then add `CC_BUDDY_MONITOR_ONLY=1` to `EnvironmentVariables` in
`~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist`: Claude Code
runs with `permissions.defaultMode = bypassPermissions` here, so a card on the
robot would be a second gate; taps only focus the terminal.
`~/.config/cc-buddy-bridge/matchers.toml` has `replace_defaults = true` and an
empty `always_ask`, so no Bash command is routed to the board either.

The launchd daemon does not read `.zshrc`: put `OPENAI_API_KEY=sk-...` in
`~/.config/cc-buddy-bridge/env` (mode 600); a value already in the environment
wins. The daemon's python needs **Input Monitoring** (listen key) and
**Accessibility** (Enter, and push-to-talk if you opt in with
`CC_BUDDY_VOICE_HOTKEY`; it is off by default); the startup log names the binary.
Reload the plist with `launchctl unload` + `load -w`, not `kickstart`.

Knobs (all in `bridge/README.md`): `CC_BUDDY_LISTEN_KEY` (`option`, `fn`,
`off`), `CC_BUDDY_OWNER_THRESHOLD` (0.9, lower is stricter), `CC_BUDDY_EXPLORE=0`,
`CC_BUDDY_EXPLORE_AFTER_MIN` (10), `CC_BUDDY_EXPLORE_CYCLE_MIN` (15),
`CC_BUDDY_NOTES_PER_HOUR` (6), `CC_BUDDY_NOTES_MODEL` (`gpt-5-mini`),
`CC_BUDDY_NOTES_DIR`, `CC_BUDDY_SAVE_FRAMES`; the voice and computer-control
knobs (`CC_BUDDY_VOICE`, `CC_BUDDY_WAKE_WORD`, `CC_BUDDY_COMPUTER_CONTROL`,
`CC_BUDDY_AGENT_MODEL`, …) are in [voice.md](voice.md). The daemon's python also
needs **Microphone** (wake word) and **Screen Recording** (computer use).

## Owner identity and explorer

Hold Option while facing the robot for a few seconds: the daemon takes one
Vision feature print (`VNGenerateImageFeaturePrintRequest` revision 2) every
2 s of the single face in frame, keeps up to 24 in
`~/.config/cc-buddy-bridge/owner_faceprints.json` (mode 600), and tags every
later face `owner` or `unknown` (`cc-buddy-bridge identity`, `identity reset`).
After 10 idle minutes the daemon sends `mode explore true`, walks ten `look`
waypoints (yaw -45..45 at pitch 40, then 60), and when a view changed spends
one note (OpenAI Responses API, one low-detail image, 60 output tokens max)
on `~/.config/cc-buddy-bridge/notes/YYYY-MM-DD.md`. Between the host's looks
and through the rest between cycles the robot stays in explore mode and looks
around the room on its own. Any sign of the human stops it. `cc-buddy-bridge notes --last 3` reads them; the WidgetKit widget in
`widget/` or `cc-buddy-bridge notes-widget` shows them on the desktop.

## Bench-verified conventions

- BSP `+yaw` turns the head to the **robot's right** = the viewer's left. A
  toucher at the screen's right is reached with a negative yaw (-28).
- GC0308 RGB565 is **big-endian per pixel** (`kRgb565ByteSwap = true`); the
  JPEG encoder reads it the same way, so the subsample copies words untouched.
- Static init: sprites must take `&halDisplay()`, never `&M5.Lcd`. `M5.Lcd` is
  a reference member of `pet::M5`, constructed in another TU; reading it at
  static init handed the sprite a null parent and boot-looped the first flash.
- Two globals named `M5` coexist (M5Unified's and the pet's `M5Compat`). The
  TUs that include M5Unified never include `board_compat.h` (`src/hal_m5.h`).
- The HWCDC RX/TX rings are raised to 4096 before the first `Serial.begin()`.
