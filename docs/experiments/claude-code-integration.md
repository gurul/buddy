# Claude Code integration and the host bridge

Shared by every build (the robot included). Moved here from the root README when it was
refocused on the robot; the current knobs are in [../../bridge/README.md](../../bridge/README.md).

## What a Claude Code session does to the pet

- **Mirrors Claude's state.** Sleeping, busy, waiting, celebrating — driven by Claude Code
  hooks plus a tailer over the session transcripts for tokens and message counts.
- **Approves tool calls from the board.** Risky commands (`git push`, `rm`, …) and reads
  outside the session directory render as a swipe card. The card shows which session is
  asking, how many more prompts wait behind it (a peeking deck + "+N" badge), and a bar
  draining toward the 300s terminal fallback. Destructive commands (`rm`, `sudo`,
  `git reset --hard`, …) get a red border and need a longer, harder swipe to approve.
  Tap the card for a full-screen view of the whole command; hold it at the right edge to
  approve **and stop being asked** for that command shape (daemon lifetime). Approving a
  read grants its whole enclosing repo for the daemon's lifetime.
- **Summons your terminal.** Tap the pet while it demands attention and the daemon raises
  the terminal of the session that's blocked on you (swipe up on a permission card for the
  same, without deciding). Window-level matching by the session's repo name works everywhere —
  AppleScript for iTerm2/Terminal.app, Accessibility (AXRaise) for Ghostty, Warp, cmux and
  friends — falling back to raising the app; the app order is configurable via
  `CC_BUDDY_FOCUS_APPS`.
- **Push-to-talk dictation (opt-in).** Set `CC_BUDDY_VOICE_HOTKEY` and holding the pet makes
  the daemon hold your dictation app's global hotkey until you let go — app-agnostic, it just
  holds a chord. Off by default: out of the box buddy never presses a key or opens the mic.
- **Hands-on-pet option picking.** Swipe left/right to walk Claude Code's option pickers
  (Up/Down arrows), swipe down for Enter — so the loop is: hold to dictate, release,
  swipe to choose, swipe down to send.
- **Always shows the time.** A small clock sits top-left of the pet whenever the bridge has
  synced the RTC.
- **Reports its own crashes — and recovers alone.** `cc-buddy-bridge diag` prints why the
  board last reset, what it was doing, and which loop phase hung, from an event ring that
  survives panics and watchdog reboots. The daemon watches the link both ways and escalates
  from reconnects (with a deliberate closed-port hold) up to an automatic RTS hardware reset
  of the board, so a wedged link heals without touching a cable.

## Host bridge

```bash
cd bridge
python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/cc-buddy-bridge install    # registers the hooks in Claude Code's settings.json
.venv/bin/cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*' --voice-hotkey option
```

`install` and `install --service` are **two separate steps** — `--service` installs the unit
*instead of* the hooks, so running only the second leaves you with a connected board that
never receives session state. Run both.

| Command | What |
|---|---|
| `daemon --serial-port …` | run the bridge in the foreground |
| `install` / `uninstall` / `status` | manage hooks; `--service` installs the launchd/systemd unit *instead* |
| `install --service --voice-hotkey option` | bake the push-to-talk hotkey into the unit so it survives reinstalls |
| `audit` | the approval decision log |
| `diag` / `diag --watch` | why the board last reset, and what it was doing |
| `voice-check` | diagnose push-to-talk (Accessibility permission, hotkey delivery) |
| `ears-check` | diagnose the "hey buddy" wake word (mic, model), then listen for it |
| `notes --last N` | print the diary's newest thoughts |
| `celebrate` | make the pet celebrate |

### Configuration

| Variable | Purpose |
|---|---|
| `CLAUDE_CONFIG_DIR` | which Claude config home `install`/`status` target (default `~/.claude`) |
| `CC_BUDDY_CLAUDE_CONFIG_DIRS` | `os.pathsep`-separated homes the daemon serves — it runs outside any session, so it can't inherit the above |
| `CC_BUDDY_VOICE_HOTKEY` | `off` (default — holding the pet never touches the keyboard or your mic), `option` (recommended when enabling), `opt-space`, or `fn`. Bake it in with `install --service --voice-hotkey …`; a hand-edited unit file is wiped by the next `--service` install |
| `CC_BUDDY_KEY_METHOD` | `osascript` routes Enter through System Events, for apps that swallow synthetic key events (Warp) |
| `CC_BUDDY_FOCUS_APPS` | comma-separated app names, in priority order, that tap-to-focus raises (e.g. `Warp,cmux,Composer`) — default: Ghostty, Warp, cmux, Composer, Cursor, VS Code |
| `CC_BUDDY_VOICE` / `CC_BUDDY_WAKE_WORD` / `CC_BUDDY_WAKE_THRESHOLD` / `CC_BUDDY_MIC` | the wake word (default on, `hey buddy`, 0.25, default input) — [docs/stackchan/voice.md](../stackchan/voice.md) |
| `CC_BUDDY_REALTIME_MODEL` / `CC_BUDDY_VOICE_NAME` / `CC_BUDDY_VOICE_IDLE_SECS` | the voice conversation (`gpt-realtime-2.1-mini`, `marin`, 20 s) |
| `CC_BUDDY_COMPUTER_CONTROL` / `CC_BUDDY_AGENT_MODEL` / `CC_BUDDY_AGENT_MAX_TURNS` | computer use (on, `gpt-6-astra`, 25 steps); action logs in `~/.config/cc-buddy-bridge/agent-runs/` |
| `CC_BUDDY_MONITOR_ONLY` | `1`/`true`/`yes`/`on` → never surface a permission card; defer to Claude Code's own prompt immediately. **Required by the e-ink agent-monitor firmware**, whose buttons can't answer. Env-gated rather than a CLI flag because it's a property of which firmware is flashed, not of how the daemon was invoked |

Installing into the wrong config home **fails silently** — hooks written, board animating,
no session ever prompting. `status` prints the home it resolved; check it first.

Push-to-talk is off unless `CC_BUDDY_VOICE_HOTKEY` names a chord. When enabled it needs
**Accessibility permission** for the daemon's python (macOS filters synthetic events from
untrusted processes). `voice-check` prints the exact binary to grant. Avoid `fn` as a hotkey:
it's a secondary-fn modifier that many apps read from raw HID, which synthetic events can't
reach — rebind your dictation app to a bare **Option** hold, which synthesizes reliably.

Granting that permission has two traps worth knowing before you fight them:

- **The system dialog is the easy path.** Holding the pet once triggers macOS's
  "would like to control this computer" prompt, whose *Open System Settings* button adds the
  entry for you. It fires **once per daemon lifetime** — if you miss it, restart the daemon
  to get it back. Adding the binary by hand instead means `+` → file picker → click out of
  the search field → `Cmd+Shift+G`; dragging from Finder is silently rejected.
- **`voice-check` run from a granted terminal reports that terminal's permission, not the
  daemon's.** macOS attributes Accessibility to the responsible process, so a terminal with
  the grant makes the check print `trusted: True` while the launchd daemon logs
  `Accessibility not granted`. When the two disagree, **the daemon log is the truth.**

After editing a unit file, reload it properly — `launchctl kickstart` restarts the process
but reuses the job definition cached at load time, so environment changes are ignored:

```bash
launchctl unload ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
launchctl load -w ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
```

