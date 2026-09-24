import asyncio
import json
from datetime import datetime

from test_telegram import FakeApi, FakeCreate, Rig, call, run_rig, say, update

from cc_buddy_bridge import rundown, second_brain


def test_obsidian_dates_completed_fences_and_symlinks(tmp_path):
    root = tmp_path / 'vault'
    root.mkdir()
    (root / 'tasks.md').write_text('''- [ ] Today 📅 2026-09-22
- [ ] Overdue [due:: 2026-09-20]
- [ ] Future ⏳ 2026-09-23
- [ ] Backlog
- [x] Finished 📅 2026-09-22
```
- [ ] Example
```
''')
    (root / '2026-09-22.md').write_text('- [ ] Daily plan\n')
    outside = tmp_path / 'private.md'
    outside.write_text('- [ ] Must not read\n')
    (root / 'link.md').symlink_to(outside)
    context = rundown.todo_context(root, '2026-09-22')
    assert {t['text'] for t in context['today']} == {'Today 📅 2026-09-22', 'Daily plan'}
    assert [t['text'] for t in context['overdue']] == ['Overdue [due:: 2026-09-20]']
    assert [t['text'] for t in context['undated']] == ['Backlog']
    assert not rundown.todo_context(None, '2026-09-22')['available']
    text = rundown.context(root, datetime.now().astimezone())
    assert 'start_inclusive' in text and 'end_exclusive' in text and 'Slack' in text


def test_rundown_tool_policy_reads_four_sources_and_refuses_mutations():
    assert rundown.allows('COMPOSIO_SEARCH_TOOLS', {})
    for slug in ['GMAIL_FETCH_EMAILS', 'GOOGLECALENDAR_LIST_EVENTS', 'SLACK_SEARCH_MESSAGES']:
        assert rundown.allows('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': [{'tool_slug': slug}]})
    for slug in ['GMAIL_SEND_EMAIL', 'GOOGLECALENDAR_CREATE_EVENT', 'SLACK_SEND_MESSAGE', 'GMAIL_MARK_EMAIL_AS_READ', 'GITHUB_GET_REPO']:
        assert not rundown.allows('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': [{'tool_slug': slug}]})
    for name in ['start_task', 'edit_note', 'capture_note', 'COMPOSIO_REMOTE_WORKBENCH']:
        assert not rundown.allows(name, {})
    assert not rundown.allows('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': []})
    assert not rundown.allows('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': [None]})


class Apps:
    started = True
    names = frozenset({'COMPOSIO_SEARCH_TOOLS', 'COMPOSIO_MULTI_EXECUTE_TOOL'})
    def __init__(self):
        self.calls = []
    def tools(self):
        return [{'type': 'function', 'name': n, 'parameters': {'type': 'object', 'properties': {}}} for n in self.names]
    def execute(self, name, args):
        self.calls.append((name, args))
        return {'ok': True, 'data': {'summary': 'source checked'}}


def test_rundown_loads_skill_and_reads_sources_even_with_codex_selected(tmp_path):
    (tmp_path / 'tasks.md').write_text('- [ ] Call the dentist\n')
    apps = Apps()
    api = FakeApi([update('rundown')])
    create = FakeCreate(call('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': [
        {'tool_slug': 'GMAIL_FETCH_EMAILS', 'arguments': {}},
        {'tool_slug': 'GOOGLECALENDAR_LIST_EVENTS', 'arguments': {}},
        {'tool_slug': 'SLACK_SEARCH_MESSAGES', 'arguments': {}}]}), say('Email checked. Calendar checked. Slack checked. Todos: call the dentist.'))
    rig = Rig(api, create, apps=apps, vault=second_brain.VaultConfig(True, tmp_path))
    rig.inlet._codex_chat = 4242
    run_rig(rig)
    assert len(apps.calls) == 1
    assert not rig.agents
    payload = create.requests[0]
    assert 'Call the dentist' in json.dumps(payload)
    assert 'rundown' in json.dumps(payload) and 'Slack' in json.dumps(payload)
    assert {t['name'] for t in payload['tools']} == apps.names
    assert any('dentist' in message for _, message in api.sent)


