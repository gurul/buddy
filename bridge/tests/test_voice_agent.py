"""voice_agent.py with a scripted realtime connection, a fake speaker and a
fake computer agent: session setup, tool dispatch, board-state mirroring,
barge-in, ask_user relay, idle timeout and the session cap."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from cc_buddy_bridge.computer_agent import AgentEvent
from cc_buddy_bridge.voice_agent import (
    TOOLS,
    VoiceConfig,
    VoiceSession,
    configured,
    session_config,
)

# ---- fakes ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self, conn: "FakeConnection", kind: str) -> None:
        self.conn, self.kind = conn, kind

    async def update(self, **kw):   # session.update
        self.conn.sent.append((f"{self.kind}.update", kw))

    async def create(self, **kw):   # response.create / conversation.item.create
        self.conn.sent.append((f"{self.kind}.create", kw))

    async def append(self, **kw):   # input_audio_buffer.append
        self.conn.sent.append((f"{self.kind}.append", kw))


class FakeConnection:
    """Yields scripted events; after each event the test may push more via feed()."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        for e in events or []:
            self.queue.put_nowait(e)
        self.session = _Recorder(self, "session")
        self.response = _Recorder(self, "response")
        self.input_audio_buffer = _Recorder(self, "input_audio_buffer")
        self.conversation = SimpleNamespace(item=_Recorder(self, "conversation.item"))

    def feed(self, *events: dict | None) -> None:
        for e in events:
            self.queue.put_nowait(e)

    def __aiter__(self):
        return self

    async def __anext__(self):
        e = await self.queue.get()
        if e is None:
            raise StopAsyncIteration
        return e

    def kinds(self) -> list[str]:
        return [k for k, _ in self.sent]

    def tool_outputs(self) -> list[dict]:
        return [json.loads(kw["item"]["output"]) for k, kw in self.sent
                if k == "conversation.item.create" and kw["item"]["type"] == "function_call_output"]

    def user_messages(self) -> list[str]:
        return [kw["item"]["content"][0]["text"] for k, kw in self.sent
                if k == "conversation.item.create" and kw["item"]["type"] == "message"]


class FakeSpeaker:
    def __init__(self) -> None:
        self.played = b""
        self.stops = 0
        self._busy = False

    def play(self, pcm: bytes) -> None:
        self.played += pcm

    def stop(self) -> None:
        self.stops += 1
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy


class FakeAgent:
    """Runs until released; reports events through on_event like the real one."""

    def __init__(self, on_event, ask_user, final: str = "Mail is open.", ask: str | None = None) -> None:
        self.on_event, self.ask_user, self.final_text, self.ask_q = on_event, ask_user, final, ask
        self.running = False
        self.steers: list[str] = []
        self.cancelled = False
        self.release = asyncio.Event()
        self.final: str | None = None

    async def run(self, goal: str) -> str:
        self.running = True
        self.on_event(AgentEvent("started", goal))
        self.on_event(AgentEvent("exec", "display(...)", 1))
        answer = None
        if self.ask_q:
            self.on_event(AgentEvent("ask", self.ask_q, 1))
            answer = await self.ask_user(self.ask_q)
        await self.release.wait()
        self.running = False
        if self.cancelled:
            self.on_event(AgentEvent("cancelled", "Stopped."))
            self.final = "Stopped."
            return self.final
        self.final = f"{self.final_text} (answer={answer})" if answer is not None else self.final_text
        self.on_event(AgentEvent("final", self.final, 2))
        return self.final

    def steer(self, text: str) -> bool:
        if not self.running:
            return False
        self.steers.append(text)
        return True

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()

    def status(self) -> dict:
        return {"running": self.running, "turn": 1, "goal": "g", "last": "looking", "final": self.final}


def _tool_call(name: str, call_id: str = "c1", **args) -> dict:
    return {"type": "response.function_call_arguments.done", "name": name, "call_id": call_id,
            "arguments": json.dumps(args)}


