"""Exercise the actual framed transport against a fake Mac owner, with no model calls."""
import asyncio
import contextlib
import json
import sqlite3
import struct
import tempfile
import time
from pathlib import Path

import pytest

from cc_buddy_bridge.codex_relay import CodexRelay, RelayError, recent_tasks

TASK = '00000000-0000-4000-8000-000000000001'


def turn(id='old', *, status='completed', text='public answer', started=1):
    return {'turnId': id, 'status': status, 'turnStartedAtMs': started, 'items': [
        {'type': 'reasoning', 'text': 'secret thinking'},
        {'type': 'commandExecution', 'aggregatedOutput': 'private tool output'},
        {'type': 'agentMessage', 'phase': 'commentary', 'text': 'working'},
        {'type': 'agentMessage', 'phase': 'final_answer', 'text': text},
    ]}


class Owner:
    def __init__(self):
        self.state = {'id': TASK, 'cwd': '/project', 'turns': [turn()], 'requests': []}
        self.received = []
        self.writer = None
        self.reject = None
        self.version = 11
        self.silent = False
        self.drop = None
        self.version_replies = []
        self.closed = asyncio.Event()

    async def write(self, message):
        data = json.dumps(message).encode()
        self.writer.write(struct.pack('<I', len(data)) + data)
        await self.writer.drain()

    async def broadcast(self, change=None, **kw):
        message = dict(type='broadcast', method='thread-stream-state-changed', version=self.version,
                       sourceClientId='owner', targetClientIds=['buddy'],
                       params={'hostId': 'local', 'conversationId': TASK,
                               'change': change or {'type': 'snapshot', 'revision': 1,
                                                    'conversationState': self.state}})
        message.update(kw)
        await self.write(message)

    async def handle(self, reader, writer):
        self.writer = writer
        try:
            while True:
                size = struct.unpack('<I', await reader.readexactly(4))[0]
                message = json.loads(await reader.readexactly(size))
                self.received.append(message)
                method = message.get('method')
                if method == 'thread-stream-following-changed':
                    if message['params']['following'] and not self.silent:
                        await self.broadcast()
                elif message['type'] == 'request':
                    if method == self.drop:
                        continue
                    result = {'clientId': 'buddy'} if method == 'initialize' else {'ok': True}
                    response = dict(type='response', requestId=message['requestId'], method=method,
                                    resultType='success', handledByClientId='owner', result=result)
                    if method == self.reject:
                        response.update(resultType='error', error='request-version-mismatch')
                    await self.write(response)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.closed.set()


@contextlib.asynccontextmanager
async def rig(**kw):
    with tempfile.TemporaryDirectory(prefix='cx-', dir='/tmp') as root:
        home = Path(root)
        (home / 'ipc').mkdir()
        owner = Owner()
        server = await asyncio.start_unix_server(owner.handle, path=str(home / 'ipc/ipc.sock'))
        relay = CodexRelay(home=home, timeout=0.3, refresh_delay=0.01, **kw)
        output = []

        async def emit(text):
            output.append(text)

        try:
            yield owner, relay, output, emit
        finally:
            await relay.close()
            server.close()
            await server.wait_closed()
            if owner.writer is not None:
                await asyncio.wait_for(owner.closed.wait(), 1)


def test_transport_start_steer_interrupt_and_unfollow():
    async def go():
        async with rig() as (owner, relay, output, emit):
            await relay.attach(TASK, emit)
            assert relay.connected and not output  # no history replay
            await relay.send('new prompt')
            start = next(m for m in owner.received if m.get('method') == 'thread-follower-start-turn')
            assert start['targetClientId'] == 'owner' and start['version'] == 2
            assert start['params']['turnStart'] == {
                'request': {'threadId': TASK, 'input': [{'type': 'text', 'text': 'new prompt', 'text_elements': []}]},
                'context': {'inheritThreadSettings': True},
            }
            owner.state['turns'].append(turn('active', status='inProgress', started=time.time()*1000))
            await relay.send('follow up')  # fetches fresh state before choosing steer
            steer = next(m for m in owner.received if m.get('method') == 'thread-follower-steer-turn')
            assert steer['params']['input'][0]['text'] == 'follow up'
            assert steer['params']['restoreMessage']['context'] == {}
            await relay.interrupt()
            stop = next(m for m in owner.received if m.get('method') == 'thread-follower-interrupt-turn')
            assert stop['version'] == 4
            assert stop['params']['expectedTurnId'] == 'active'
            assert stop['params']['mode'] == 'user-stop'
            await relay.close()
            await asyncio.wait_for(owner.closed.wait(), 1)
            assert owner.received[-1]['params']['following'] is False
            assert not relay.connected
    asyncio.run(go())


def test_refresh_final_only_once_and_target_filter():
    async def go():
        async with rig() as (owner, relay, output, emit):
            await relay.attach(TASK, emit)
            newer = turn('new', text='finished', started=time.time()*1000)
            owner.state['turns'].append(newer)
            await owner.broadcast(sourceClientId='wrong-owner')
            await owner.broadcast(targetClientIds=['someone-else'])
            await asyncio.sleep(0.01)
            assert not output
            await owner.broadcast({'type': 'patches', 'baseRevision': 1, 'revision': 2, 'patches': []})
            await asyncio.sleep(0.04)
            assert output == ['finished']
            await owner.broadcast()
            await asyncio.sleep(0.01)
            assert output == ['finished']
            # Loading older history after attach must not leak historical replies.
            owner.state['turns'].insert(0, turn('historical', text='past conversation'))
            await owner.broadcast()
            await asyncio.sleep(0.01)
            assert output == ['finished']
    asyncio.run(go())


