"""The Holo lane (holo_computer.py) against a fake `holo` binary that writes the CLI's real event shape."""
import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from cc_buddy_bridge import holo_computer
from cc_buddy_bridge.daemon import Daemon
from cc_buddy_bridge.holo_computer import HoloComputerAgent, HoloConfig, configured, describe_step

# One recorded run's events, screenshots dropped (a real `holo run` on 2026-09-28, trimmed).
FAKE = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time, uuid
    args = sys.argv[1:]
    log = os.path.join(os.path.dirname(__file__), 'calls.jsonl')
    with open(log, 'a') as f:
        f.write(json.dumps(args) + '\\n')
    if args[0] == 'stop':
        sys.exit(0)
    if args[0] == 'agent-api':
        # the daemon-owned runtime: answers /health on its port until killed; notes the token it was given
        from http.server import BaseHTTPRequestHandler, HTTPServer
        with open(log, 'a') as f:
            f.write(json.dumps({{"agent-api-token": os.environ.get("HAI_AGENT_RUNTIME_API_TOKEN")}}) + '\\n')
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{{"version": "fake"}}'
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a): pass
        HTTPServer(('127.0.0.1', int(args[args.index('--port') + 1])), H).serve_forever()
    runs = args[args.index('--runs-dir') + 1]
    task = args[-1]
    d = os.path.join(runs, str(uuid.uuid4()))
    os.makedirs(d)
    def ev(event):
        with open(os.path.join(d, 'events.jsonl'), 'a') as f:
            f.write(json.dumps({{"id": str(uuid.uuid4()), "run_id": "run-1", "event": event}}) + '\\n')
    ev({{"kind": "message_event", "caller_id": "user", "content": [task]}})
    ev({{"kind": "policy_event", "message": {{"content": json.dumps({{"note": "Opening Calculator", "thought": "x"}})}},
         "tool_reqs": [{{"tool_name": "click", "args": {{"x": 1, "y": 2}}}}]}})
    ev({{"kind": "policy_event", "message": {{"content": "{{}}"}},
         "tool_reqs": [{{"tool_name": "type_text", "args": {{"text": "hunter2"}}}}]}})
    if task == 'hang':
        time.sleep(60)
    if task == 'fail':
        print('model backend error', file=sys.stderr)
        sys.exit(3)
    ev({{"kind": "answer_event", "answer": "2 + 2 is 4."}})
    print("2 + 2 is 4.")
