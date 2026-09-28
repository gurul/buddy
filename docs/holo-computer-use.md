# Holo computer use

buddy can hand its desktop tasks to H Company's Holo4 instead of Codex. Holo4 is a
computer-use model: it looks at screenshots of the Mac and clicks, types and scrolls
until the task is done.

Holo is **off** by default. Codex stays the desktop lane unless you turn Holo on.

## How it works

`holo_computer.py` runs one `holo run` per task. `holo run` is the HoloDesktop CLI,
H Company's own desktop agent. H's CLI does the screenshots, clicks and memory;
buddy starts it, watches it, and stops it.

- **Order of lanes:** the launch reflex ("open Spotify"), the Firecrawl reader and the
  Chrome lane still take their tasks first. Holo takes what Codex would have taken.
- **Progress:** buddy reads the run's `events.jsonl` while it works and reports each
  step. A step shows the model's note, or the name of the tool it used. Tool arguments
  are never shown, so text Holo types (a password, a message) does not reach the board
  or the chat.
- **Answer:** the text `holo run` prints at the end is the task's result.
- **Stopping:** `stop_task` runs `holo stop` and then ends the process.
- **No steering, no questions:** `holo run` takes no messages mid-run, so a correction
  is refused, and Holo never asks you anything.
- **No warm Codex:** while Holo is on, buddy does not keep a Codex process ready.
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
| `CC_BUDDY_HOLO_MAX_STEPS` | none | A step cap passed to `holo run --max-steps`. |
| `CC_BUDDY_HOLO_KEEP_RUNS` | off | `1` keeps each run's events and screenshots, for debugging. |
| `CC_BUDDY_HOLO_BIN` | `holo` on PATH, else `~/.holo/bin/holo` | The CLI to run. |

The task budget is ten minutes, the same as Codex.

## Checks

- `bridge/tests/test_holo_computer.py` runs the lane against a fake `holo` that writes
  the CLI's real event shape.
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
