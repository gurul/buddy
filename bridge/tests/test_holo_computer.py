"""The Holo lane (holo_computer.py) against a fake `holo` binary that writes the CLI's real event shape."""
import asyncio
import json
import stat
import sys
import textwrap
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
