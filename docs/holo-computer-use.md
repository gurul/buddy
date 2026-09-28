# Holo computer use

buddy can hand its desktop tasks to H Company's Holo4 instead of Codex. Holo4 is a
computer-use model: it looks at screenshots of the Mac and clicks, types and scrolls
until the task is done.

Holo is **off** by default. Codex stays the desktop lane unless you turn Holo on.

## How it works

`holo_computer.py` drives H Company's desktop agent through its open-source CLI,
[holo-desktop-cli](https://github.com/hcompai/holo-desktop-cli): by default through the
CLI's own Python client (`holo_driver.py`, "the two drivers" below), so a task can be
corrected and stopped while it runs; or, when that Python is missing, as one `holo run`
per task. H's runtime does the screenshots, clicks and memory; buddy starts the task,
watches it, corrects it, and stops it.

- **Order of lanes:** the launch reflex ("open Spotify"), the Firecrawl reader and the
  Chrome lane still take their tasks first. Holo takes what Codex would have taken.
- **Progress:** buddy reads the run's `events.jsonl` while it works and reports each
  step. A step shows the model's note, or the name of the tool it used. Tool arguments
  are never shown, so text Holo types (a password, a message) does not reach the board
  or the chat.
- **Answer:** the text `holo run` prints at the end is the task's result.
- **Corrections reach Holo:** "steer_task" (a texted or spoken correction while the task
  runs) is sent to the running session. Before the session exists it is queued in the
  driver and delivered the moment the session is running. If the runtime refuses one,
  buddy says so as a progress line instead of pretending it was taken.
- **Stopping:** `stop_task` asks the driver to stop: it files the CLI's own stop request
  and pauses then cancels the session, so Holo halts at the next action boundary. A stop
  that reached the task is always reported "stopped", never as a result, even when Holo
  had already finished ("It had reached: …" is said, not certified). That is the rule
  `verification/Buddy/HoloSteer.lean` proves, and the one `TaskStop.lean` proves for the
  chat.
- **No questions:** Holo has no move that asks you anything, so a task that needs a
  decision from you is not one for this lane.
- **No warm Codex:** while Holo is on, buddy does not keep a Codex process ready.

### The two drivers

`holo_computer.py` has two ways to drive Holo, one contract (run / steer / cancel /
status, the same as Codex's):

- **client** (the default when holo's own Python exists at
  `~/.holo/tools/holo-desktop-cli/bin/python`): buddy starts `holo_driver.py` under
  **holo's** Python, never its own venv, so buddy stays free of the CLI's fifteen
  dependencies and the session request (instructions and skills from `~/.holo`) is built
  by the client H ships. The two talk JSON lines: buddy sends `run`, `steer` and
  `cancel`; the driver reports `ready`, `session`, `progress`, `steered` / `refused`,
  and `final` (answer, status, the runtime's step and cost metrics). The driver uses the
  CLI's own turn runner (`session_runner.run_turn`), so the machine-wide desktop lock and
  the double-Esc / `holo stop` kill switch apply exactly as they do to `holo run`.
- **cli** (`CC_BUDDY_HOLO_DRIVER=cli`, or when that Python is missing): one `holo run`
  per task, progress tailed from the run's `events.jsonl`, no steering, as before.

**The warm runtime.** The `hai-agent-runtime` binary shuts itself down when the process
that launched it goes away, so a runtime the driver spawned would die with every task.
The daemon therefore owns one: `HoloRuntime` starts `holo agent-api` as the daemon's
child on the CLI's default port (18795) at the first task, hands the driver the same
bearer token through `HAI_AGENT_RUNTIME_API_TOKEN`, and ends it when the daemon stops.
Each task then only attaches. If something else already listens on the port (your own
`holo run`), nothing is spawned and the driver attaches to that runtime instead.
- **Screenshots are deleted:** each run writes to `~/.holo/runs/buddy/<id>/`, which
  holds desktop screenshots. buddy deletes it when the task ends.
- **Spend:** each task writes one unpriced line (`hcompany`, the model) to the spend
  ledger. The event log has no token counts, so the price is on H's credit dashboard.

## Set up

1. Install the CLI (it lives in `~/.holo/bin/holo`), then sign in:

   ```bash
   holo login
   ```

   This saves `HAI_API_KEY` to `~/.holo/.env`. buddy never reads the key; the CLI does.

2. Add **Models API** credits at
   <https://portal.hcompany.ai/credits?product=modelsapi>. The Holo4 models are paid
   tier only. The Agents API Developer plan is a separate product: it does not
   unlock them. Credits can take 15 minutes to appear.

3. Turn it on in `~/.config/cc-buddy-bridge/env` and restart the daemon:

   ```bash
   CC_BUDDY_COMPUTER=holo
   ```

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `CC_BUDDY_COMPUTER` | `codex` | `holo` makes Holo the desktop lane. |
| `CC_BUDDY_HOLO_MODEL` | `holo4-27b` | Any Models API ID, for example `holo4-35b-a3b` (faster and cheaper) or `holo3-1-35b-a3b` (free tier). |
| `CC_BUDDY_HOLO_MAX_STEPS` | none | A step cap for each task (`--max-steps` on either driver). |
| `CC_BUDDY_HOLO_KEEP_RUNS` | off | `1` keeps each run's events and screenshots, for debugging. |
| `CC_BUDDY_HOLO_BIN` | `holo` on PATH, else `~/.holo/bin/holo` | The CLI to run (`holo run`, `holo stop`, `holo agent-api`). |
| `CC_BUDDY_HOLO_DRIVER` | `client` | `client` drives Holo through the CLI's Python client (corrections, clean stop, warm runtime); `cli` is one `holo run` per task. |
| `CC_BUDDY_HOLO_PYTHON` | `~/.holo/tools/holo-desktop-cli/bin/python` | The interpreter `holo_driver.py` runs under: holo's own, where `holo_desktop` is installed. When it is missing the lane falls back to `cli`. |

The task budget is ten minutes, the same as Codex.

## Checks

- `bridge/tests/test_holo_computer.py` runs the lane against a fake `holo` that writes
  the CLI's real event shape (the cli driver), a fake `holo_driver.py` that speaks the
  client protocol (a correction forwarded, one queued before the session, a stop that
  lands after Holo finished, a driver that fails), and a fake `holo agent-api` (the warm
  runtime spawned once for two tasks, the token handed to each driver, ended on close).
- `bridge/tests/test_holo_lean.py` replays the traces of `verification/Buddy/HoloSteer.lean`
  against the real `HoloComputerAgent`.
- `bridge/tools/holo_live_check.py` is the live proof, on the real desktop: a read-only
  task, then a correction sent through `steer()` asking Holo to end its answer with a
  code word it was never given otherwise. 2026-09-28: `HOLO_STEER_OK`, the answer ended
  with the word, 11 s from start to answer. Two things it caught on the way, both fixed:
  a correction sent the instant the session was created bounced (the runtime takes a
  message only once the session is running, so the driver now waits for that), and the
  driver outlived its final event (a stdin reader on the default executor kept the
  process alive; it is a daemon thread now, and buddy stops waiting after the final).
- A real read-only run on `holo3-1-35b-a3b` (2026-09-28) answered "which app is in
  front?" correctly in 6.3 s.
- A live read-only `holo4-27b` run through `HoloComputerAgent` itself (2026-09-28,
  CLI 0.0.6, runtime 0.1.12): "which application window is in front?" answered
  correctly in 9.5 s, with one `progress` event and the `final`. The daemon has run
  with `CC_BUDDY_COMPUTER=holo` since that day; its startup log says
  `agent: Holo desktop lane enabled; model=holo4-27b`.

The CLI is [`hcompai/holo-desktop-cli`](https://github.com/hcompai/holo-desktop-cli)
(Apache-2.0). To upgrade it in place, run the two commands the installer runs, with
the toolchain already in `~/.holo`:

```bash
export UV_PYTHON_INSTALL_DIR=~/.holo/python UV_TOOL_DIR=~/.holo/tools UV_TOOL_BIN_DIR=~/.holo/bin
~/.holo/toolchain/uv/uv tool install "holo-desktop-cli==<version>" --python 3.12 --force --reinstall-package holo-desktop-cli
~/.holo/toolchain/uv/uv run --with "holo-desktop-cli==<version>" python -m holo_desktop.installer_bootstrap --yes
```

The second command downloads the runtime that version pins (sha256-checked). The
consumer installer's manifest can lag the GitHub release (it said 0.0.5 when 0.0.6
was out), so pin the version yourself. No daemon restart is needed: each task is its
own `holo run`.
