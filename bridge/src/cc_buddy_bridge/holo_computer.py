"""Buddy's computer tasks through H Company's HoloDesktop CLI (``holo run``), as the desktop floor instead of Codex.

CC_BUDDY_COMPUTER=holo turns it on; Codex stays the floor otherwise. Each task is one ``holo run`` subprocess on
the visible desktop, on the hosted Models API (the key ``holo login`` or the owner saved in ``~/.holo/.env``; the
CLI reads it itself, buddy never touches it). The run's answer is the final text; its ``events.jsonl`` is tailed
for progress while it works. The run directory holds desktop screenshots, so it is deleted when the task ends
unless CC_BUDDY_HOLO_KEEP_RUNS=1.

Two drivers, one contract. The **client** driver (the default when holo's own Python is installed,
``CC_BUDDY_HOLO_DRIVER=client``) runs ``holo_driver.py`` under ``~/.holo/tools/holo-desktop-cli/bin/python``: it
talks to the runtime through holo-desktop-cli's Python client, so a correction from the owner mid-task reaches
Holo (``steer`` sends it to the running session), a stop pauses and cancels the session at the next action
boundary, and the runtime stays warm between tasks. The **cli** driver (``CC_BUDDY_HOLO_DRIVER=cli``, or when
that Python is missing) is one ``holo run`` per task as before: no steering, progress tailed from the run's
``events.jsonl``. Neither asks the owner questions: Holo has no such move.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
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
DRIVERS = ('client', 'cli')
# holo's own Python, where holo_desktop lives (the consumer installer's uv tool env). holo_driver.py runs there.
DRIVER_PYTHON = Path('~/.holo/tools/holo-desktop-cli/bin/python').expanduser()
DRIVER_PORT = 18795                    # the CLI's default agent-API port: one warm runtime, shared with `holo run`
STOP_GRACE_SECS = 8                    # a stop is step-bounded: the runtime finishes the action in flight first


@dataclass(frozen=True)
class HoloConfig:
    enabled: bool
    model: str = DEFAULT_MODEL
    max_steps: Optional[int] = None
    keep_runs: bool = False
    driver: str = 'client'             # client | cli (see the module docstring)
    python: Path = DRIVER_PYTHON       # the interpreter holo_driver.py runs under, CC_BUDDY_HOLO_PYTHON


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
    driver = (env.get('CC_BUDDY_HOLO_DRIVER') or 'client').strip().lower()
    python = (env.get('CC_BUDDY_HOLO_PYTHON') or '').strip()
    return HoloConfig(enabled=on, model=(env.get('CC_BUDDY_HOLO_MODEL') or DEFAULT_MODEL).strip(),
                      max_steps=int(steps) if steps.isdigit() and int(steps) > 0 else None,
                      keep_runs=(env.get('CC_BUDDY_HOLO_KEEP_RUNS') or '').strip().lower() in ON,
                      driver=driver if driver in DRIVERS else 'client',
                      python=Path(python).expanduser() if python else DRIVER_PYTHON)


def driver_python(config: HoloConfig) -> Optional[Path]:
    """The interpreter the client driver runs under, or None: then the lane is one ``holo run`` per task."""
    if config.driver != 'client':
        return None
    return config.python if config.python.is_file() else None


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


class HoloRuntime:
    """The daemon-owned agent-API runtime: ``holo agent-api`` kept as the daemon's child, so every task starts
    warm and each driver only attaches. The runtime binary shuts itself down when its launching parent goes
    (seen live 2026-09-28: "launching parent pid is gone; stopping running turns"), so a runtime a driver
    spawned dies with that driver; this one lives as long as the daemon, and ends with it (``close``).

    The token is generated here and handed to both sides through ``HAI_AGENT_RUNTIME_API_TOKEN``: the runtime
    reads it at start, the driver's ``ensure_running`` sees it in its environment and attaches. When another
    runtime already listens on the port (the owner's own ``holo run``), nothing is spawned and the driver attaches
    to that one through the token file its spawner published."""

    def __init__(self, binary: str | None = None, model: str = DEFAULT_MODEL, port: int = DRIVER_PORT,
                 runs_dir: Path | None = None):
        self.binary = binary or executable()
        self.model, self.port = model, port
        self.runs_dir = runs_dir or RUNS_DIR
        self.token = secrets.token_urlsafe(32)
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @property
    def spawned(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def healthy(self) -> bool:
        """GET /health on the port, as the launcher's probe does."""
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.port), 2)
        except (OSError, asyncio.TimeoutError):
            return False
        try:
            writer.write(f'GET /health HTTP/1.0\r\nHost: 127.0.0.1:{self.port}\r\n\r\n'.encode())
            await writer.drain()
            status = await asyncio.wait_for(reader.readline(), 2)
            return b' 200 ' in status
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def ensure(self) -> dict[str, str]:
        """The environment a driver needs to attach: the token when the runtime is ours; nothing when another
        runtime already listens. Spawns ours when none does. Raises RuntimeError when it cannot come up."""
        async with self._lock:
            if self.spawned:
                return {'HAI_AGENT_RUNTIME_API_TOKEN': self.token}
            if await self.healthy():
                return {}
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, 'HAI_AGENT_RUNTIME_API_TOKEN': self.token,
                   'HAI_AGENT_RUNTIME_RUNS_DIR': str(self.runs_dir)}
            self._proc = await asyncio.create_subprocess_exec(
                self.binary, 'agent-api', '--port', str(self.port), '--model', self.model, env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            deadline = asyncio.get_running_loop().time() + 45      # the launcher's own SPAWN_TIMEOUT_S
            while asyncio.get_running_loop().time() < deadline:
                if self._proc.returncode is not None:
                    raise RuntimeError(f'holo agent-api exited {self._proc.returncode} before it was healthy')
                if await self.healthy():
                    log.info('holo: runtime up on port %s (model %s), kept warm', self.port, self.model)
                    return {'HAI_AGENT_RUNTIME_API_TOKEN': self.token}
                await asyncio.sleep(0.25)
            await self.close()
            raise RuntimeError('holo agent-api did not become healthy in 45 s')

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


class HoloComputerAgent:
    """The run/steer/cancel/status contract (codex_computer.CodexComputerAgent's) over ``holo run``."""

    provider = 'holo'

    def __init__(self, *, on_event: Callable[[AgentEvent], None],
                 ask_user: Callable[[str], Awaitable[str]], binary: str | None = None,
                 config: HoloConfig | None = None, max_secs: float = 600, runs_dir: Path | None = None,
                 driver_script: Path | None = None, runtime: HoloRuntime | None = None):
        self.on_event, self.ask_user = on_event, ask_user
        self.binary = binary or executable()
        self.driver_script = driver_script or Path(__file__).with_name('holo_driver.py')
        self.runtime = runtime                    # the daemon's warm runtime; None: the driver spawns its own
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
        self.python = driver_python(self.config)          # None: the cli driver
        self.driver = 'client' if self.python is not None else 'cli'
        self.session_id: str | None = None               # client driver: the agent-API session, once known
        self.steered: list[str] = []                     # corrections handed to the driver this task
        self._driver_final: dict[str, Any] | None = None
        self._driver_error = ''
        self._stop_asked = False
        if self.config.driver == 'client' and self.python is None:
            log.info('holo: no holo Python at %s; the cli driver (holo run) carries tasks, with no steering',
                     self.config.python)

    @property
    def steerable(self) -> bool:
        """True while a correction can reach Holo: the client driver, a task running, not stopping."""
        return self.driver == 'client' and self.running and not self._cancelled and self._proc is not None \
            and self._proc.returncode is None

    def _emit(self, kind: str, text: str = '') -> None:
        self.on_event(AgentEvent(kind, text))

    def status(self) -> dict[str, Any]:
        return dict(running=self.running, provider=self.provider, goal=self.goal, driver=self.driver,
                    last=self.last_commentary, final=self.final, thread_id=self.thread_id)

    def steer(self, text: str) -> bool:
        """A correction for the running task. Client driver: sent to the session (queued by the driver until the
        session exists). Cli driver: False, `holo run` takes no messages mid-run."""
        text = (text or '').strip()
        if not text or not self.steerable:
            return False
        self.steered.append(text)
        self._send({'op': 'steer', 'text': text})
        return True

    def _send(self, op: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            return
        with contextlib.suppress(OSError, RuntimeError):
            proc.stdin.write((json.dumps(op) + '\n').encode())

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
        if self.driver == 'client':
            # The driver pauses and cancels the session (the CLI's own stop path) and exits on its own; a stop is
            # step-bounded, so it gets STOP_GRACE_SECS. Terminating it first would leave the runtime acting. Its
            # last lines are read meanwhile: the final says what Holo had reached before the stop landed.
            if not self._stop_asked:
                self._stop_asked = True
                self._send({'op': 'cancel'})
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._drain(proc), STOP_GRACE_SECS)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 1)
        else:
            # `holo stop` ends the turn in the runtime; terminating the client alone can leave it acting.
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                stopper = await asyncio.create_subprocess_exec(self.binary, 'stop', stdout=asyncio.subprocess.DEVNULL,
                                                               stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(stopper.wait(), 5)
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    # -- the client driver: holo_driver.py under holo's Python, JSON lines both ways --

    async def _drain(self, proc: asyncio.subprocess.Process) -> None:
        """The driver's remaining lines, to EOF."""
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            self._on_driver_line(line)

    def _stopped_text(self) -> str:
        """The stop verdict. A stop that reached the task never certifies a result (HoloSteer.lean); what Holo had
        already finished is said, not certified."""
        final = self._driver_final or {}
        answer = str(final.get('answer') or '').strip()
        reached = f' It had reached: {answer}' if answer and str(final.get('status') or '') in ('completed', 'idle') else ''
        return 'Holo task stopped. The requested result is not verified.' + reached

    def _driver_command(self) -> list[str]:
        cmd = [str(self.python), str(self.driver_script), '--port', str(DRIVER_PORT),
               '--model', self.config.model, '--max-time-s', str(int(self.max_secs)), '--runs-dir', str(self.runs_root)]
        if self.config.max_steps:
            cmd += ['--max-steps', str(self.config.max_steps)]
        return cmd

    def _on_driver_line(self, line: bytes) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        kind = event.get('ev')
        if kind == 'session':
            self.session_id = self.thread_id = str(event.get('id') or '') or None
        elif kind == 'progress':
            text = str(event.get('text') or '').strip()
            if text and text != self.last_commentary:
                self.last_commentary = text
                self._emit('progress', text)
        elif kind == 'steered':
            log.info('holo: correction delivered to the session')
        elif kind == 'refused':
            # The owner hears it: a correction that bounced must not look taken (steer() had said True).
            reason = str(event.get('reason') or '')
            log.warning('holo: correction refused by the runtime: %s', reason[-200:])
            self._emit('progress', 'Holo did not take the correction' + (f' ({reason[:80]})' if reason else '') + '.')
        elif kind == 'final':
            self._driver_final = event
            metrics = event.get('metrics') or {}
            if isinstance(metrics, dict) and metrics:
                log.info('holo: session %s: %s steps, cost %s', event.get('session_id'), metrics.get('steps'),
                         metrics.get('cost_per_model'))
            if isinstance(event.get('answer'), str) and event['answer']:
                self.ui_evidence.append({'call_id': event.get('session_id'), 'state': 'holo answer'})
        elif kind == 'error':
            self._driver_error = str(event.get('text') or '')
            log.warning('holo driver: %s', self._driver_error[-400:])
        elif kind == 'ready':
            log.info('holo: runtime %s (%s)', event.get('runtime') or '?',
                     'spawned, left warm' if event.get('spawned') else 'attached')

    async def _run_client(self, goal: str) -> None:
        before = {p.name for p in self.runs_root.glob('*') if p.is_dir()} if self.runs_root.is_dir() else set()
        env = dict(os.environ)
        if self.runtime is not None:
            try:
                env.update(await self.runtime.ensure())
            except RuntimeError as exc:
                log.warning('holo: %s', exc)
                self.final = 'Holo could not start. No other computer-use lane was tried.'
                self._emit('error', self.final)
                return
        self._proc = await asyncio.create_subprocess_exec(
            *self._driver_command(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env)
        self._send({'op': 'run', 'goal': goal})
        try:
            assert self._proc.stdout is not None
            while self._driver_final is None:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                self._on_driver_line(line)
            # The final is the last word; a driver that lingers after it (a stuck thread, a slow runtime
            # teardown) is not waited on past a few seconds.
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                log.warning('holo: driver did not exit after its final; terminated')
                self._proc.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._proc.wait(), 3)
        finally:
            # The runtime's run directories hold desktop screenshots: the ones this task made go, unless kept.
            if not self.config.keep_runs and self.runs_root.is_dir():
                for p in self.runs_root.glob('*'):
                    if p.is_dir() and p.name not in before:
                        shutil.rmtree(p, ignore_errors=True)
        final = self._driver_final
        if final is None:
            err = self._driver_error or (await self._proc.stderr.read()).decode(errors='replace')[-400:]
            log.warning('holo: driver exited %s without a result: %s', self._proc.returncode, err)
            self.final = 'Holo could not complete the task. The requested result is not verified.'
            self._emit('error', self.final)
            return
        status = str(final.get('status') or '')
        answer = str(final.get('answer') or '').strip()
        if self._stop_asked or status in ('cancelled', 'interrupted'):
            # A stop was asked (ours), or one came from outside buddy (double-Esc, `holo stop`). A stop that
            # reached the task reports "stopped", never a result (verification/Buddy/HoloSteer.lean, and the
            # same rule as TaskStop.lean); what Holo had already finished is said, not certified.
            self.final = self._stopped_text()
            self._emit('cancelled', self.final)
        elif status in ('completed', 'idle'):
            self.final = answer or 'Holo ended without a result.'
            self._emit('final', self.final)
        else:
            log.warning('holo: session ended %s: %s', status or '?', str(final.get('error') or '')[-400:])
            self.final = 'Holo could not complete the task. The requested result is not verified.'
            self._emit('error', self.final)

    # -- the cli driver: one `holo run`, progress tailed from the run's events.jsonl --

    async def _run_cli(self, goal: str, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        follower: asyncio.Task | None = None
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command(goal, run_dir), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            follower = asyncio.create_task(self._follow(run_dir))
            out, err = await self._proc.communicate()
        finally:
            if follower is not None:
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

    async def run(self, goal: str) -> str:
        if self.running:
            raise RuntimeError('A Holo task is already running.')
        self._runner = asyncio.current_task()
        self.running = True
        self.goal, self.final, self.last_commentary, self.thread_id = goal, '', '', None
        self.session_id, self.steered, self._driver_final, self._driver_error = None, [], None, ''
        self._stop_asked = False
        self.ui_evidence = []
        self._events, self._offset = None, 0
        run_dir = self.runs_root / uuid.uuid4().hex          # cli driver only
        self._emit('started', goal)
        spend.record('hcompany', self.config.model, spend.TASKS, None, note=f'holo {self.driver}; priced per token by H')
        try:
            if self._cancelled:
                raise asyncio.CancelledError
            async with asyncio.timeout(self.max_secs + 30):
                if self.driver == 'client':
                    await self._run_client(goal)
                else:
                    await self._run_cli(goal, run_dir)
        except asyncio.CancelledError:
            self._cancelled = True
            await self._stop()                  # reads the driver's final first: it may say what was reached
            self.final = self._stopped_text()
            self._emit('cancelled', self.final)
        except (OSError, TimeoutError) as exc:
            log.warning('holo: %s', type(exc).__name__)
            self.final = 'Holo could not start or timed out. No other computer-use lane was tried.'
            self._emit('error', self.final)
        finally:
            await self._stop()
            if self.driver == 'cli' and not self.config.keep_runs:
                shutil.rmtree(run_dir, ignore_errors=True)
            self._proc = None
            self.running = False
            self._runner = None
        return self.final