def _session(conn: FakeConnection, agents: list[FakeAgent], clock: dict | None = None, **kw):
    states: list[str] = []
    clock = clock if clock is not None else {"now": 0.0}

    def factory(on_event, ask_user):
        a = agents[0] if len(agents) == 1 else agents.pop(0)
        a.on_event, a.ask_user = on_event, ask_user
        return a

    mic: asyncio.Queue[bytes] = asyncio.Queue()
    s = VoiceSession(conn, mic, FakeSpeaker(), factory, states.append,
                     config=kw.pop("config", VoiceConfig(idle_timeout_secs=20.0, max_session_secs=600.0)),
                     clock=lambda: clock["now"], **kw)
    return s, states, mic


# ---- config -------------------------------------------------------------------------------

def test_configured_defaults_and_env() -> None:
    c = configured({})
    assert c.model == "gpt-realtime-2.1-mini" and c.voice == "marin" and c.idle_timeout_secs == 20.0
    c = configured({"CC_BUDDY_REALTIME_MODEL": "gpt-realtime-2.1", "CC_BUDDY_VOICE_NAME": "cedar",
                    "CC_BUDDY_VOICE_IDLE_SECS": "45"})
    assert c.model == "gpt-realtime-2.1" and c.voice == "cedar" and c.idle_timeout_secs == 45.0
    assert configured({"CC_BUDDY_VOICE_IDLE_SECS": "1"}).idle_timeout_secs == 5.0     # floor


def test_session_config_shape() -> None:
    s = session_config(VoiceConfig())
    assert s["type"] == "realtime" and s["model"] == "gpt-realtime-2.1-mini"
    assert s["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert s["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert s["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert s["audio"]["output"]["voice"] == "marin"
    assert [t["name"] for t in s["tools"]] == ["start_task", "steer_task", "stop_task", "task_status",
                                                "answer_question", "end_conversation"]
    assert all(t["type"] == "function" for t in TOOLS)


# ---- the conversation ------------------------------------------------------------------------

def test_setup_greeting_mic_pump_and_goodbye() -> None:
    conn = FakeConnection()
    s, states, mic = _session(conn, [FakeAgent(None, None)])

    async def go():
        mic.put_nowait(b"\x01\x02" * 100)
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)                        # let the mic pump forward the block
        conn.feed(_tool_call("end_conversation"), None)
        await task
    asyncio.run(go())
    assert conn.kinds()[:2] == ["session.update", "response.create"]
    assert conn.sent[0][1]["session"]["model"] == "gpt-realtime-2.1-mini"
    assert "greeting" in conn.sent[1][1]["response"]["instructions"]
    appended = [kw["audio"] for k, kw in conn.sent if k == "input_audio_buffer.append"]
    assert appended and base64.b64decode(appended[0]) == b"\x01\x02" * 100
    assert conn.tool_outputs() == [{"ok": True}]
    # end_conversation does not ask for another response
    assert conn.kinds()[-1] == "conversation.item.create"
    assert states[0] == "wake" and states[-1] == "idle"


def test_audio_events_drive_speaker_and_states() -> None:
    pcm = base64.b64encode(b"\x00\x10" * 50).decode()
    conn = FakeConnection([
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": pcm},
        {"type": "response.output_audio_transcript.done", "transcript": "Yeah?"},
        {"type": "response.done"},
        {"type": "input_audio_buffer.speech_started"},
        _tool_call("end_conversation"), None,
    ])
    s, states, _ = _session(conn, [FakeAgent(None, None)])
    asyncio.run(s.run())
    assert s.speaker.played == b"\x00\x10" * 50
    assert s.transcript == ["Yeah?"]
    assert states == ["wake", "thinking", "speaking", "listening", "idle"]
    assert s.speaker.stops >= 1          # speech_started stops playback (barge-in)


def test_start_task_runs_agent_and_reports_result() -> None:
    agent = FakeAgent(None, None, final="Your mail is open.")
    conn = FakeConnection([_tool_call("start_task", goal="open mail")])
    s, states, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        assert s.task_running and "working" in states
        conn.feed({"type": "response.done"})            # after "On it."
        await asyncio.sleep(0.01)
        assert states[-1] == "working"
        agent.release.set()
        await asyncio.sleep(0.01)
        assert conn.user_messages() == ["[task finished] Your mail is open."]
        assert not s._ended.is_set()                    # still speaking the result
        conn.feed({"type": "response.created"}, {"type": "response.done"})   # the result, spoken
        await asyncio.sleep(0.01)
        assert s._ended.is_set()                        # ... and the conversation closes by itself
        conn.feed(None)
        await task
    asyncio.run(go())
    assert conn.tool_outputs()[0] == {"ok": True, "goal": "open mail"}
    assert "done" in states and states[-1] == "idle"
    assert s.tool_calls[0] == ("start_task", {"goal": "open mail"})


def test_steer_stop_and_status_tools() -> None:
    agent = FakeAgent(None, None)
    conn = FakeConnection([_tool_call("steer_task", "c0", text="nothing yet"), _tool_call("start_task", "c1", goal="g")])
    s, states, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_tool_call("steer_task", "c2", text="use safari"), _tool_call("task_status", "c3"),
                  _tool_call("stop_task", "c4"))
        await asyncio.sleep(0.02)
        conn.feed(_tool_call("stop_task", "c5"), _tool_call("end_conversation", "c6"), None)
        await task
    asyncio.run(go())
    outs = conn.tool_outputs()
    assert outs[0] == {"ok": False, "reason": "no task is running"}
    assert outs[1] == {"ok": True, "goal": "g"}
    assert outs[2] == {"ok": True} and agent.steers == ["use safari"]
    assert outs[3]["running"] is True and outs[3]["last"] == "looking"
    assert outs[4] == {"ok": True} and agent.cancelled
    assert outs[5] == {"ok": False, "reason": "no task is running"}
    assert conn.user_messages() == ["[task finished] Stopped."]


