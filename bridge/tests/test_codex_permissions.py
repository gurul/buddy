"""Native Codex persistence: exact user choices and offered app scope, no local cache."""
import asyncio
from copy import deepcopy

import pytest

from cc_buddy_bridge.codex_computer import CodexComputerAgent, app_persistence, browser_origin


def request():
    return {'id': 10, 'method': 'mcpServer/elicitation/request', 'params': {
        'threadId': 'thread', 'serverName': 'cua_repl', 'mode': 'form',
        'message': 'Allow Computer Use to use "Calculator"?',
        'requestedSchema': {'type': 'object', 'properties': {}},
        '_meta': {'codex_approval_kind': 'mcp_tool_call', 'connector_id': 'computer-use',
                  'persist': ['session', 'always'], 'tool_name': 'get_app_state',
                  'tool_params': {'app': 'com.apple.calculator'}, 'riskLevel': 'low'}}}


def answer(text, msg=None, site_access='ask'):
    async def go():
        questions, responses, events = [], [], []
        async def ask(question):
            questions.append(question)
            return text
        async def send(response):
            responses.append(response)
        agent = CodexComputerAgent(on_event=events.append, ask_user=ask, site_access=site_access)
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


def site_request():
    msg = request()
    msg['params']['message'] = 'Allow Browser Use to access https://example.com?'
    msg['params']['_meta'] = {
        'codex_approval_kind': 'mcp_tool_call', 'codex_sensitive_action': True,
        'codex_request_type': 'approval_request', 'connector_id': 'browser-use',
        'persist': 'always', 'tool_name': 'access_browser_origin',
        'tool_params': {'origin': 'https://example.com'}, 'origin': 'https://example.com'}
    return msg


def test_owner_site_preference_avoids_prompt_without_saving_or_forging_review():
    msg = site_request()
    assert browser_origin(msg['params']) == 'https://example.com'
    reply, questions, events = answer('no', msg, site_access='allow')
    assert reply['result'] == {'action': 'accept', 'content': {}}
    assert not questions and not events
    reply, questions, _ = answer('no', msg, site_access='ask')
    assert questions and reply['result']['action'] == 'decline'
    reply, questions, _ = answer('no', request(), site_access='allow')
    assert questions and reply['result']['action'] == 'decline'


@pytest.mark.parametrize('change', [
    {'tool_name': 'access_browser_origin_with_raw_cdp'},
    {'tool_name': 'upload_browser_files'}, {'tool_name': 'webmcp.tool'},
    {'codex_strict_auto_review': True}, {'codex_requires_user_input': True},
    {'full_cdp_access': True}, {'file_transfer': 'upload'},
    {'sensitive_data': 'browsing_history'}, {'riskLevel': 'high'},
    {'connector_id': 'computer-use'}, {'codex_approval_kind': 'browser_auth'},
    {'tool_params': {'origin': 'https://example.com', 'action': 'send'}},
    {'origin': 'https://different.example'}, {'codex_request_type': 'unknown'},
])
def test_site_preference_does_not_approve_other_permissions(change):
    msg = site_request()
    msg['params']['_meta'].update(change)
    assert browser_origin(msg['params']) is None
    reply, _, _ = answer('no', msg, site_access='allow')
    assert reply['result']['action'] == 'decline'


@pytest.mark.parametrize('origin', ['file:///tmp/a', 'https://example.com/path',
    'https://user:secret@example.com', 'https://example.com?token=secret',
    'https://example.com#action', 'https://example.com:bad', '', 'https://example.com\n'])
def test_only_bare_http_origins_receive_site_preference(origin):
    msg = site_request()
    msg['params']['_meta'].update(origin=origin, tool_params={'origin': origin})
    assert browser_origin(msg['params']) is None
    assert answer('no', msg, site_access='allow')[0]['result']['action'] == 'decline'


def test_site_preference_does_not_approve_stale_threads_or_forms():
    msg = site_request()
    msg['params']['threadId'] = 'other'
    assert 'error' in answer('yes', msg, site_access='allow')[0]
    msg = site_request()
    msg['params']['requestedSchema']['properties']['password'] = {'type': 'string'}
    reply, questions, _ = answer('yes', msg, site_access='allow')
    assert reply['result']['action'] == 'decline' and not questions
