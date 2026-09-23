"""Native Codex persistence: exact user choices and offered app scope, no local cache."""
import asyncio
from copy import deepcopy

import pytest

from cc_buddy_bridge.codex_computer import CodexComputerAgent, app_persistence


def request():
    return {'id': 10, 'method': 'mcpServer/elicitation/request', 'params': {
        'threadId': 'thread', 'serverName': 'cua_repl', 'mode': 'form',
        'message': 'Allow Computer Use to use "Calculator"?',
        'requestedSchema': {'type': 'object', 'properties': {}},
        '_meta': {'codex_approval_kind': 'mcp_tool_call', 'connector_id': 'computer-use',
                  'persist': ['session', 'always'], 'tool_name': 'get_app_state',
                  'tool_params': {'app': 'com.apple.calculator'}, 'riskLevel': 'low'}}}


def answer(text, msg=None):
    async def go():
        questions, responses, events = [], [], []
        async def ask(question):
            questions.append(question)
            return text
        async def send(response):
            responses.append(response)
        agent = CodexComputerAgent(on_event=events.append, ask_user=ask)
        agent.thread_id = 'thread'
        agent._send = send
        await agent._answer_request(msg or request())
        return responses[0], questions, events
    return asyncio.run(go())


@pytest.mark.parametrize('text,expected', [('always allow', 'always'), ('allow always', 'always'),
                                         ('allow for task', 'session'), ('allow for this task', 'session')])
def test_persistent_choices_go_to_codex(text, expected):
    reply, questions, _ = answer(text)
    assert reply['result'] == {'action': 'accept', 'content': {}, '_meta': {'persist': expected}}
    assert 'Calculator' in questions[0] and 'always allow' in questions[0] and 'allow for task' in questions[0]


@pytest.mark.parametrize('text,decision', [('yes', 'accept'), ('allow once', 'accept'),
                                         ('no', 'decline'), ('always allow everything', 'decline'),
                                         ('yes but later', 'decline'), ('', 'decline')])
def test_once_and_denial_never_persist(text, decision):
    reply, _, _ = answer(text)
    assert reply['result']['action'] == decision and '_meta' not in reply['result']


def test_managed_restriction_removes_always_without_downgrading_choice():
    msg = request()
    msg['params']['_meta']['persist'] = ['session']
    reply, questions, events = answer('always allow', msg)
    assert 'always allow' not in questions[0]
    assert reply['result']['action'] == 'decline' and '_meta' not in reply['result']
    assert any('no permission was saved' in e.text for e in events)
    assert answer('allow for task', msg)[0]['result']['_meta']['persist'] == 'session'


@pytest.mark.parametrize('change', [
    {'connector_id': 'browser'}, {'codex_request_type': 'approval_request'},
    {'tool_name': 'start_audio_recording'}, {'tool_params': {}},
    {'tool_params': {'app': 'com.apple.calculator', 'action': 'delete'}},
    {'tool_params': {'app': ''}}, {'persist': 'always'}, {'persist': None},
])
def test_unknown_audio_action_scopes_cannot_be_remembered(change):
    msg = request()
    msg['params']['_meta'].update(change)
    assert not app_persistence(msg['params'])
    reply, questions, _ = answer('always allow', msg)
    assert reply['result']['action'] == 'decline' and 'always allow' not in questions[0]


def test_risk_warning_is_preserved_and_forms_still_fail_closed():
    msg = request()
    msg['params']['_meta']['subtitle'] = 'This app can change account settings.'
    _, questions, _ = answer('always allow', msg)
    assert 'This app can change account settings.' in questions[0]
    bad = deepcopy(msg)
    bad['params']['requestedSchema']['properties']['password'] = {'type': 'string'}
    reply, questions, _ = answer('always allow', bad)
    assert reply['result']['action'] == 'decline' and not questions
    bad = deepcopy(msg)
    bad['params']['threadId'] = 'other'
    reply, questions, _ = answer('always allow', bad)
    assert 'error' in reply and not questions
