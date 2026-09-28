"""One Holo task, driven through holo-desktop-cli's own Python client, so buddy can correct it mid-run.

This file runs under **holo's** Python (``~/.holo/tools/holo-desktop-cli/bin/python``, where ``holo_desktop`` and
its runtime types live), never under buddy's venv: buddy stays free of holo's fifteen dependencies, and the client
that builds the session request (instructions, skills from ``~/.holo``) is the one H ships, not a copy. buddy's
side is ``holo_computer.HoloComputerAgent``, which starts this as a subprocess and talks JSON lines with it:

    stdin, one op per line          stdout, one event per line
    {"op": "run", "goal": ...}      {"ev": "ready", "runtime": "0.1.12", "spawned": false}
    {"op": "steer", "text": ...}    {"ev": "session", "id": "<agent-API session id>"}
    {"op": "cancel"}                {"ev": "progress", "text": "Opening Calculator"}
                                    {"ev": "steered", "text": ...} | {"ev": "stopping"}
                                    {"ev": "final", "answer": ..., "status": "completed", "error": null}
                                    {"ev": "error", "text": ...}

The runtime (``hai-agent-runtime`` on 127.0.0.1) is attached to when one is already listening, else spawned and
**left running** when the task ends, so the next task starts warm; ``--stop-runtime`` ends a runtime this
process spawned. The turn itself is ``session_runner.run_turn``, the same driver ``holo mcp``/``acp``/``serve``
use: it holds the machine-wide desktop lock and honours the CLI's kill switch (double-Esc, ``holo stop``).

A correction is ``send_message`` on the running session. A cancel files the CLI's own stop request (pause, then
cancel at the next action boundary) and, belt and braces, pauses and cancels the session directly. SIGTERM and
a closed stdin (buddy went away) do the same, so the runtime never keeps clicking after buddy stopped watching.

Progress lines carry the model's note or the names of the tools it called, never their arguments: text Holo
types (a password, a message) does not reach the board or the chat.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from holo_desktop.agent_client.client import AgentApiClient
from holo_desktop.agent_client.events import AGENT_ERROR_EVENT, format_event, policy_view
from holo_desktop.agent_client.launcher import SpawnConfig, ensure_running
from holo_desktop.agent_client.session_runner import Session, run_turn
from holo_desktop.killswitch.channel import request_stop
from holo_desktop.settings import load_holo_settings


def emit(**event: Any) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def describe(event: Any) -> Optional[str]:
    """One progress line for a policy step (the note, else the tools' names), or the runtime's error line."""
    view = policy_view(event)
    if view is None:
        return format_event(event) if getattr(event, "type", "") == AGENT_ERROR_EVENT else None
    if view.note and view.note.strip():
        return view.note.strip()
    tools = [call.name for call in view.tool_calls if call.name != "answer"]
    return ("Holo: " + ", ".join(tools)) if tools else None


class Turn:
    def __init__(self, client: AgentApiClient, session: Session) -> None:
        self.client, self.session = client, session
        self.announced = False
        self.pending: list[str] = []          # corrections sent before the session existed
        self.stopping = False

    async def announce(self) -> None:
        """Say the session id once run_turn has created it AND it is running, then deliver corrections queued
        before then. Polled rather than hooked on the first event: a turn that answers at once raises no event.
        The runtime takes a message on a running session (probed live 2026-09-28: 200 while running, 200 while
        paused) but not before it has started, so a correction sent at creation would bounce."""
        while not self.session.session_id:
            await asyncio.sleep(0.05)
        for _ in range(120):                   # up to 30 s for the session to start
            with contextlib.suppress(Exception):
                status = (await self.client.get_status(self.session.session_id)).status
                if getattr(status, "value", status) in ("running", "paused") or getattr(status, "is_terminal", False):
                    break
            await asyncio.sleep(0.25)
        self.announced = True
        emit(ev="session", id=self.session.session_id)
        pending, self.pending = self.pending, []
        for text in pending:
            await self.steer(text)

    async def on_event(self, event: Any) -> None:
        text = describe(event)
        if text:
            emit(ev="progress", text=text)

    async def steer(self, text: str) -> None:
        if not self.session.session_id:
            self.pending.append(text)
            return
        detail = ""
        for attempt in range(4):               # a 409 means "not accepting input right now": a few seconds' grace
            try:
                await self.client.send_message(self.session.session_id, text)
                emit(ev="steered", text=text)
                return
            except Exception as exc:  # noqa: BLE001 — the daemon is told, the task goes on
                response = getattr(exc, "response", None)
                detail = f"{response.status_code} {response.text[:160]}" if response is not None else type(exc).__name__
                if response is None or response.status_code != 409 or attempt == 3:
                    break
                await asyncio.sleep(1.0)
        emit(ev="refused", text=text, reason=detail)

    async def cancel(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        emit(ev="stopping")
        request_stop(time.time())              # the CLI's own kill switch: pause, then cancel at the next step
        session_id = self.session.session_id
        if session_id:
            for action in (self.client.pause, self.client.cancel):
                with contextlib.suppress(Exception):
                    await action(session_id)


async def read_ops(turn: Turn) -> None:
    """stdin, one op per line; EOF means buddy is gone, which is a cancel.

    The blocking reads run on a daemon thread of their own, never the default executor: asyncio.run() waits for
    the default executor's threads at shutdown, and a thread parked in readline() on an open pipe would keep
    this process alive after its final event (seen live 2026-09-28: buddy waited for an EOF that never came)."""
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str] = asyncio.Queue()

    def pump() -> None:
        while True:
            line = sys.stdin.readline()
            loop.call_soon_threadsafe(lines.put_nowait, line)
            if not line:
                return

    threading.Thread(target=pump, name="holo-driver-stdin", daemon=True).start()
    while True:
        line = await lines.get()
        if not line:
            await turn.cancel()
            return
        try:
            op = json.loads(line)
        except ValueError:
            continue
        if op.get("op") == "steer" and str(op.get("text") or "").strip():
            await turn.steer(str(op["text"]).strip())
        elif op.get("op") == "cancel":
            await turn.cancel()


async def main(args: argparse.Namespace) -> int:
    first = sys.stdin.readline()
    try:
        goal = str(json.loads(first).get("goal") or "").strip()
    except ValueError:
        goal = ""
    if not goal:
        emit(ev="error", text="no goal: the first line on stdin must be {\"op\": \"run\", \"goal\": ...}")
        return 2
    settings = load_holo_settings()
    try:
        daemon = await ensure_running(
            SpawnConfig(port=args.port, model=args.model, runs_dir=Path(args.runs_dir) if args.runs_dir else None,
                        require_fresh_for_config=False), settings=settings)
    except Exception as exc:  # noqa: BLE001 — no runtime: permissions, key, download; the daemon reports it
        emit(ev="error", text=f"runtime not available: {exc}")
        return 2
    emit(ev="ready", runtime=daemon.runtime_version, spawned=daemon.proc is not None)
    session = Session()
    code = 0
    try:
        async with AgentApiClient(daemon.base_url, daemon.token) as client:
            turn = Turn(client, session)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(turn.cancel()))
            reader = asyncio.create_task(read_ops(turn))
            announcer = asyncio.create_task(turn.announce())
            try:
                outcome = await run_turn(client, session, goal, max_steps=args.max_steps,
                                         max_time_s=args.max_time_s, on_event=turn.on_event)
            finally:
                for task in (reader, announcer):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            status = outcome.status.value if outcome.status is not None else None
            metrics: Any = None
            if outcome.session_id:
                with contextlib.suppress(Exception):   # steps and cost_per_model, when the runtime still has them
                    changes = await client.get_changes(outcome.session_id, 0, wait_for_seconds=0, include_events=False)
                    metrics = changes.metrics.model_dump(mode="json") if changes is not None and changes.metrics else None
            emit(ev="final", answer=outcome.answer, status=status, error=outcome.error, session_id=outcome.session_id,
                 metrics=metrics)
    except Exception as exc:  # noqa: BLE001 — the daemon gets the type, never a stack trace on its stdout
        emit(ev="error", text=f"{type(exc).__name__}: {exc}")
        code = 1
    finally:
        if args.stop_runtime and daemon.proc is not None:
            with contextlib.suppress(Exception):
                await daemon.aclose()
    return code


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=18795)
    p.add_argument("--model", default=None)
    p.add_argument("--runs-dir", default=None)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--max-time-s", type=float, default=600.0)
    p.add_argument("--stop-runtime", action="store_true", help="end a runtime this process spawned (default: leave it warm)")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse(sys.argv[1:]))))