''')


def fake_holo(tmp_path):
    binary = tmp_path / 'holo'
    binary.write_text(FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def calls(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]


def agent(tmp_path, events, **config):
    # The cli driver, always: with the default (client) a test would run the real holo_driver.py under holo's
    # real Python on this Mac and drive the owner's desktop. The client driver's tests use client_agent below.
    config.setdefault('driver', 'cli')
    cfg = HoloConfig(enabled=True, **config)
    return HoloComputerAgent(on_event=events.append, ask_user=None, binary=str(fake_holo(tmp_path)),
                             config=cfg, runs_dir=tmp_path / 'runs')


def test_configured_is_off_by_default_and_defaults_to_holo4_27b():
    assert configured({}).enabled is False
    assert configured({'CC_BUDDY_COMPUTER': 'codex'}).enabled is False
    cfg = configured({'CC_BUDDY_COMPUTER': 'Holo'})
    assert cfg.enabled and cfg.model == 'holo4-27b' and cfg.max_steps is None and not cfg.keep_runs
    cfg = configured({'CC_BUDDY_COMPUTER': 'holo', 'CC_BUDDY_HOLO_MODEL': 'holo4-35b-a3b',
                      'CC_BUDDY_HOLO_MAX_STEPS': '40', 'CC_BUDDY_HOLO_KEEP_RUNS': '1'})
    assert (cfg.model, cfg.max_steps, cfg.keep_runs) == ('holo4-35b-a3b', 40, True)
    assert configured({'CC_BUDDY_COMPUTER': 'holo', 'CC_BUDDY_HOLO_MAX_STEPS': 'lots'}).max_steps is None


def test_describe_step_uses_the_note_and_never_the_typed_text():
    note = {'kind': 'policy_event', 'message': {'content': json.dumps({'note': 'Opening Calculator'})}}
    assert describe_step(note) == 'Opening Calculator'
    typed = {'kind': 'policy_event', 'message': {'content': 'not json'},
             'tool_reqs': [{'tool_name': 'type_text', 'args': {'text': 'hunter2'}}]}
    assert describe_step(typed) == 'Holo: type_text'
    answer_only = {'kind': 'policy_event', 'message': {}, 'tool_reqs': [{'tool_name': 'answer'}]}
    assert describe_step(answer_only) is None
    assert describe_step({'kind': 'observation_event'}) is None


def test_run_returns_the_answer_streams_progress_and_deletes_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(holo_computer, 'POLL_SECS', 0.01)
    events = []
    a = agent(tmp_path, events, max_steps=25)
    final = asyncio.run(a.run('what is 2 + 2 in Calculator'))
    assert final == '2 + 2 is 4.'
    kinds = [e.kind for e in events]
    assert kinds[0] == 'started' and kinds[-1] == 'final'
    progress = [e.text for e in events if e.kind == 'progress']
    assert 'Opening Calculator' in progress
    assert not any('hunter2' in e.text for e in events)
    run_call = calls(tmp_path)[0]
    assert run_call[:2] == ['run', '--quiet'] and '--no-kill-switch' in run_call
    assert run_call[run_call.index('--model') + 1] == 'holo4-27b'
    assert run_call[run_call.index('--max-steps') + 1] == '25'
    assert run_call[-1] == 'what is 2 + 2 in Calculator'
    assert a.ui_evidence and a.status()['thread_id'] == 'run-1' and not a.running
    assert list((tmp_path / 'runs').iterdir()) == []          # screenshots do not outlive the task


def test_keep_runs_keeps_the_event_log(tmp_path):
    a = agent(tmp_path, [], keep_runs=True)
    asyncio.run(a.run('add'))
    assert list((tmp_path / 'runs').glob('*/*/events.jsonl'))


def test_a_failed_run_is_an_error_not_an_answer(tmp_path):
    events = []
    final = asyncio.run(agent(tmp_path, events).run('fail'))
    assert 'not verified' in final and events[-1].kind == 'error'


def test_cancel_stops_the_runtime_and_the_client(tmp_path):
    events = []
    a = agent(tmp_path, events)

    async def go():
        task = asyncio.create_task(a.run('hang'))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if any(e.kind == 'progress' for e in events):
                break
        a.cancel('owner said stop')
        return await asyncio.wait_for(task, 10)

    final = asyncio.run(go())
    assert 'stopped' in final and events[-1].kind == 'cancelled'
    assert ['stop'] in calls(tmp_path)                        # `holo stop` ends the runtime's turn
    assert not a.running and a.steer('also do this') is False


def test_the_daemon_hands_the_desktop_to_holo_when_asked(monkeypatch):
    monkeypatch.setenv('CC_BUDDY_COMPUTER', 'holo')
    made = []
    fake = SimpleNamespace(_codex_warm=None, _active_agent=None)
    monkeypatch.setattr('cc_buddy_bridge.app_reflex.ReflexFirstAgent',
                        lambda make_inner, *a, **k: made.append(make_inner()) or SimpleNamespace())
    monkeypatch.setattr(Daemon, '_bodies', lambda self, *a: {})
    Daemon._make_agent(fake, lambda e: None, None)
    assert isinstance(made[0], HoloComputerAgent) and made[0].config.model == 'holo4-27b'


# -- the client driver: holo_driver.py's protocol, played by a fake driver under this test's Python --

FAKE_DRIVER = textwrap.dedent('''\
    import json, os, select, sys, time
    log = os.path.join(os.path.dirname(__file__), 'driver-ops.jsonl')
    def note(op):
        with open(log, 'a') as f:
            f.write(json.dumps(op) + '\\n')
    def emit(**ev):
        sys.stdout.write(json.dumps(ev) + '\\n'); sys.stdout.flush()
    def ops(seconds):
        """The ops that arrive within `seconds`, noted; True when a cancel came."""
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([sys.stdin], [], [], 0.02)
            if not r:
                continue
            line = sys.stdin.readline()
            if not line:
                return True
            op = json.loads(line); note(op)
            if op.get('op') == 'cancel':
                return True
            if op.get('op') == 'steer':
                emit(ev='steered', text=op['text'])
        return False
    note({'op': 'argv', 'args': sys.argv[1:], 'token': os.environ.get('HAI_AGENT_RUNTIME_API_TOKEN')})
    first = json.loads(sys.stdin.readline()); note(first)
    goal = first['goal']
    emit(ev='ready', runtime='0.1.12', spawned=False)
    if goal == 'fail':
        emit(ev='error', text='runtime not available: no key'); sys.exit(2)
    if goal == 'late-session':
        ops(0.3)                                   # a correction arrives before the session exists
        emit(ev='session', id='sess-late')
        emit(ev='steered', text='(queued, delivered at session)')
        emit(ev='final', answer='done late', status='completed', error=None, session_id='sess-late'); sys.exit(0)
    emit(ev='session', id='sess-1')
    emit(ev='progress', text='Opening Calculator')
    emit(ev='progress', text='Holo: click')
    if goal == 'hang':
        if ops(30):
            emit(ev='stopping')
            emit(ev='final', answer='', status='interrupted', error=None, session_id='sess-1')
        sys.exit(0)
    if goal == 'finish-anyway':
        ops(30)                                    # cancelled, but the turn had already completed
        emit(ev='final', answer='2 + 2 is 4.', status='completed', error=None, session_id='sess-1'); sys.exit(0)
    ops(0.3)
    emit(ev='final', answer='2 + 2 is 4.', status='completed', error=None, session_id='sess-1')