def test_second_start_while_running_is_refused_and_agent_can_be_disabled() -> None:
    agent = FakeAgent(None, None)
    conn = FakeConnection([_tool_call("start_task", "c1", goal="a"), _tool_call("start_task", "c2", goal="b")])
    s, _, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        agent.release.set()
        conn.feed(_tool_call("end_conversation", "c3"), None)
        await task
    asyncio.run(go())
    assert conn.tool_outputs()[1]["reason"].startswith("a task is already running")

    conn2 = FakeConnection([_tool_call("start_task", "c1", goal="a"), _tool_call("end_conversation", "c2"), None])
    s2, _, _ = _session(conn2, [FakeAgent(None, None)], agent_enabled=False)
    asyncio.run(s2.run())
    assert "disabled" in conn2.tool_outputs()[0]["reason"]


def test_ask_user_is_spoken_and_answered_through_the_tool() -> None:
    agent = FakeAgent(None, None, final="Sent.", ask="Send the email to Sam?")
    conn = FakeConnection([_tool_call("start_task", "c1", goal="email sam")])
    s, states, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        assert conn.user_messages() == ["[task question] Send the email to Sam?"]
        assert states[-1] == "asking"
        conn.feed(_tool_call("answer_question", "c2", answer="yes"))
        await asyncio.sleep(0.01)
        agent.release.set()
        await asyncio.sleep(0.01)
        conn.feed(_tool_call("end_conversation", "c3"), None)
        await task
    asyncio.run(go())
    assert conn.tool_outputs()[1] == {"ok": True}
    assert conn.user_messages()[-1] == "[task finished] Sent. (answer=yes)"


def test_answer_without_a_pending_question_is_refused() -> None:
    conn = FakeConnection([_tool_call("answer_question", "c1", answer="yes"), _tool_call("end_conversation", "c2"), None])
    s, _, _ = _session(conn, [FakeAgent(None, None)])
    asyncio.run(s.run())
    assert conn.tool_outputs()[0] == {"ok": False, "reason": "no question is pending"}


