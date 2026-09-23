"""Protocol contract tests; a separate recorded native-app check proves the real plugin."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from cc_buddy_bridge.codex_computer import CodexComputerAgent, external_environment
from cc_buddy_bridge.daemon import Daemon


class Process:
    def __init__(self, *, available=True, mode='complete', answer_schema=None):
        self.stdout = asyncio.StreamReader(limit=32*1024*1024)
        self.stdin = self
        self.returncode = None
        self.requests = []
        self.available, self.mode = available, mode
        self.answer_schema = answer_schema or {'type': 'object', 'properties': {}}
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    def out(self, obj):
        import json
        self.stdout.feed_data((json.dumps(obj)+'\n').encode())

    def event(self, method, **params):
        self.out({'method': method, 'params': {'threadId': 'thread', **params}})

    def write(self, data):
        import json
        m = json.loads(data)
        self.requests.append(m)
        method = m.get('method')
        result = {}
        if method == 'initialize':
            result = {'userAgent': 'test'}
        elif method == 'thread/start':
            result = {'thread': {'id': 'thread'}}
        elif method == 'mcpServerStatus/list':
            if not m['params'].get('cursor'):
                result = {'data': [{'name': 'other', 'tools': {'js': {}}}], 'nextCursor': 'page2'}
            else:
                result = {'data': [{'name': 'cua_repl', 'tools': {'js': {}}}] if self.available else [], 'nextCursor': None}
        elif method == 'turn/start':
            result = {'turn': {'id': 'turn'}}
            self.started.set()
            self.event('item/completed', item={'type': 'agentMessage', 'phase': 'commentary', 'text': 'Opening Calculator'})
            if self.mode == 'approval':
                self.out({'id': 100, 'method': 'mcpServer/elicitation/request', 'params': {
                    'threadId': 'thread', 'serverName': 'cua_repl', 'mode': 'form',
                    'message': 'Allow Computer Use to use "Calculator"?', 'requestedSchema': self.answer_schema}})
            elif self.mode == 'disconnect':
                self.stdout.feed_eof()
            elif self.mode == 'complete':
                self.complete()
        elif method == 'turn/interrupt':
            self.event('turn/completed', turn={'id': 'turn', 'status': 'interrupted'})
        elif method == 'turn/steer':
            result = {'turnId': 'turn'}
        elif m.get('id') == 100:
            self.complete()
        if method and 'id' in m and self.mode != 'disconnect':
            self.out({'id': m['id'], 'result': result})

    def complete(self):
        self.event('item/completed', item={'id': 'cua-call', 'type': 'mcpToolCall', 'server': 'cua_repl',
            'tool': 'js', 'status': 'completed', 'result': {'content': [{'type': 'text', 'text': 'Window: Calculator. Value: 4'}]}})
        self.event('item/completed', item={'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Calculator displays 4.'})
        self.event('turn/completed', turn={'id': 'turn', 'status': 'completed'})

    async def drain(self):
        pass

    def terminate(self):
        self.returncode = 0
        if not self.stdout.at_eof():
            self.stdout.feed_eof()
        self.finished.set()

    kill = terminate

    async def wait(self):
        await self.finished.wait()
        return self.returncode


def test_inventory_pagination_progress_result_and_no_credentials():
    async def go():
        p = Process()
        events = []
        async def spawn(*args, **kw):
            assert args[1:] == ('app-server', '--listen', 'stdio://')
            assert 'CODEX_APP_TOOLS_PIPE_PATH' not in kw['env']
            return p
        async def ask(q):
            raise AssertionError(q)
        agent = CodexComputerAgent(on_event=events.append, ask_user=ask)
        with patch('asyncio.create_subprocess_exec', spawn):
            assert await agent.run('Compute 2+2') == 'Calculator displays 4.'
        assert not agent.running and agent.ui_evidence[0]['state'].endswith('4')
        assert any(e.kind == 'progress' and e.text == 'Opening Calculator' for e in events)
        assert any(e.kind == 'final' for e in events)
        start = next(m['params'] for m in p.requests if m.get('method') == 'thread/start')
        assert start['ephemeral'] and start['approvalPolicy'] == 'on-request' and start['sandbox'] == 'read-only'
        assert 'model' not in start
    asyncio.run(go())
    with patch.dict('os.environ', {'CODEX_APP_TOOLS_PIPE_PATH': 'secret', 'OPENAI_API_KEY': 'secret', 'HOME': '/tmp/home'}):
        assert 'OPENAI_API_KEY' not in external_environment()
        assert external_environment()['HOME'] == '/tmp/home'


def test_missing_tool_never_starts_a_turn_positive_control_in_previous_test():
    async def go():
        p = Process(available=False)
        async def spawn(*a, **kw): return p
        async def ask(q): raise AssertionError(q)
        agent = CodexComputerAgent(on_event=lambda e: None, ask_user=ask)
        with patch('asyncio.create_subprocess_exec', spawn):
            assert 'no cua_repl.js' in await agent.run('do something')
        assert not any(m.get('method') == 'turn/start' for m in p.requests)
    asyncio.run(go())


def test_approvals_are_once_explicit_and_unknown_forms_fail_closed():
    async def go(answer, schema=None):
        p = Process(mode='approval', answer_schema=schema)
        questions = []
        async def spawn(*a, **kw): return p
        async def ask(q):
            questions.append(q)
            return answer
        agent = CodexComputerAgent(on_event=lambda e: None, ask_user=ask)
        with patch('asyncio.create_subprocess_exec', spawn):
            await agent.run('Compute 2+2')
        reply = next(m for m in p.requests if m.get('id') == 100)
        assert '_meta' not in reply.get('result', {})  # no saved/always permission
        return reply, questions
    for answer, expected in [('yes', 'accept'), ('no', 'decline'), ('yes but not yet', 'decline'), ('', 'decline')]:
        reply, questions = asyncio.run(go(answer))
        assert reply['result']['action'] == expected
        assert questions and 'Calculator' in questions[0]
    reply, questions = asyncio.run(go('yes', {'type': 'object', 'properties': {'secret': {'type': 'string'}}}))
    assert reply['result']['action'] == 'decline' and not questions


def test_cancel_while_approval_waits_interrupts_turn_and_cleans_up():
    async def go():
        p = Process(mode='approval')
        asking = asyncio.Event()
        async def spawn(*a, **kw): return p
        async def ask(q):
            asking.set()
            await asyncio.Future()
        agent = CodexComputerAgent(on_event=lambda e: None, ask_user=ask)
        with patch('asyncio.create_subprocess_exec', spawn):
            run = asyncio.create_task(agent.run('Compute 2+2'))
            await asking.wait()
            agent.cancel()
            assert 'stopped' in await asyncio.wait_for(run, 1)
        assert any(m.get('method') == 'turn/interrupt' for m in p.requests)
        assert not any(m.get('id') == 100 for m in p.requests)
        assert p.returncode == 0 and not agent.running
    asyncio.run(go())


def test_steering_and_disconnect():
    async def go():
        p = Process(mode='hold')
        async def spawn(*a, **kw): return p
        async def ask(q): return 'no'
        agent = CodexComputerAgent(on_event=lambda e: None, ask_user=ask, rpc_timeout=.2)
        with patch('asyncio.create_subprocess_exec', spawn):
            run = asyncio.create_task(agent.run('test'))
            await p.started.wait()
            for _ in range(10):
                if agent.turn_id:
                    break
                await asyncio.sleep(0)
            assert agent.steer('Use 3+3 instead')
            await asyncio.sleep(.01)
            assert any(m.get('method') == 'turn/steer' for m in p.requests)
            p.stdout.feed_eof()
            assert 'disconnected' in await asyncio.wait_for(run, 1)
    asyncio.run(go())


def test_daemon_factory_routes_shared_task_contract_to_codex():
    host = SimpleNamespace()
    agent = Daemon._make_agent(host, lambda e: None, lambda q: None)
    assert isinstance(agent, CodexComputerAgent)
    assert host._active_agent is agent
