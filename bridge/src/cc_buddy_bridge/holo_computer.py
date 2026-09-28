"""Buddy's computer tasks through H Company's HoloDesktop CLI (``holo run``), as the desktop floor instead of Codex.

CC_BUDDY_COMPUTER=holo turns it on; Codex stays the floor otherwise. Each task is one ``holo run`` subprocess on
the visible desktop, on the hosted Models API (the key ``holo login`` or the owner saved in ``~/.holo/.env``; the
CLI reads it itself, buddy never touches it). The run's answer is the final text; its ``events.jsonl`` is tailed
for progress while it works. The run directory holds desktop screenshots, so it is deleted when the task ends
unless CC_BUDDY_HOLO_KEEP_RUNS=1.

What the CLI does not offer, this lane does not fake: no mid-run steering (``steer`` returns False) and no
questions back to the owner.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from . import spend
from .agent_contract import AgentEvent

log = logging.getLogger(__name__)

DEFAULT_MODEL = 'holo4-27b'
RUNS_DIR = Path('~/.holo/runs/buddy').expanduser()
POLL_SECS = 0.5
ON = ('1', 'true', 'yes', 'on')


@dataclass(frozen=True)
class HoloConfig:
    enabled: bool
    model: str = DEFAULT_MODEL
    max_steps: Optional[int] = None
    keep_runs: bool = False


def executable() -> str:
    """CC_BUDDY_HOLO_BIN, else ``holo`` on PATH, else the installer's ~/.holo/bin/holo."""
    configured_bin = os.environ.get('CC_BUDDY_HOLO_BIN', '').strip()
    if configured_bin:
        return configured_bin
    return shutil.which('holo') or str(Path('~/.holo/bin/holo').expanduser())


def configured(environ: Optional[Mapping[str, str]] = None) -> HoloConfig:
    """CC_BUDDY_COMPUTER = holo | codex (the default); CC_BUDDY_HOLO_MODEL (default holo4-27b);
    CC_BUDDY_HOLO_MAX_STEPS; CC_BUDDY_HOLO_KEEP_RUNS=1 keeps each run's events and screenshots."""
    env = os.environ if environ is None else environ
    on = (env.get('CC_BUDDY_COMPUTER') or 'codex').strip().lower() == 'holo'
    steps = (env.get('CC_BUDDY_HOLO_MAX_STEPS') or '').strip()
    return HoloConfig(enabled=on, model=(env.get('CC_BUDDY_HOLO_MODEL') or DEFAULT_MODEL).strip(),
                      max_steps=int(steps) if steps.isdigit() and int(steps) > 0 else None,
                      keep_runs=(env.get('CC_BUDDY_HOLO_KEEP_RUNS') or '').strip().lower() in ON)


def describe_step(event: dict[str, Any]) -> Optional[str]:
    """One progress line for a policy step: the model's note, else the tools it called. Tool arguments are left
    out, so typed text (a password, a message) never reaches the board or the chat."""
    if event.get('kind') != 'policy_event':
        return None
    message = event.get('message') or {}
    note = None
    with contextlib.suppress(TypeError, ValueError, AttributeError):
        note = (json.loads(message.get('content') or '{}') or {}).get('note')
    if isinstance(note, str) and note.strip():
        return note.strip()
    tools = [r.get('tool_name') for r in event.get('tool_reqs') or [] if r.get('tool_name') not in (None, 'answer')]
    return ('Holo: ' + ', '.join(tools)) if tools else None