def test_idle_timeout_closes_the_session() -> None:
    clock = {"now": 0.0}
    conn = FakeConnection([{"type": "response.done"}])
    s, states, _ = _session(conn, [FakeAgent(None, None)], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        clock["now"] = 25.0                              # > idle_timeout 20 s, no task
        await asyncio.sleep(0.6)                         # one watchdog tick
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert states[-1] == "idle"


def test_running_task_defers_idle_timeout_but_not_the_session_cap() -> None:
    clock = {"now": 0.0}
    agent = FakeAgent(None, None)
    conn = FakeConnection([_tool_call("start_task", "c1", goal="g")])
    s, _, _ = _session(conn, [agent], clock=clock, config=VoiceConfig(idle_timeout_secs=20.0, max_session_secs=100.0))

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        clock["now"] = 50.0
        await asyncio.sleep(0.6)
        assert not s._ended.is_set()                     # task running: idle timeout does not apply
        clock["now"] = 101.0
        await asyncio.sleep(0.6)
        assert s._ended.is_set()                         # session cap does
        conn.feed(None)
        await task
    asyncio.run(go())
    assert agent.cancelled                               # the cap cancels a running task


def test_unknown_tool_and_error_events_are_harmless() -> None:
    conn = FakeConnection([{"type": "error", "error": {"message": "boom"}}, _tool_call("teleport", "c1"),
                           _tool_call("end_conversation", "c2"), None])
    s, _, _ = _session(conn, [FakeAgent(None, None)])
    asyncio.run(s.run())
    assert conn.tool_outputs()[0] == {"ok": False, "reason": "unknown tool teleport"}


def test_tool_result_defers_response_create_until_response_done() -> None:
    """function_call_arguments.done arrives before response.done; the API rejects a
    second response.create while one is active (bench 2026-09-06 09:01)."""
    conn = FakeConnection([{"type": "response.created"}, _tool_call("task_status", "c1")])
    s, _, _ = _session(conn, [FakeAgent(None, None)])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        creates_before = sum(1 for k, _ in conn.sent if k == "response.create")
        assert creates_before == 1                       # the greeting only; the tool result waited
        conn.feed({"type": "response.done"})
        await asyncio.sleep(0.01)
        creates_after = sum(1 for k, _ in conn.sent if k == "response.create")
        assert creates_after == 2                        # created once the response finished
        conn.feed(_tool_call("end_conversation", "c2"), None)
        await task
    asyncio.run(go())


def test_mic_is_muted_while_buddy_speaks_and_for_a_tail() -> None:
    clock = {"now": 0.0}
    conn = FakeConnection()
    s, _, mic = _session(conn, [FakeAgent(None, None)], clock=clock)
    s.speaker._busy = True

    async def go():
        task = asyncio.create_task(s.run())
        mic.put_nowait(b"\x01" * 100)                     # while speaking: dropped
        await asyncio.sleep(0.01)
        s.speaker._busy = False
        clock["now"] = 0.2                                # inside the 0.4 s tail: dropped
        mic.put_nowait(b"\x02" * 100)
        await asyncio.sleep(0.01)
        clock["now"] = 1.0                                # after the tail: forwarded
        mic.put_nowait(b"\x03" * 100)
        await asyncio.sleep(0.01)
        conn.feed(_tool_call("end_conversation"), None)
        await task
    asyncio.run(go())
    appended = [base64.b64decode(kw["audio"]) for k, kw in conn.sent if k == "input_audio_buffer.append"]
    assert appended == [b"\x03" * 100]


def test_conversation_does_not_keep_listening_after_a_task() -> None:
    """Owner request: after the result is spoken the session ends — no 20 s of listening."""
    agent = FakeAgent(None, None, final="Done.")
    conn = FakeConnection([_tool_call("start_task", "c1", goal="g")])
    s, states, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        agent.release.set()
        await asyncio.sleep(0.01)
        # the "On it" reply was still queued behind the greeting: it plays first ...
        conn.feed({"type": "response.created"}, {"type": "response.done"})
        await asyncio.sleep(0.01)
        assert not s._ended.is_set()
        # ... then the result is spoken, and that ends the conversation
        conn.feed({"type": "response.created"}, {"type": "response.done"})
        await asyncio.sleep(0.01)
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert states[-1] == "idle"
    creates = [k for k, _ in conn.sent if k == "response.create"]
    assert len(creates) == 2          # greeting, then ONE reply covering "On it" + the result (both queued) — nothing after