''')


def fake_driver(tmp_path):
    script = tmp_path / 'fake_driver.py'
    script.write_text(FAKE_DRIVER)
    return script


def driver_ops(tmp_path):
    path = tmp_path / 'driver-ops.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def client_agent(tmp_path, events, **config):
    cfg = HoloConfig(enabled=True, python=Path(sys.executable), **config)
    return HoloComputerAgent(on_event=events.append, ask_user=None, binary=str(fake_holo(tmp_path)),
                             config=cfg, runs_dir=tmp_path / 'runs', driver_script=fake_driver(tmp_path))


def test_the_client_driver_is_the_default_when_holo_python_exists_else_cli(tmp_path):
    cfg = configured({'CC_BUDDY_COMPUTER': 'holo', 'CC_BUDDY_HOLO_PYTHON': sys.executable})
    assert cfg.driver == 'client' and cfg.python == Path(sys.executable)
    assert holo_computer.driver_python(cfg) == Path(sys.executable)
    missing = configured({'CC_BUDDY_COMPUTER': 'holo', 'CC_BUDDY_HOLO_PYTHON': str(tmp_path / 'nope')})
    assert holo_computer.driver_python(missing) is None
    assert HoloComputerAgent(on_event=lambda e: None, ask_user=None, config=missing).driver == 'cli'
    cli = configured({'CC_BUDDY_COMPUTER': 'holo', 'CC_BUDDY_HOLO_DRIVER': 'cli', 'CC_BUDDY_HOLO_PYTHON': sys.executable})
    assert cli.driver == 'cli' and holo_computer.driver_python(cli) is None
    assert configured({'CC_BUDDY_COMPUTER': 'holo', 'CC_BUDDY_HOLO_DRIVER': 'weird'}).driver == 'client'


def test_client_driver_runs_a_task_and_reports_session_steps_and_answer(tmp_path):
    events = []
    a = client_agent(tmp_path, events)
    assert a.driver == 'client'
    final = asyncio.run(a.run('Open Calculator and compute 2+2'))
    assert final == '2 + 2 is 4.'
    assert [e.kind for e in events] == ['started', 'progress', 'progress', 'final']
    assert [e.text for e in events][1:3] == ['Opening Calculator', 'Holo: click']
    assert a.thread_id == 'sess-1' and a.status()['driver'] == 'client' and not a.running
    argv = driver_ops(tmp_path)[0]['args']
    assert argv[:2] == ['--port', '18795'] and '--model' in argv and 'holo4-27b' in argv
    assert driver_ops(tmp_path)[1] == {'op': 'run', 'goal': 'Open Calculator and compute 2+2'}
    assert not (tmp_path / 'runs').exists() or not any((tmp_path / 'runs').iterdir())


def test_a_correction_reaches_the_driver_while_the_task_runs(tmp_path):
    events, results = [], {}
    a = client_agent(tmp_path, events)

    async def go():
        task = asyncio.ensure_future(a.run('hang'))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if a.thread_id:
                break
        results['steer'] = a.steer('use the menu, not the keyboard')
        results['blank'] = a.steer('   ')
        await asyncio.sleep(0.2)
        a.cancel('stopped from Telegram')
        results['after_cancel'] = a.steer('too late')
        results['final'] = await task

    asyncio.run(go())
    assert results['steer'] is True and results['blank'] is False and results['after_cancel'] is False
    assert a.steered == ['use the menu, not the keyboard']
    ops = driver_ops(tmp_path)
    assert {'op': 'steer', 'text': 'use the menu, not the keyboard'} in ops
    assert ops[-1] == {'op': 'cancel'}                          # the driver was asked, not just killed
    assert results['final'].startswith('Holo task stopped') and events[-1].kind == 'cancelled'
    assert a.steer('now?') is False                             # nothing runs


def test_a_correction_before_the_session_exists_is_queued_not_lost(tmp_path):
    events = []
    a = client_agent(tmp_path, events)

    async def go():
        task = asyncio.ensure_future(a.run('late-session'))
        await asyncio.sleep(0.1)                                # the driver is up, no session yet
        assert a.thread_id is None
        assert a.steer('also open Notes') is True
        return await task

    assert asyncio.run(go()) == 'done late'
    assert {'op': 'steer', 'text': 'also open Notes'} in driver_ops(tmp_path)


def test_a_stop_that_lands_after_holo_finished_is_still_reported_as_stopped(tmp_path):
    """The rule TaskStop.lean and HoloSteer.lean share: a stop that reached the task never certifies a result."""
    events = []
    a = client_agent(tmp_path, events)

    async def go():
        task = asyncio.ensure_future(a.run('finish-anyway'))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if a.thread_id:
                break
        a.cancel('stopped from Telegram')
        return await task

    final = asyncio.run(go())
    assert final.startswith('Holo task stopped') and events[-1].kind == 'cancelled'
    assert not any(e.kind == 'final' for e in events)


def test_the_driver_failing_to_start_is_an_error_not_a_fallback(tmp_path):
    events = []
    a = client_agent(tmp_path, events)
    final = asyncio.run(a.run('fail'))
    assert final.startswith('Holo could not complete') and events[-1].kind == 'error'
    assert driver_ops(tmp_path)[-1] == {'op': 'run', 'goal': 'fail'}


def test_cli_driver_still_refuses_corrections(tmp_path):
    events = []
    a = agent(tmp_path, events, driver='cli')
    assert a.driver == 'cli'

    async def go():
        task = asyncio.ensure_future(a.run('hang'))
        await asyncio.sleep(0.3)
        ok = a.steer('use the menu')
        a.cancel()
        await task
        return ok

    assert asyncio.run(go()) is False


# -- the daemon-owned runtime: `holo agent-api` kept warm, the driver attaches with the daemon's token --

def free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_the_runtime_is_spawned_once_and_each_driver_attaches_with_its_token(tmp_path):
    from cc_buddy_bridge.holo_computer import HoloRuntime

    port = free_port()
    runtime = HoloRuntime(binary=str(fake_holo(tmp_path)), model='holo4-27b', port=port, runs_dir=tmp_path / 'runs')

    async def go():
        assert not await runtime.healthy()
        results = []
        for goal in ('Open Calculator and compute 2+2', 'Open Calculator and compute 2+2'):
            cfg = HoloConfig(enabled=True, python=Path(sys.executable))
            a = HoloComputerAgent(on_event=lambda e: None, ask_user=None, binary=str(fake_holo(tmp_path)), config=cfg,
                                  runs_dir=tmp_path / 'runs', driver_script=fake_driver(tmp_path), runtime=runtime)
            results.append(await a.run(goal))
        assert runtime.spawned and await runtime.healthy()
        await runtime.close()
        assert not runtime.spawned and not await runtime.healthy()
        return results

    assert asyncio.run(go()) == ['2 + 2 is 4.', '2 + 2 is 4.']
    spawns = [c for c in calls(tmp_path) if isinstance(c, list) and c and c[0] == 'agent-api']
    assert len(spawns) == 1 and spawns[0][1:] == ['--port', str(port), '--model', 'holo4-27b']
    given = [c['agent-api-token'] for c in calls(tmp_path) if isinstance(c, dict)]
    assert given == [runtime.token]
    drivers = [op for op in driver_ops(tmp_path) if op.get('op') == 'argv']
    assert len(drivers) == 2 and all(op['token'] == runtime.token for op in drivers)


def test_a_runtime_that_never_comes_up_is_an_error_not_a_hang(tmp_path):
    from cc_buddy_bridge.holo_computer import HoloRuntime

    events = []
    port = free_port()
    binary = tmp_path / 'holo'
    binary.write_text(f'#!{sys.executable}\nimport sys, time\nif sys.argv[1] == "agent-api":\n    sys.exit(3)\n')
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    runtime = HoloRuntime(binary=str(binary), port=port, runs_dir=tmp_path / 'runs')
    cfg = HoloConfig(enabled=True, python=Path(sys.executable))
    a = HoloComputerAgent(on_event=events.append, ask_user=None, binary=str(binary), config=cfg,
                          runs_dir=tmp_path / 'runs', driver_script=fake_driver(tmp_path), runtime=runtime)
    final = asyncio.run(a.run('anything'))
    assert final.startswith('Holo could not start') and events[-1].kind == 'error'
    assert not runtime.spawned
    assert driver_ops(tmp_path) == []                       # no driver was started without a runtime