def test_rundown_declines_a_write_without_executing_or_asking():
    apps = Apps()
    api = FakeApi([update('/rundown')])
    create = FakeCreate(call('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': [
        {'tool_slug': 'SLACK_SEND_MESSAGE', 'arguments': {}}]}), say('Slack read unavailable.'))
    rig = Rig(api, create, apps=apps)
    run_rig(rig)
    assert not apps.calls
    assert 'rundown only reads' in json.dumps(create.requests[1])


def test_codex_progress_is_delivered_to_task_owner():
    from types import SimpleNamespace

    from test_telegram import settle

    from cc_buddy_bridge.computer_agent import AgentEvent

    async def go():
        api = FakeApi()
        rig = Rig(api, FakeCreate())
        rig.inlet._agent = SimpleNamespace(provider='codex')
        rig.inlet._chat_id = 9999
        rig.inlet._on_agent_event(AgentEvent('progress', 'Checking Calculator'), 4242)
        await settle()
        assert api.sent[0][0] == 4242
    asyncio.run(go())


# "What is my plane today" (2026-09-24 08:44) missed the rundown, went to the vault-only daily plan and
# a 20 s think_hard, and came back with no calendar. Asking for today's plan is asking for the rundown.
PLAN_ASKS = [
    'rundown', '/rundown', 'buddy: rundown', 'Rundown!',
    'What is my plane today', "What's my plan today?", 'whats my plan for today', 'What’s the plan today?',
    'what is my schedule today', 'my schedule today', "today's plan", "What's today's schedule?",
    'plan my day', 'Plan my day please', 'help me plan my day', 'can you plan my day?', 'plan today',
    "what's on today", "What's on for today?", "what's on my calendar today", 'what is on today',
    'what do I have today', 'What do I have on today?', 'what do i have going on today',
    'what have I got today', "what's my day look like", 'what does my day look like today',
    'how does my day look?', 'what does today look like', 'hey buddy, what is my plan today?',
]
NOT_PLAN_ASKS = [
    'plan a trip', 'plan my trip to Goa', "today's news", "what's the news today", 'what is the weather today',
    "what's my plan for the trip", 'plan my day tomorrow', 'what do I have tomorrow', "what's on netflix today",
    'I have no plan today', 'cancel my plan today', 'rundown of the meeting', 'what is a plane',
    'what is my plane ticket number', "what's the plan", 'plan', 'today',
]


def test_asking_for_todays_plan_is_the_rundown():
    missed = [t for t in PLAN_ASKS if not rundown.matches(t)]
    wrong = [t for t in NOT_PLAN_ASKS if rundown.matches(t)]
    assert not missed and not wrong, (missed, wrong)


def test_what_is_my_plane_today_reads_the_calendar_in_one_pass(tmp_path):
    (tmp_path / 'tasks.md').write_text('- [ ] Call the dentist\n')
    apps = Apps()
    api = FakeApi([update('What is my plane today')])
    create = FakeCreate(call('COMPOSIO_MULTI_EXECUTE_TOOL', {'tools': [
        {'tool_slug': 'GOOGLECALENDAR_LIST_EVENTS', 'arguments': {}}]}), say('9:00 standup. Then: call the dentist.'))
    rig = Rig(api, create, apps=apps, vault=second_brain.VaultConfig(True, tmp_path))
    run_rig(rig)
    first = create.requests[0]
    assert 'Call the dentist' in json.dumps(first) and 'start_inclusive' in json.dumps(first)
    assert {t['name'] for t in first['tools']} == apps.names          # the rundown's read tools only
    assert apps.calls[0][1]['tools'][0]['tool_slug'] == 'GOOGLECALENDAR_LIST_EVENTS'
    assert len(create.requests) == 2 and any('standup' in m for _, m in api.sent)
