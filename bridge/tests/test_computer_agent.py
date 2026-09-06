"""computer_agent.py against a scripted Responses client and a fake worker:
the exec_py contract, the context-bearing first turn, output chaining, step
and wall-clock caps, cancel (between turns and mid-request), steer, ask_user,
fail-safe, effort pacing, repeat notes, retries, continuation, failure text,
progress events and the action log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cc_buddy_bridge import computer_agent as ca
from cc_buddy_bridge.computer_agent import (
    INSTRUCTIONS,
    TOOLS,
    AgentConfig,
    AgentEvent,
    ComputerAgent,
    FailSafe,
    WorkerDead,
    classify_response,
    configured,
    describe_failure,
)
from cc_buddy_bridge.desktop_helpers import HELPER_NAMES

# ---- fakes -------------------------------------------------------------------------

IMAGE = {"type": "input_image", "detail": "original", "image_url": "data:image/png;base64,AAAA"}
CONTEXT = {"type": "input_text", "text": "frontmost: Warp — 'zsh'; screen 100x50; 14:02"}


def _call(name: str, call_id: str, **args) -> dict:
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args)}


def _message(text: str) -> dict:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _response(rid: str, *items: dict, status: str = "completed", **extra) -> dict:
    return {"id": rid, "status": status, "output": list(items), **extra}


class FakeClient:
    """Serves scripted responses in order; records every request."""

    def __init__(self, responses: list[dict], raise_first: list[BaseException] | None = None) -> None:
        self.responses = list(responses)
        self.raise_first = list(raise_first or [])
        self.requests: list[dict] = []
        self.gate: asyncio.Event | None = None
        self.calls = 0

    async def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_first:
            raise self.raise_first.pop(0)
        if not self.responses:
            raise AssertionError("client ran out of scripted responses")
        return self.responses.pop(0)


class FakeWorker:
    def __init__(self, results: dict[str, list[dict]] | None = None, failsafe_on: str | None = None) -> None:
        self.results = results or {}
        self.failsafe_on = failsafe_on
        self.executed: list[str] = []
        self.started = False
        self.closed = False
        self.block: asyncio.Event | None = None

    async def start(self) -> dict:
        self.started = True
        return {"ready": True, "width": 100, "height": 50}

    async def observe(self) -> list[dict]:
        return [dict(IMAGE), dict(CONTEXT)]

    async def execute(self, code: str) -> list[dict]:
        self.executed.append(code)
        if self.block is not None:
            await self.block.wait()
        if self.failsafe_on is not None and self.failsafe_on in code:
            raise FailSafe("corner")
        return self.results.get(code, [{"type": "input_text", "text": f"ran {code}"}])

    async def close(self) -> None:
        self.closed = True


def _agent(client: FakeClient, worker: FakeWorker, tmp: Path, **kw) -> tuple[ComputerAgent, list[AgentEvent]]:
    events: list[AgentEvent] = []
    cfg = kw.pop("config", AgentConfig(runs_dir=tmp / "runs", max_turns=kw.pop("max_turns", 25)))
    clock = kw.pop("clock", lambda: 1.0)
    a = ComputerAgent(client, worker_factory=lambda: worker, config=cfg, on_event=events.append,
                      clock=clock, **kw)
    return a, events


async def _no_sleep(_s: float) -> None:
    pass


# ---- config / classification -------------------------------------------------------

def test_configured_defaults_and_env() -> None:
    c = configured({})
    assert c.enabled and c.model == "gpt-6-astra" and c.max_turns == 25
    c = configured({"CC_BUDDY_COMPUTER_CONTROL": "off", "CC_BUDDY_AGENT_MODEL": "gpt-5.6-sol",
                    "CC_BUDDY_AGENT_MAX_TURNS": "3"})
    assert not c.enabled and c.model == "gpt-5.6-sol" and c.max_turns == 3
    assert configured({"CC_BUDDY_AGENT_MAX_TURNS": "lots"}).max_turns == 25


def test_configured_reads_new_knobs_and_wires_exec_timeout(monkeypatch, caplog, tmp_path: Path) -> None:
    c = configured({})
    assert (c.reasoning_effort, c.plan_reasoning_effort) == ("low", "medium")
    assert (c.exec_timeout_secs, c.max_secs, c.api_timeout_secs) == (60.0, 180.0, 90.0)
    c = configured({"CC_BUDDY_AGENT_REASONING": "medium", "CC_BUDDY_AGENT_PLAN_REASONING": "high",
                    "CC_BUDDY_AGENT_EXEC_TIMEOUT": "30", "CC_BUDDY_AGENT_MAX_SECS": "60"})
    assert (c.reasoning_effort, c.plan_reasoning_effort, c.exec_timeout_secs, c.max_secs) == ("medium", "high", 30.0, 60.0)
    with caplog.at_level("WARNING"):
        assert configured({"CC_BUDDY_AGENT_REASONING": "turbo"}).reasoning_effort == "low"
    assert "turbo" in caplog.text
    assert configured({"CC_BUDDY_AGENT_EXEC_TIMEOUT": "3"}).exec_timeout_secs == 10.0
    assert configured({"CC_BUDDY_AGENT_MAX_SECS": "5"}).max_secs == 30.0
    # the default worker factory hands exec_timeout_secs to the WorkerClient
    made: list[float] = []

    class Spy:
        def __init__(self, timeout_secs: float) -> None:
            made.append(timeout_secs)

        async def start(self) -> dict:
            raise RuntimeError("spy: no desktop here")

        async def close(self) -> None:
            pass
    monkeypatch.setattr(ca, "WorkerClient", Spy)
    a = ComputerAgent(FakeClient([]), config=AgentConfig(runs_dir=tmp_path / "runs", exec_timeout_secs=30.0))
    assert asyncio.run(a.run("x")) == "Sorry, that failed unexpectedly."
    assert made == [30.0]


def test_tool_contract_is_the_documented_exec_py_shape() -> None:
    exec_tool = next(t for t in TOOLS if t["name"] == "exec_py")
    assert exec_tool["type"] == "function" and exec_tool["strict"] is True
    assert exec_tool["parameters"]["required"] == ["code"]
    assert exec_tool["parameters"]["additionalProperties"] is False
    assert "ask_user" in INSTRUCTIONS and "FAILSAFE" in INSTRUCTIONS


def test_instructions_tool_text_and_helper_names_agree() -> None:
    for name in HELPER_NAMES:
        assert name in INSTRUCTIONS, name
    for phrase in ("never Spotlight", "log()", "ask_user", "FAILSAFE"):
        assert phrase in INSTRUCTIONS, phrase
    assert "start with `display" not in INSTRUCTIONS and "command+space" not in INSTRUCTIONS
    code_desc = next(t for t in TOOLS if t["name"] == "exec_py")["parameters"]["properties"]["code"]["description"]
    for name in HELPER_NAMES:
        assert name in code_desc, name


def test_classify_calls_final_commentary() -> None:
    c = classify_response(_response("r1", {"type": "reasoning"}, _call("exec_py", "c1", code="x")))
    assert c.kind == "calls" and c.calls[0]["args"] == {"code": "x"}
    assert classify_response(_response("r2", _message("done"))).kind == "final"
    assert classify_response(_response("r3")).kind == "commentary"
    c = classify_response(_response("r4", _message("looking"), _call("exec_py", "c2", code="y")))
    assert c.kind == "calls" and c.text == "looking"


def test_classify_rejects_bad_replies() -> None:
    with pytest.raises(RuntimeError):
        classify_response(_response("r", status="incomplete"))
    with pytest.raises(RuntimeError):
        classify_response(_response("r", _call("rm_rf", "c1")))
    with pytest.raises(RuntimeError):
        classify_response(_response("r", {"type": "function_call", "name": "exec_py", "call_id": "c", "arguments": "{"}))


# ---- the loop -----------------------------------------------------------------------

def test_happy_path_chains_outputs_and_ends_on_final(tmp_path: Path) -> None:
    client = FakeClient([
        _response("r1", _call("exec_py", "c1", code="display(pyautogui.screenshot())")),
        _response("r2", _message("Looking at it now."), _call("exec_py", "c2", code="pyautogui.click(1, 2)")),
        _response("r3", _message("Done, your mail is open.")),
    ])
    worker = FakeWorker({"display(pyautogui.screenshot())": [dict(IMAGE)]})
    a, events = _agent(client, worker, tmp_path)
    final = asyncio.run(a.run("open my mail"))
    assert final == "Done, your mail is open."
    assert worker.started and worker.closed
    assert worker.executed == ["display(pyautogui.screenshot())", "pyautogui.click(1, 2)"]
    # request shapes: turn 1 carries the goal, the context line and the observe screenshot
    r1, r2, r3 = client.requests
    assert r1["model"] == "gpt-6-astra" and "previous_response_id" not in r1
    [first] = r1["input"]
    assert first["type"] == "message" and first["role"] == "user"
    assert first["content"][0]["type"] == "input_text"
    assert "Goal: open my mail" in first["content"][0]["text"] and "frontmost: Warp" in first["content"][0]["text"]
    assert first["content"][1] == IMAGE
    assert r1["tools"] == TOOLS and r1["parallel_tool_calls"] is False
    assert r2["previous_response_id"] == "r1"
    assert r2["input"] == [{"type": "function_call_output", "call_id": "c1", "output": [IMAGE]}]
    assert r3["previous_response_id"] == "r2"
    assert r3["input"][0]["output"] == [{"type": "input_text", "text": "ran pyautogui.click(1, 2)"}]
    kinds = [e.kind for e in events]
    assert kinds == ["started", "turn", "exec", "turn", "commentary", "exec", "progress", "turn", "final"]
    assert a.status()["running"] is False and a.status()["final"] == final


def test_first_turn_carries_context_and_plan_effort(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", _call("exec_py", "c1", code="a")), _response("r2", _message("ok"))])
    a, _ = _agent(client, FakeWorker(), tmp_path)
    asyncio.run(a.run("x"))
    assert client.requests[0]["reasoning"] == {"effort": "medium"}
    assert client.requests[1]["reasoning"] == {"effort": "low"}
    assert all(r["timeout"] == 90.0 for r in client.requests)


def test_step_cap_ends_the_run(tmp_path: Path) -> None:
    client = FakeClient([_response(f"r{i}", _call("exec_py", f"c{i}", code=f"step{i}")) for i in range(10)])
    a, events = _agent(client, FakeWorker(), tmp_path, max_turns=3)
    final = asyncio.run(a.run("loop forever"))
    assert "ran out of steps (3)" in final
    assert len(client.requests) == 3 and events[-1].kind == "final"


def test_wall_clock_budget_ends_the_run(tmp_path: Path) -> None:
    client = FakeClient([_response(f"r{i}", _call("exec_py", f"c{i}", code=f"step{i}")) for i in range(10)])
    cfg = AgentConfig(runs_dir=tmp_path / "runs", max_secs=150)
    a, events = _agent(client, FakeWorker(), tmp_path, config=cfg, clock=lambda: 100.0 * client.calls)
    final = asyncio.run(a.run("slow"))
    assert final.startswith("I ran out of time (150 s)")
    assert len(client.requests) == 2 and events[-1].kind == "final"


def test_commentary_only_turn_continues(tmp_path: Path) -> None:
    client = FakeClient([_response("r1"), _response("r2", _message("ok"))])
    a, _ = _agent(client, FakeWorker(), tmp_path)
    assert asyncio.run(a.run("x")) == "ok"
    assert client.requests[1]["input"] == []


def test_steer_is_delivered_with_the_next_turn(tmp_path: Path) -> None:
    client = FakeClient([
        _response("r1", _call("exec_py", "c1", code="a")),
        _response("r2", _call("exec_py", "c2", code="b")),
        _response("r3", _message("done")),
    ])
    a, _ = _agent(client, FakeWorker(), tmp_path)

    async def go() -> str:
        assert a.steer("too early") is False          # not running yet
        client.gate = asyncio.Event()                  # hold every request until released
        task = asyncio.create_task(a.run("x"))
        await asyncio.sleep(0)                         # the run is now waiting on request 1
        assert a.steer("actually use safari") is True
        client.gate.set()
        return await task
    assert asyncio.run(go()) == "done"
    # the steer rides along with c1's output, tagged, as a user message
    inp = client.requests[1]["input"]
    assert inp[0]["type"] == "function_call_output"
    assert inp[1] == {"type": "message", "role": "user",
                      "content": [{"type": "input_text", "text": "[steer] actually use safari"}]}
    assert client.requests[1]["reasoning"] == {"effort": "medium"}        # a steer is a planning moment
    assert all(i["type"] != "message" for i in client.requests[2]["input"])   # delivered once
    assert client.requests[2]["reasoning"] == {"effort": "low"}


def test_cancel_stops_between_turns_and_kills_the_worker(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", _call("exec_py", "c1", code="a")), _response("r2", _message("never"))])
    worker = FakeWorker()
    a, events = _agent(client, worker, tmp_path)

    async def go() -> str:
        client.gate = asyncio.Event()
        task = asyncio.create_task(a.run("x"))
        await asyncio.sleep(0)
        a.cancel()
        client.gate.set()
        return await task
    assert asyncio.run(go()) == "Stopped."
    assert worker.closed and worker.executed == []
    assert events[-1].kind == "cancelled"


def test_cancel_interrupts_an_inflight_request(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", _message("never"))])
    worker = FakeWorker()
    a, events = _agent(client, worker, tmp_path)

    async def go() -> str:
        client.gate = asyncio.Event()                  # never set: the request hangs forever
        task = asyncio.create_task(a.run("x"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(client.requests) == 1
        a.cancel(reason="hushed by a touch")
        return await asyncio.wait_for(task, timeout=0.1)
    assert asyncio.run(go()) == "Stopped: hushed by a touch."
    assert worker.closed and events[-1].kind == "cancelled"
    lines = [json.loads(ln) for ln in a.run_log.read_text().splitlines()]
    assert {"cancelled": "hushed by a touch"}.items() <= lines[-2].items()


def test_cancel_interrupts_a_running_exec(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", _call("exec_py", "c1", code="wait_for('never')"))])
    worker = FakeWorker()
    a, _ = _agent(client, worker, tmp_path)

    async def go() -> str:
        worker.block = asyncio.Event()                 # the exec never returns on its own
        task = asyncio.create_task(a.run("x"))
        for _ in range(100):
            await asyncio.sleep(0)
            if worker.executed:
                break
        assert worker.executed == ["wait_for('never')"]
        a.cancel()
        return await asyncio.wait_for(task, timeout=0.1)
    assert asyncio.run(go()) == "Stopped."
    assert worker.closed


def test_ask_user_routes_to_the_voice_session(tmp_path: Path) -> None:
    client = FakeClient([
        _response("r1", _call("ask_user", "c1", question="Send the email?")),
        _response("r2", _message("sent")),
    ])
    asked: list[str] = []

    async def ask(q: str) -> str:
        asked.append(q)
        return "yes go ahead"
    a, events = _agent(client, FakeWorker(), tmp_path, ask_user=ask)
    assert asyncio.run(a.run("x")) == "sent"
    assert asked == ["Send the email?"]
    assert client.requests[1]["input"][0]["output"] == [{"type": "input_text", "text": "yes go ahead"}]
    assert any(e.kind == "ask" and e.text == "Send the email?" for e in events)


def test_ask_user_without_a_listener_answers_no(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", _call("ask_user", "c1", question="Delete it?")), _response("r2", _message("ok"))])
    a, _ = _agent(client, FakeWorker(), tmp_path)
    asyncio.run(a.run("x"))
    assert client.requests[1]["input"][0]["output"][0]["text"].startswith("no (")
    assert client.requests[1]["reasoning"] == {"effort": "medium"}        # a "no" means re-plan


def test_failsafe_ends_the_run(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", _call("exec_py", "c1", code="corner"))])
    worker = FakeWorker(failsafe_on="corner")
    a, events = _agent(client, worker, tmp_path)
    assert asyncio.run(a.run("x")).startswith("Stopped: the mouse")
    assert worker.closed and events[-1].kind == "cancelled"


def test_client_error_is_reported_not_raised(tmp_path: Path) -> None:
    client = FakeClient([_response("r1", status="failed")])
    a, events = _agent(client, FakeWorker(), tmp_path)
    assert asyncio.run(a.run("x")).startswith("Sorry, that failed")
    assert events[-1].kind == "error"


# ---- pacing: effort, repeats, retries, continuation ---------------------------------

def test_repeat_exec_gets_a_note_and_medium_effort(tmp_path: Path) -> None:
    client = FakeClient([
        _response("r1", _call("exec_py", "c1", code="pyautogui.click(1, 2)")),
        _response("r2", _call("exec_py", "c2", code="pyautogui.click(1, 2)")),
        _response("r3", _message("stuck")),
    ])
    a, _ = _agent(client, FakeWorker(), tmp_path)
    asyncio.run(a.run("x"))
    assert client.requests[1]["reasoning"] == {"effort": "low"}
    inp = client.requests[2]["input"]
    notes = [i for i in inp if i["type"] == "message"]
    assert len(notes) == 1 and notes[0]["content"][0]["text"].startswith("[note] That is the same code")
    assert client.requests[2]["reasoning"] == {"effort": "medium"}
    lines = [json.loads(ln) for ln in a.run_log.read_text().splitlines()]
    assert any(ln.get("repeat") is True for ln in lines)


def test_error_result_escalates_effort_once(tmp_path: Path) -> None:
    client = FakeClient([
        _response("r1", _call("exec_py", "c1", code="bad")),
        _response("r2", _call("exec_py", "c2", code="good")),
        _response("r3", _call("exec_py", "c3", code="better")),
        _response("r4", _message("ok")),
    ])
    worker = FakeWorker({"bad": [{"type": "input_text", "text": "Traceback (most recent call last):\n  boom"}]})
    a, _ = _agent(client, worker, tmp_path)
    asyncio.run(a.run("x"))
    efforts = [r["reasoning"]["effort"] for r in client.requests]
    assert efforts == ["medium", "medium", "low", "low"]


def test_transient_api_error_retries_once(tmp_path: Path) -> None:
    class APIConnectionError(Exception):
        pass

    client = FakeClient([_response("r1", _message("ok"))], raise_first=[APIConnectionError("down")])
    a, _ = _agent(client, FakeWorker(), tmp_path, sleep=_no_sleep)
    assert asyncio.run(a.run("x")) == "ok"
    assert len(client.requests) == 2 and client.requests[0] == client.requests[1]
    lines = [json.loads(ln) for ln in a.run_log.read_text().splitlines()]
    assert any(ln.get("retry") == "APIConnectionError" for ln in lines)
    client = FakeClient([_response("r1", _message("ok"))],
                        raise_first=[APIConnectionError("down"), APIConnectionError("still down")])
    a, events = _agent(client, FakeWorker(), tmp_path, sleep=_no_sleep)
    assert asyncio.run(a.run("x")) == "Sorry, I lost my connection to the model service."
    assert events[-1].kind == "error" and events[-1].text.startswith("APIConnectionError")


def test_incomplete_response_is_continued_once(tmp_path: Path) -> None:
    cut = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    client = FakeClient([_response("r1", **cut), _response("r2", _message("finished"))])
    a, _ = _agent(client, FakeWorker(), tmp_path)
    assert asyncio.run(a.run("x")) == "finished"
    assert client.requests[1]["previous_response_id"] == "r1"
    assert client.requests[1]["input"] == [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "[note] Your reply was cut off; continue."}]}]
    client = FakeClient([_response("r1", **cut), _response("r2", **cut)])
    a, _ = _agent(client, FakeWorker(), tmp_path)
    assert asyncio.run(a.run("x")) == "Sorry, that failed unexpectedly."


def test_failure_text_is_human() -> None:
    class APITimeoutError(Exception):
        pass

    assert "crashed twice" in describe_failure(WorkerDead("deadline, twice"))
    assert "lost my connection" in describe_failure(APITimeoutError("slow"))
    assert describe_failure(ValueError("x")) == "Sorry, that failed unexpectedly."
    assert "grant Screen Recording" in describe_failure(RuntimeError("Screen Recording is not granted"))


def test_progress_events_from_short_text_lines(tmp_path: Path) -> None:
    result = [{"type": "input_text", "text": t} for t in (
        "opened Spotify", '{"app": "Spotify", "title": "Spotify"}',
        "[after] frontmost: Spotify — 'Spotify'; screen: changed", "Traceback (most recent call last):\n  x")]
    client = FakeClient([_response("r1", _call("exec_py", "c1", code="open_app('Spotify')")),
                         _response("r2", _message("ok"))])
    a, events = _agent(client, FakeWorker({"open_app('Spotify')": result}), tmp_path)
    seen: list[str] = []
    a.on_event = lambda ev: (events.append(ev), seen.append(a.status()["last"]) if ev.kind == "progress" else None)
    asyncio.run(a.run("x"))
    progress = [e for e in events if e.kind == "progress"]
    assert progress == [AgentEvent("progress", "opened Spotify", 1)]
    assert seen == ["opened Spotify"]                  # status()["last"] while the task runs
    assert ca.progress_lines([{"type": "input_text", "text": "hi"}, {"type": "input_image"},
                              {"type": "input_text", "text": "typed 22 characters"},
                              {"type": "input_text", "text": "typed 22 characters"}]) == ["typed 22 characters"]


def test_action_log_records_goal_exec_results_and_final(tmp_path: Path) -> None:
    client = FakeClient([
        _response("r1", _call("exec_py", "c1", code="display(pyautogui.screenshot())")),
        _response("r2", _message("all done")),
    ])
    worker = FakeWorker({"display(pyautogui.screenshot())": [
        {"type": "input_text", "text": "hi"}, {"type": "input_image", "detail": "original", "image_url": "data:x"}]})
    a, _ = _agent(client, worker, tmp_path)
    asyncio.run(a.run("look"))
    assert a.run_log is not None and a.run_log.parent == tmp_path / "runs"
    lines = [json.loads(ln) for ln in a.run_log.read_text().splitlines()]
    assert lines[0]["goal"] == "look" and lines[0]["model"] == "gpt-6-astra"
    assert lines[1] == {"t": 1.0, "turn": 0, "context": "frontmost: Warp — 'zsh'; screen 100x50; 14:02"}
    assert lines[2] == {"t": 1.0, "turn": 1, "effort": "medium", "api_secs": 0.0}
    assert lines[3]["exec"] == "display(pyautogui.screenshot())"
    assert lines[4] == {"t": 1.0, "turn": 1, "result": ["hi"], "images": 1}     # no image bytes in the log
    assert lines[-1]["final"] == "all done"
    assert "data:x" not in a.run_log.read_text() and "AAAA" not in a.run_log.read_text()