class HoloComputerAgent:
    """The run/steer/cancel/status contract (codex_computer.CodexComputerAgent's) over ``holo run``."""

    provider = 'holo'

    def __init__(self, *, on_event: Callable[[AgentEvent], None],
                 ask_user: Callable[[str], Awaitable[str]], binary: str | None = None,
                 config: HoloConfig | None = None, max_secs: float = 600, runs_dir: Path | None = None):
        self.on_event, self.ask_user = on_event, ask_user
        self.binary = binary or executable()
        self.config = config or configured()
        self.max_secs = max_secs
        self.runs_root = runs_dir or RUNS_DIR
        self.running = False
        self.goal = self.final = self.last_commentary = ''
        self.thread_id: str | None = None
        self.browser_used = False
        self.browser_screenshot = None
        self.ui_evidence: list[dict[str, Any]] = []
        self._proc: asyncio.subprocess.Process | None = None
        self._runner: asyncio.Task | None = None
        self._cancelled = False
        self._events: Path | None = None
        self._offset = 0

    def _emit(self, kind: str, text: str = '') -> None:
        self.on_event(AgentEvent(kind, text))

    def status(self) -> dict[str, Any]:
        return dict(running=self.running, provider=self.provider, goal=self.goal,
                    last=self.last_commentary, final=self.final, thread_id=self.thread_id)

    def steer(self, text: str) -> bool:
        return False            # `holo run` takes no messages mid-run

    def cancel(self, reason: str = '') -> None:
        self._cancelled = True
        if self._runner is not None:
            self._runner.cancel()

    def _command(self, goal: str, run_dir: Path) -> list[str]:
        cmd = [self.binary, 'run', '--quiet', '--no-kill-switch', '--model', self.config.model,
               '--max-time-s', str(int(self.max_secs)), '--runs-dir', str(run_dir)]
        if self.config.max_steps:
            cmd += ['--max-steps', str(self.config.max_steps)]
        return cmd + [goal]

    async def _follow(self, run_dir: Path) -> None:
        """Tail the run's events.jsonl (it lands in a subdirectory named for the run) into progress events."""
        while True:
            self._read_new(run_dir)
            await asyncio.sleep(POLL_SECS)

    def _read_new(self, run_dir: Path) -> None:
        """The lines written since the last read. Called once more after the run ends, so a run faster than one
        poll still reports its steps and its answer."""
        if self._events is None:
            self._events = next(run_dir.glob('*/events.jsonl'), None)
        if self._events is None:
            return
        with contextlib.suppress(OSError):
            with open(self._events, 'rb') as f:
                f.seek(self._offset)
                chunk = f.read()
            # Only whole lines: a line still being written is read on the next pass.
            whole = chunk[:chunk.rfind(b'\n') + 1]
            self._offset += len(whole)
            for line in whole.splitlines():
                self._on_line(line)

    def _on_line(self, line: bytes) -> None:
        try:
            record = json.loads(line)
        except ValueError:
            return
        self.thread_id = self.thread_id or record.get('run_id')
        event = record.get('event') or {}
        if event.get('kind') == 'answer_event' and isinstance(event.get('answer'), str):
            self.ui_evidence.append({'call_id': record.get('id'), 'state': 'holo answer'})
        text = describe_step(event)
        if text and text != self.last_commentary:
            self.last_commentary = text
            self._emit('progress', text)

    async def _stop(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        # `holo stop` ends the turn in the runtime; terminating the client alone can leave it acting.
        with contextlib.suppress(OSError, asyncio.TimeoutError):
            stopper = await asyncio.create_subprocess_exec(self.binary, 'stop', stdout=asyncio.subprocess.DEVNULL,
                                                           stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(stopper.wait(), 5)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def run(self, goal: str) -> str:
        if self.running:
            raise RuntimeError('A Holo task is already running.')
        self._runner = asyncio.current_task()
        self.running = True
        self.goal, self.final, self.last_commentary, self.thread_id = goal, '', '', None
        self.ui_evidence = []
        self._events, self._offset = None, 0
        run_dir = self.runs_root / uuid.uuid4().hex
        follower: asyncio.Task | None = None
        self._emit('started', goal)
        spend.record('hcompany', self.config.model, spend.TASKS, None, note='holo run; priced per token by H')
        try:
            if self._cancelled:
                raise asyncio.CancelledError
            run_dir.mkdir(parents=True, exist_ok=True)
            async with asyncio.timeout(self.max_secs + 30):
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command(goal, run_dir), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                follower = asyncio.create_task(self._follow(run_dir))
                out, err = await self._proc.communicate()
            follower.cancel()
            await asyncio.gather(follower, return_exceptions=True)
            self._read_new(run_dir)             # steps written after the follower's last pass, before the verdict
            answer = out.decode(errors='replace').strip()
            if self._proc.returncode != 0:
                log.warning('holo: run exited %s: %s', self._proc.returncode, err.decode(errors='replace')[-400:])
                self.final = 'Holo could not complete the task. The requested result is not verified.'
                self._emit('error', self.final)
            else:
                self.final = answer or 'Holo ended without a result.'
                self._emit('final', self.final)
        except asyncio.CancelledError:
            self._cancelled = True
            self.final = 'Holo task stopped. The requested result is not verified.'
            self._emit('cancelled', self.final)
        except (OSError, TimeoutError) as exc:
            log.warning('holo: %s', type(exc).__name__)
            self.final = 'Holo could not start or timed out. No other computer-use lane was tried.'
            self._emit('error', self.final)
        finally:
            await self._stop()
            if follower is not None:
                follower.cancel()
                await asyncio.gather(follower, return_exceptions=True)
            if not self.config.keep_runs:
                shutil.rmtree(run_dir, ignore_errors=True)
            self._proc = None
            self.running = False
            self._runner = None
        return self.final
