"""The traces from verification/Buddy/HoloSteer.lean and verification/Buddy/ResponseRetry.lean, replayed against
the real HoloComputerAgent (a fake driver speaks holo_driver.py's protocol) and the real make_stream_creator (a
fake OpenAI client). Each `current_violates` trace is run against the code as it was where that code still exists
(the cli driver), and every `fixed_*` theorem against the fixed code."""

from __future__ import annotations

import asyncio
import ssl

import pytest
from test_holo_computer import agent, client_agent, driver_ops
from test_response_creators import FakeClient, text_of, tls_drop

from cc_buddy_bridge.computer_agent import make_stream_creator


async def until_session(a, seconds: float = 2.0) -> None:
    for _ in range(int(seconds / 0.02)):
        await asyncio.sleep(0.02)
        if a.thread_id:
            return


# -- HoloSteer.lean --

def test_holo_steer_current_violates_the_cli_driver_refuses_the_correction(tmp_path):
    """[start, steer] on the cli driver: lost."""
    a = agent(tmp_path, [], driver='cli')

    async def go():
        task = asyncio.ensure_future(a.run('hang'))
        await asyncio.sleep(0.3)
        refused = a.steer('use the menu') is False
        a.cancel()
        await task
        return refused

    assert asyncio.run(go()) is True


def test_holo_steer_fixed_replays_counterexample_the_correction_reaches_the_session(tmp_path):
    """[start, session, steer]: sent, not lost."""
    a = client_agent(tmp_path, [])

    async def go():
        task = asyncio.ensure_future(a.run('hang'))
        await until_session(a)
        ok = a.steer('use the menu')
        await asyncio.sleep(0.2)
        a.cancel()
        await task
        return ok

    assert asyncio.run(go()) is True
    assert {'op': 'steer', 'text': 'use the menu'} in driver_ops(tmp_path)


def test_holo_steer_early_correction_is_delivered(tmp_path):
    """[start, steer, session, finalOk]: sent, nothing queued, the result certified."""
    events = []
    a = client_agent(tmp_path, events)

    async def go():
        task = asyncio.ensure_future(a.run('late-session'))
        await asyncio.sleep(0.1)
        assert a.thread_id is None and a.steer('also open Notes') is True
        return await task

    assert asyncio.run(go()) == 'done late'
    assert events[-1].kind == 'final'
    assert {'op': 'steer', 'text': 'also open Notes'} in driver_ops(tmp_path)


def test_holo_steer_stop_then_finish_is_stopped(tmp_path):
    """[start, session, cancel, finalOk]: over, not verified."""
    events = []
    a = client_agent(tmp_path, events)

    async def go():
        task = asyncio.ensure_future(a.run('finish-anyway'))
        await until_session(a)
        a.cancel('stop')
        return await task

    final = asyncio.run(go())
    assert final.startswith('Holo task stopped') and 'It had reached: 2 + 2 is 4.' in final
    assert [e.kind for e in events if e.kind in ('final', 'cancelled')] == ['cancelled']


# -- ResponseRetry.lean --

async def _no_wait(_secs: float) -> None:
    return None


def _stream(client):
    return make_stream_creator(client=client, sleep=_no_wait)


def test_response_retry_fixed_replays_counterexample():
    """[drop, text, done]: succeeded, not failed, nothing read out twice."""
    client = FakeClient([tls_drop()], ['It is ', 'four.'])
    heard: list[str] = []
    out = asyncio.run(_stream(client)({}, heard.append))
    assert text_of(out) == 'It is four.' and heard == ['It is ', 'four.']


def test_response_retry_fixed_survives_two_drops():
    """[drop, drop, text, done]: the 2026-09-30 trace (a drop, then a drop on the retry) now succeeds."""
    client = FakeClient([tls_drop()], [tls_drop()], ['It is ', 'four.'])
    heard: list[str] = []
    out = asyncio.run(_stream(client)({}, heard.append))
    assert text_of(out) == 'It is four.' and heard == ['It is ', 'four.']


def test_response_retry_fixed_never_repeats():
    """[text, drop, text]: failed, not duplicated — the naive retry would have read the sentence out again."""
    client = FakeClient(['It is ', tls_drop()], ['It is ', 'four.'])
    heard: list[str] = []
    with pytest.raises(ssl.SSLError):
        asyncio.run(_stream(client)({}, heard.append))
    assert heard == ['It is ']


def test_response_retry_three_drops_fail():
    """[drop, drop, drop]: failed with droppedEvery — the only other way a turn may fail."""
    client = FakeClient([tls_drop()], [tls_drop()], [tls_drop()], ['never'])
    with pytest.raises(ssl.SSLError):
        asyncio.run(_stream(client)({}, lambda t: None))
    assert client.responses.calls == 3
