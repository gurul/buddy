"""A follower of an existing local Codex desktop task; never a second app-server writer.

The desktop IPC protocol is private and versioned. Reject errors instead of retrying
prompts (a timeout may mean the app accepted one). No permission decisions are sent.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import stat
import struct
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_FRAME = 32 * 1024 * 1024


class RelayError(Exception):
    """A user-readable relay failure, with no transcript or credentials."""


@dataclass(frozen=True)
class DesktopTask:
    id: str
    title: str


def recent_tasks(home: Path | None = None) -> list[DesktopTask]:
    """Read metadata only, with SQLite's read-only mode (including its live WAL)."""
    home = home or Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    databases = sorted(home.glob('state_*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not databases:
        raise RelayError('No Codex tasks found. Open a task in the Mac app first.')
    try:
        with contextlib.closing(sqlite3.connect(databases[0].as_uri() + '?mode=ro', uri=True)) as db:
            rows = db.execute('SELECT id, title FROM threads WHERE archived = 0 '
                              'AND agent_nickname IS NULL ORDER BY updated_at DESC LIMIT 10').fetchall()
    except sqlite3.Error as exc:
        raise RelayError('Could not read the Mac app task list.') from exc
    return [DesktopTask(id, title or id) for id, title in rows]


class CodexRelay:
    def __init__(self, *, home: Path | None = None, timeout: float = 10, refresh_delay: float = 1.2):
        self.home = home or Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        self.timeout, self.refresh_delay = timeout, refresh_delay
        self.client_id = 'initializing-client'
        self.owner: str | None = None
        self.thread_id: str | None = None
        self.state: dict[str, Any] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._reader_job: asyncio.Task | None = None
        self._refresh_job: asyncio.Task | None = None
        self._delivery_job: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ready = asyncio.Event()
        self._snapshot_event = asyncio.Event()
        self._messages: asyncio.Queue[str] = asyncio.Queue()
        self._seen: set[str] = set()
        self._requests: set[str] = set()
        self._seeded = False
        self._since_ms = 0.0
        self._initial_active: set[str] = set()
        self._failed = False

    @property
    def connected(self) -> bool:
        return self._ready.is_set() and not self._failed and self._writer is not None

    async def attach(self, thread_id: str, emit: Callable[[str], Awaitable[None]]) -> None:
        await self.close()
        # Reject accidental paths, prefixes and malformed task IDs before discovery.
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise RelayError('Use a task number from codex on, or a full Codex task ID.') from exc
        self.thread_id = thread_id
        self._failed, self._seeded = False, False
        self._ready.clear()
        self._seen.clear()
        self._initial_active.clear()
        self._since_ms = time.time() * 1000
        self._requests.clear()
        self._messages = asyncio.Queue()
        self.client_id = 'initializing-client'
        sock = self.home / 'ipc' / 'ipc.sock'
        try:
            info = sock.stat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise RelayError('The Codex socket is not owned by this Mac user.')
            reader, self._writer = await asyncio.open_unix_connection(str(sock))
            self._reader_job = asyncio.create_task(self._read(reader))
            hello = await self._request('initialize', {'clientType': 'buddy-telegram'}, version=0)
            self.client_id = hello['result']['clientId']
            owner = await self._request('thread-owner-discovery', {'hostId': 'local', 'conversationId': thread_id})
            self.owner = owner['handledByClientId']
            await self._follow(True)
            await asyncio.wait_for(self._ready.wait(), self.timeout)
            if self._failed:
                raise RelayError('The Mac app disconnected while attaching. Try codex on again.')
            self._delivery_job = asyncio.create_task(self._deliver(emit))
        except asyncio.CancelledError:
            await self.close()
            raise
        except (OSError, TimeoutError, KeyError, RelayError) as exc:
            await self.close()
            if isinstance(exc, RelayError):
                raise
            raise RelayError('Could not attach. Open that Codex task in the Mac app, then try codex on again.') from exc

    async def _write(self, message: dict[str, Any]) -> None:
        if self._writer is None:
            raise RelayError('Codex is disconnected. Use codex on to reconnect.')
        data = json.dumps(message).encode()
        if len(data) > MAX_FRAME:
            raise RelayError('That message is too large for the Mac app.')
        self._writer.write(struct.pack('<I', len(data)) + data)
        await self._writer.drain()

    async def _request(self, method: str, params: dict, *, version: int = 1) -> dict:
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message = dict(type='request', requestId=request_id, sourceClientId=self.client_id,
                       method=method, version=version, params=params, timeoutMs=int(self.timeout * 1000))
        if self.owner:
            message['targetClientId'] = self.owner
        try:
            await self._write(message)
            response = await asyncio.wait_for(future, self.timeout)
            if response.get('resultType') != 'success':
                raise RelayError('The Mac app could not confirm the request. Check the selected task before retrying.')
            return response
        except (TimeoutError, OSError) as exc:
            raise RelayError('No confirmation from the Mac app. Check the task before retrying; it may have received the message.') from exc
        finally:
            self._pending.pop(request_id, None)

    async def _follow(self, following: bool) -> None:
        await self._write(dict(type='broadcast', method='thread-stream-following-changed', version=1,
                               sourceClientId=self.client_id, targetClientIds=[self.owner],
                               params=dict(hostId='local', conversationId=self.thread_id, following=following)))

    async def _refresh(self) -> None:
        await asyncio.sleep(self.refresh_delay)
        # Reaffirming an existing follow asks the owner for its current snapshot.
        # This also handles transcript text deltas and canonical history without
        # maintaining another implementation of the desktop's patch machinery.
        try:
            await self._follow(True)
        except (OSError, RelayError):
            self._fail()

    async def _read(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                size = struct.unpack('<I', await reader.readexactly(4))[0]
                if not 0 < size <= MAX_FRAME:
                    raise RelayError('Unsupported desktop frame size.')
                message = json.loads(await reader.readexactly(size))
                if not isinstance(message, dict):
                    raise RelayError('Unsupported desktop message.')
                kind = message.get('type')
                if kind == 'response':
                    future = self._pending.get(message.get('requestId'))
                    if future is not None and not future.done():
                        future.set_result(message)
                elif kind == 'client-discovery-request':
                    await self._write(dict(type='client-discovery-response', requestId=message['requestId'],
                                           response={'canHandle': False}))
                elif kind == 'broadcast':
                    targets = message.get('targetClientIds')
                    if targets is not None and self.client_id not in targets:
                        continue
                    params = message.get('params', {})
                    if (message.get('method') == 'client-status-changed'
                            and params.get('clientId') == self.owner and params.get('status') == 'disconnected'):
                        self._fail()
                        return
                    if (message.get('sourceClientId') != self.owner
                            or params.get('conversationId') != self.thread_id
                            or params.get('hostId') != 'local'):
                        continue
                    method = message.get('method')
                    if method == 'thread-stream-following-status-requested':
                        await self._follow(True)
                    elif method == 'thread-stream-state-changed':
                        if message.get('version') != 11:
                            raise RelayError('Unsupported desktop protocol version.')
                        change = params['change']
                        if change['type'] == 'snapshot':
                            self._snapshot(change['conversationState'])
                            self._ready.set()
                            self._snapshot_event.set()
                        elif change['type'] == 'patches':
                            if self._refresh_job is None or self._refresh_job.done():
                                self._refresh_job = asyncio.create_task(self._refresh())
        except (OSError, asyncio.IncompleteReadError, ValueError, KeyError, TypeError, AttributeError, RelayError):
            self._fail()

    def _fail(self) -> None:
        if self._failed:
            return
        self._failed = True
        self._ready.set()
        self._snapshot_event.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RelayError('The Mac app disconnected. Use codex on to reconnect.'))
        self._messages.put_nowait('Codex relay disconnected. Check the Mac app, then use codex on again.')

    def _turns(self) -> list[dict]:
        history = self.state.get('turnHistory', {})
        if history and history.get('kind') == 'canonical':
            return list(history['history']['entitiesByKey'].values())
        return self.state.get('turns', [])

    def _snapshot(self, state: dict) -> None:
        if state.get('id') != self.thread_id:
            raise RelayError('The Mac app returned a different task.')
        self.state = state
        for turn in self._turns():
            key = turn.get('turnId') or turn.get('params', {}).get('clientUserMessageId')
            if turn.get('status') == 'inProgress':
                if not self._seeded and key:
                    self._initial_active.add(key)
                continue
            if not key or key in self._seen:
                continue
            self._seen.add(key)
            if (not self._seeded or (key not in self._initial_active
                                    and (turn.get('turnStartedAtMs') or 0) < self._since_ms)):
                continue
            # Only the public answer, never reasoning, tools, or their output.
            text = '\n\n'.join(item['text'] for item in turn.get('items', [])
                               if item.get('type') == 'agentMessage' and item.get('phase') in (None, 'final_answer')
                               and isinstance(item.get('text'), str))
            if text.strip():
                self._messages.put_nowait(text)
            elif turn.get('status') in ('failed', 'interrupted'):
                self._messages.put_nowait('Codex stopped before finishing. Check the task in the Mac app.')
        requests = {str(r.get('id', r.get('requestId', i))) for i, r in enumerate(state.get('requests', []))}
        if requests - self._requests:
            self._messages.put_nowait('Codex needs your input or approval. Answer in the Mac app to continue.')
        self._requests = requests
        self._seeded = True

    async def _deliver(self, emit: Callable[[str], Awaitable[None]]) -> None:
        while True:
            await emit(await self._messages.get())

    async def _sync_state(self) -> None:
        if not self.connected:
            raise RelayError('Codex is disconnected. Use codex on to reconnect.')
        self._snapshot_event.clear()
        try:
            await self._follow(True)
            await asyncio.wait_for(self._snapshot_event.wait(), self.timeout)
        except (TimeoutError, OSError) as exc:
            self._fail()
            raise RelayError('The task stopped responding. Open it in the Mac app and reconnect.') from exc
        if not self.connected:
            raise RelayError('Codex disconnected. Open the task in the Mac app and reconnect.')

    async def send(self, text: str) -> None:
        await self._sync_state()
        if not self.connected:
            raise RelayError('Codex is disconnected. Use codex on to reconnect.')
        if self.state.get('requests'):
            raise RelayError('Codex is waiting for input or approval. Answer in the Mac app first.')
        inputs = [{'type': 'text', 'text': text, 'text_elements': []}]
        active = any(t.get('status') == 'inProgress' for t in self._turns())
        if active:
            await self._request('thread-follower-steer-turn', {
                'conversationId': self.thread_id, 'input': inputs,
                'clientUserMessageId': str(uuid.uuid4()),
                'restoreMessage': {'text': text, 'cwd': self.state.get('cwd'), 'context': {}},
            })
        else:
            await self._request('thread-follower-start-turn', {
                'conversationId': self.thread_id,
                'turnStart': {'request': {'threadId': self.thread_id, 'input': inputs},
                              'context': {'inheritThreadSettings': True}},
            }, version=2)

    async def interrupt(self) -> None:
        await self._sync_state()
        if not self.connected:
            raise RelayError('Codex is disconnected. Stop the task in the Mac app.')
        active = next((t for t in reversed(self._turns()) if t.get('status') == 'inProgress'), None)
        if not active or not active.get('turnId'):
            raise RelayError('No active Codex turn is visible. Check the Mac app if it is starting.')
        await self._request('thread-follower-interrupt-turn', {
            'conversationId': self.thread_id, 'mode': 'user-stop', 'expectedTurnId': active['turnId'],
        }, version=4)

    async def close(self) -> None:
        if self._writer is not None and self.owner:
            with contextlib.suppress(OSError, RelayError):
                await self._follow(False)
        jobs = [j for j in (self._reader_job, self._refresh_job, self._delivery_job) if j is not None]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(OSError):
                await self._writer.wait_closed()
        self._writer = self.owner = self.thread_id = None
        self._reader_job = self._refresh_job = self._delivery_job = None
        self._ready.clear()
        self.state = {}