def test_canonical_active_at_attach_and_pending_approval():
    async def go():
        async with rig() as (owner, relay, output, emit):
            active = turn('active', status='inProgress')
            owner.state.pop('turns')
            owner.state['turnHistory'] = {'kind': 'canonical', 'history': {'entitiesByKey': {'tail:1': active}}}
            await relay.attach(TASK, emit)
            owner.state['requests'] = [{'id': 'approval-1', 'method': 'item/commandExecution/requestApproval'}]
            await owner.broadcast()
            await asyncio.sleep(0.01)
            assert len(output) == 1 and 'Mac app' in output[0]
            with pytest.raises(RelayError, match='approval'):
                await relay.send('yes')
            assert not any(m.get('method', '').endswith('approval-decision') for m in owner.received)
            owner.state['requests'] = []
            active['status'] = 'completed'
            await owner.broadcast()
            await asyncio.sleep(0.01)
            assert output[-1] == 'public answer'
    asyncio.run(go())


@pytest.mark.parametrize('failure', ['version', 'disconnect', 'owner-disconnect', 'frame', 'malformed', 'wrong-task'])
def test_protocol_failures_disconnect_without_retry(failure):
    async def go():
        async with rig() as (owner, relay, output, emit):
            await relay.attach(TASK, emit)
            if failure == 'version':
                owner.version = 999
                await owner.broadcast()
            elif failure == 'disconnect':
                owner.writer.close()
            elif failure == 'owner-disconnect':
                await owner.write(dict(type='broadcast', method='client-status-changed',
                                       params={'clientId': 'owner', 'status': 'disconnected'}))
            elif failure == 'malformed':
                await owner.write(['bad shape'])
            elif failure == 'wrong-task':
                owner.state['id'] = 'different'
                await owner.broadcast()
            else:
                owner.writer.write(struct.pack('<I', 100_000_000))
                await owner.writer.drain()
            await asyncio.sleep(0.01)
            assert not relay.connected
            assert output and 'disconnected' in output[-1]
            with pytest.raises(RelayError):
                await relay.send('do not retry')
            assert not any(m.get('method') == 'thread-follower-start-turn' for m in owner.received)
    asyncio.run(go())


def test_rejection_and_silent_owner_are_explicit():
    async def go():
        async with rig() as (owner, relay, output, emit):
            await relay.attach(TASK, emit)
            owner.reject = 'thread-follower-start-turn'
            with pytest.raises(RelayError, match='could not confirm'):
                await relay.send('hello')
            assert sum(m.get('method') == owner.reject for m in owner.received) == 1
            owner.silent = True
            with pytest.raises(RelayError, match='stopped responding'):
                await relay.send('never sent')
            assert sum(m.get('method') == owner.reject for m in owner.received) == 1
    asyncio.run(go())


def test_missing_app_and_bad_id(tmp_path):
    async def go():
        relay = CodexRelay(home=tmp_path)
        with pytest.raises(RelayError, match='task ID'):
            await relay.attach('invalid', None)
        with pytest.raises(RelayError, match='Could not attach'):
            await relay.attach(TASK, None)
        assert not relay.connected
    asyncio.run(go())


def test_readonly_task_catalog_filters_archived_and_agents(tmp_path):
    path = tmp_path / 'state_5.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE threads (id, title, archived, agent_nickname, updated_at)')
        db.executemany('INSERT INTO threads VALUES (?, ?, ?, ?, ?)', [
            (TASK, 'My task', 0, None, 20), ('other', 'Older', 0, None, 10),
            ('archived', 'Archived', 1, None, 30), ('agent', 'Agent', 0, 'worker', 40),
        ])
    before = path.read_bytes()
    assert [t.title for t in recent_tasks(tmp_path)] == ['My task', 'Older']
    assert path.read_bytes() == before


def test_failed_attach_closes_and_discovery_declines_capabilities():
    async def go():
        async with rig() as (owner, relay, output, emit):
            owner.reject = 'thread-owner-discovery'
            with pytest.raises(RelayError):
                await relay.attach(TASK, emit)
            assert not relay.connected
        async with rig() as (owner, relay, output, emit):
            await relay.attach(TASK, emit)
            await owner.write({'type': 'client-discovery-request', 'requestId': 'discover', 'request': {}})
            await asyncio.sleep(0.01)
            reply = next(m for m in owner.received if m['type'] == 'client-discovery-response')
            assert reply['response'] == {'canHandle': False}
    asyncio.run(go())


def test_unconfirmed_prompt_is_never_retried():
    async def go():
        async with rig() as (owner, relay, output, emit):
            await relay.attach(TASK, emit)
            owner.drop = 'thread-follower-start-turn'
            with pytest.raises(RelayError, match='may have received'):
                await relay.send('one prompt')
            assert sum(m.get('method') == owner.drop for m in owner.received) == 1
    asyncio.run(go())
