"""computer_agent.py against a scripted Responses client and a fake worker:
the exec_py contract, output chaining, step cap, cancel, steer, ask_user,
fail-safe, and the action log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cc_buddy_bridge.computer_agent import (
    INSTRUCTIONS,
    TOOLS,
    AgentConfig,
    AgentEvent,
    ComputerAgent,
    FailSafe,
    classify_response,
    configured,
)

# ---- fakes -------------------------------------------------------------------------

def _call(name: str, call_id: str, **args) -> dict:
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args)}


def _message(text: str) -> dict:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _response(rid: str, *items: dict, status: str = "completed") -> dict:
    return {"id": rid, "status": status, "output": list(items)}


class FakeClient:
    """Serves scripted responses in order; records every request."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.gate: asyncio.Event | None = None

    async def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
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

    async def start(self) -> dict:
        self.started = True
        return {"ready": True, "width": 100, "height": 50}

    async def execute(self, code: str) -> list[dict]:
        self.executed.append(code)
        if self.failsafe_on is not None and self.failsafe_on in code:
            raise FailSafe("corner")
        return self.results.get(code, [{"type": "input_text", "text": f"ran {code}"}])

    async def close(self) -> None:
        self.closed = True


def _agent(client: FakeClient, worker: FakeWorker, tmp: Path, **kw) -> tuple[ComputerAgent, list[AgentEvent]]:
    events: list[AgentEvent] = []
    cfg = kw.pop("config", AgentConfig(runs_dir=tmp / "runs", max_turns=kw.pop("max_turns", 25)))
    a = ComputerAgent(client, worker_factory=lambda: worker, config=cfg, on_event=events.append,
                      clock=lambda: 1.0, **kw)
    return a, events


# ---- config / classification -------------------------------------------------------

def test_configured_defaults_and_env() -> None:
    c = configured({})
    assert c.enabled and c.model == "gpt-6-astra" and c.max_turns == 25
    c = configured({"CC_BUDDY_COMPUTER_CONTROL": "off", "CC_BUDDY_AGENT_MODEL": "gpt-5.6-sol",
                    "CC_BUDDY_AGENT_MAX_TURNS": "3"})
    assert not c.enabled and c.model == "gpt-5.6-sol" and c.max_turns == 3
    assert configured({"CC_BUDDY_AGENT_MAX_TURNS": "lots"}).max_turns == 25


def test_tool_contract_is_the_documented_exec_py_shape() -> None:
    exec_tool = next(t for t in TOOLS if t["name"] == "exec_py")
    assert exec_tool["type"] == "function" and exec_tool["strict"] is True
    assert exec_tool["parameters"]["required"] == ["code"]
    assert exec_tool["parameters"]["additionalProperties"] is False
    assert "ask_user" in INSTRUCTIONS and "FAILSAFE" in INSTRUCTIONS


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
    worker = FakeWorker({"display(pyautogui.screenshot())": [{"type": "input_image", "detail": "original",
                                                             "image_url": "data:image/png;base64,AAAA"}]})
    a, events = _agent(client, worker, tmp_path)
    final = asyncio.run(a.run("open my mail"))
    assert final == "Done, your mail is open."
    assert worker.started and worker.closed
    assert worker.executed == ["display(pyautogui.screenshot())", "pyautogui.click(1, 2)"]
    # request shapes
    r1, r2, r3 = client.requests
    assert r1["model"] == "gpt-6-astra" and r1["input"] == "open my mail" and "previous_response_id" not in r1
    assert r1["tools"] == TOOLS and r1["parallel_tool_calls"] is False
    assert r2["previous_response_id"] == "r1"
    assert r2["input"] == [{"type": "function_call_output", "call_id": "c1",
                            "output": [{"type": "input_image", "detail": "original", "image_url": "data:image/png;base64,AAAA"}]}]
    assert r3["previous_response_id"] == "r2"
    assert r3["input"][0]["output"] == [{"type": "input_text", "text": "ran pyautogui.click(1, 2)"}]
    kinds = [e.kind for e in events]
    assert kinds == ["started", "turn", "exec", "turn", "commentary", "exec", "turn", "final"]
    assert a.status()["running"] is False and a.status()["final"] == final


def test_step_cap_ends_the_run(tmp_path: Path) -> None:
    client = FakeClient([_response(f"r{i}", _call("exec_py", f"c{i}", code=f"step{i}")) for i in range(10)])
    a, events = _agent(client, FakeWorker(), tmp_path, max_turns=3)
    final = asyncio.run(a.run("loop forever"))
    assert "ran out of steps (3)" in final
    assert len(client.requests) == 3 and events[-1].kind == "final"


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
    assert all(i["type"] != "message" for i in client.requests[2]["input"])   # delivered once


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
    assert lines[1]["exec"] == "display(pyautogui.screenshot())"
    assert lines[2] == {"t": 1.0, "turn": 1, "result": ["hi"], "images": 1}     # no image bytes in the log
    assert lines[-1]["final"] == "all done"
    assert "data:x" not in a.run_log.read_text()
