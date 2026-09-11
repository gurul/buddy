"""voice_agent.py with a scripted Live connection, a fake speaker and a fake
computer agent: session setup, delegated tool dispatch, board-state mirroring,
barge-in, ask_user relay, idle timeout and the session cap.

The Live protocol has no transcript-done event, so a spoken turn closes on a
silence gap. `_say()` builds a turn and the tests advance the fake clock past
TURN_GAP_SECS to close it."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from cc_buddy_bridge.computer_agent import AgentEvent
from cc_buddy_bridge.voice_agent import (
    FAREWELL_MAX_SECS,
    FAREWELL_QUIET_SECS,
    LOOK_ROUTE_DELAY_SECS,
    TOOLS,
    TURN_GAP_SECS,
    VoiceConfig,
    VoiceSession,
    configured,
    session_config,
)

# ---- fakes ---------------------------------------------------------------------------

class _Recorder:
    """Records every client command under its dotted Live event name."""

    def __init__(self, conn: "FakeConnection", kind: str) -> None:
        self.conn, self.kind = conn, kind

    async def start(self, **kw):    # session.start
        self.conn.sent.append((f"{self.kind}.start", kw))

    async def update(self, **kw):   # session.update
        self.conn.sent.append((f"{self.kind}.update", kw))

    async def create(self, **kw):   # response.create / response.item.create
        self.conn.sent.append((f"{self.kind}.create", kw))

    async def append(self, **kw):   # session.input_audio.append / session.commentary.append
        self.conn.sent.append((f"{self.kind}.append", kw))

    async def mute(self, **kw):
        self.conn.sent.append((f"{self.kind}.mute", kw))

    async def unmute(self, **kw):
        self.conn.sent.append((f"{self.kind}.unmute", kw))

    async def close(self, **kw):
        self.conn.sent.append((f"{self.kind}.close", kw))


class FakeConnection:
    """Yields scripted events; after each event the test may push more via feed()."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.queue.put_nowait({"type": "session.started"})   # run() waits for this
        for e in events or []:
            self.queue.put_nowait(e)
        self.session = _Recorder(self, "session")
        self.session.input_audio = _Recorder(self, "session.input_audio")
        self.session.commentary = _Recorder(self, "session.commentary")
        self.session.thinking = _Recorder(self, "session.thinking")
        self.response = _Recorder(self, "response")
        self.response.item = _Recorder(self, "response.item")

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
                if k == "response.item.create" and kw["item"]["type"] == "function_call_output"]

    def user_messages(self) -> list[str]:
        return [kw["item"]["content"][0]["text"] for k, kw in self.sent
                if k == "response.item.create" and kw["item"]["type"] == "message"]

    def commentary(self) -> list[str]:
        """What the voice was handed to say (session.commentary.append), greeting first."""
        return [kw["content"] for k, kw in self.sent if k == "session.commentary.append"]


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

    def cancel(self, reason: str = "") -> None:
        self.cancelled = True
        self.reason = reason
        self.release.set()

    def status(self) -> dict:
        return {"running": self.running, "turn": 1, "goal": "g", "last": "looking", "final": self.final}


def _tool_call(name: str, call_id: str = "c1", **args) -> dict:
    """A backend function call, nested in a Live response.event."""
    return {"type": "response.event", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": name, "call_id": call_id,
                 "arguments": json.dumps(args)}}}


def _delegated(did: str = "d1") -> dict:
    return {"type": "session.delegation.created",
            "delegation": {"id": did, "target": "responses", "type": "delegation"}}


def _backend(kind: str) -> dict:
    return {"type": "response.event", "event": {"type": kind}}


def _done() -> dict:
    return _backend("response.completed")


def _heard(text: str) -> dict:
    return {"type": "session.input_transcript.delta", "delta": text}


def _spoke(text: str) -> dict:
    return {"type": "session.output_transcript.delta", "delta": text}


def _session(conn: FakeConnection, agents: list[FakeAgent], clock: dict | None = None, **kw):
    states: list[str] = []
    clock = clock if clock is not None else {"now": 0.0}

    def factory(on_event, ask_user):
        a = agents[0] if len(agents) == 1 else agents.pop(0)
        a.on_event, a.ask_user = on_event, ask_user
        return a

    mic: asyncio.Queue[bytes] = asyncio.Queue()
    s = VoiceSession(conn, mic, FakeSpeaker(), factory, states.append,
                     config=kw.pop("config", VoiceConfig(idle_timeout_secs=20.0, max_session_secs=600.0,
                                                          output="audio")),
                     clock=lambda: clock["now"], **kw)
    return s, states, mic


# ---- config -------------------------------------------------------------------------------

def test_configured_defaults_and_env() -> None:
    c = configured({})
    assert c.model == "gpt-live-1" and c.voice == "marin" and c.idle_timeout_secs == 20.0
    assert c.backend_model == "gpt-6-astra" and c.backend_effort == "low"
    c = configured({"CC_BUDDY_LIVE_MODEL": "gpt-live-1-preview", "CC_BUDDY_VOICE_NAME": "cedar",
                    "CC_BUDDY_LIVE_BACKEND_MODEL": "gpt-5-mini", "CC_BUDDY_LIVE_BACKEND_EFFORT": "minimal",
                    "CC_BUDDY_VOICE_IDLE_SECS": "45"})
    assert c.model == "gpt-live-1-preview" and c.voice == "cedar" and c.idle_timeout_secs == 45.0
    assert c.backend_model == "gpt-5-mini" and c.backend_effort == "minimal"
    # the Realtime-era name is ignored, not silently honoured
    assert configured({"CC_BUDDY_REALTIME_MODEL": "gpt-realtime-2.1"}).model == "gpt-live-1"
    assert configured({"CC_BUDDY_LIVE_BACKEND_EFFORT": "turbo"}).backend_effort == "low"
    assert configured({"CC_BUDDY_VOICE_IDLE_SECS": "1"}).idle_timeout_secs == 5.0     # floor


def test_session_config_shape() -> None:
    s = session_config(VoiceConfig(output="audio"))
    assert s["model"] == "gpt-live-1"
    # gpt-live-1 is full duplex and always speaks: no modality list, no turn detection
    assert "output_modalities" not in s and "tools" not in s and "type" not in s
    assert s["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert s["audio"]["output"]["voice"] == "marin"
    d = s["delegation"]
    assert d["type"] == "responses" and d["responses"]["model"] == "gpt-6-astra"
    assert d["responses"]["reasoning"] == {"effort": "low"}
    assert [t["name"] for t in d["responses"]["tools"]] == ["start_task", "steer_task", "stop_task",
                                                            "task_status", "answer_question",
                                                            "go_explore", "end_conversation",
                                                            "look", "move_head", "look_around", "find",
                                                            "set_sound"]
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
    assert conn.kinds()[:2] == ["session.start", "session.commentary.append"]
    assert conn.sent[0][1]["session"]["model"] == "gpt-live-1"
    assert "Greet" in conn.sent[1][1]["content"]
    appended = [kw["audio"] for k, kw in conn.sent if k == "session.input_audio.append"]
    assert appended and base64.b64decode(appended[0]) == b"\x01\x02" * 100
    assert conn.tool_outputs() == [{"ok": True}]
    # end_conversation does not ask for another response
    assert conn.kinds()[-1] == "response.item.create"
    assert states[0] == "wake" and states[-1] == "idle"


def test_audio_events_drive_speaker_and_states() -> None:
    pcm = base64.b64encode(b"\x00\x10" * 50).decode()
    conn = FakeConnection([
        _delegated(),
        {"type": "session.output_audio.delta", "delta": pcm},
        _spoke("Yeah?"),
        _done(),
        _heard("wait"),
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
    clock = {"now": 0.0}
    conn = FakeConnection([_tool_call("start_task", goal="open mail")])
    s, states, _ = _session(conn, [agent], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        assert s.task_running and "working" in states
        conn.feed(_done())            # after "On it."
        await asyncio.sleep(0.01)
        assert states[-1] == "working"
        agent.release.set()
        await asyncio.sleep(0.01)
        # the result goes to the VOICE to say, not into the backend (2026-09-10: "Okay, waiting.")
        assert conn.user_messages() == []
        assert "Your mail is open." in conn.commentary()[-1]
        conn.feed(_delegated(), _spoke("Your mail is open."), _done())
        await asyncio.sleep(0.05)
        clock["now"] += 2.0                             # the result has been said and gone quiet
        await asyncio.sleep(0.2)
        assert not s._ended.is_set()                    # ... and buddy keeps listening (2026-09-10)
        assert states[-1] == "listening"
        conn.feed(_tool_call("end_conversation", "c9"))   # "bye buddy" is what ends it
        await asyncio.sleep(0.05)
        assert s._ended.is_set()
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
    assert conn.user_messages() == [] and "Stopped." in conn.commentary()[-1]


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
    # the backend got the question as context, the voice asked it and later said the result
    assert conn.user_messages() == ["[task question] Send the email to Sam?"]
    assert any("Send the email to Sam?" in c for c in conn.commentary())
    assert "Sent. (answer=yes)" in conn.commentary()[-1]


def test_answer_without_a_pending_question_is_refused() -> None:
    conn = FakeConnection([_tool_call("answer_question", "c1", answer="yes"), _tool_call("end_conversation", "c2"), None])
    s, _, _ = _session(conn, [FakeAgent(None, None)])
    asyncio.run(s.run())
    assert conn.tool_outputs()[0] == {"ok": False, "reason": "no question is pending"}


def test_idle_timeout_closes_the_session() -> None:
    clock = {"now": 0.0}
    conn = FakeConnection([_done()])
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
    """A backend function call is reported before its response completes, and the
    API rejects a second response.create while one is active (bench 2026-09-06 09:01)."""
    conn = FakeConnection([_delegated(), _tool_call("task_status", "c1")])
    s, _, _ = _session(conn, [FakeAgent(None, None)])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        creates_before = sum(1 for k, _ in conn.sent if k == "response.create")
        assert creates_before == 0                       # the tool result waited; the greeting is commentary
        conn.feed(_done())
        await asyncio.sleep(0.01)
        creates_after = sum(1 for k, _ in conn.sent if k == "response.create")
        assert creates_after == 1                        # created once the response completed
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
    appended = [base64.b64decode(kw["audio"]) for k, kw in conn.sent if k == "session.input_audio.append"]
    assert appended == [b"\x03" * 100]


def test_conversation_stays_open_after_a_task_until_goodbye_or_idle() -> None:
    """Owner request 2026-09-10: a finished task does not end the conversation. A goodbye
    does (previous test), and so does the idle timeout when nothing more is said."""
    agent = FakeAgent(None, None, final="Done.")
    clock = {"now": 0.0}
    conn = FakeConnection([_tool_call("start_task", "c1", goal="g")])
    s, states, _ = _session(conn, [agent], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        agent.release.set()
        await asyncio.sleep(0.01)
        conn.feed(_delegated(), _spoke("On it."), _done())
        await asyncio.sleep(0.05)
        clock["now"] = 2.0
        await asyncio.sleep(0.2)
        conn.feed(_spoke("Done."))                     # the result, spoken
        await asyncio.sleep(0.05)
        clock["now"] = 4.0
        await asyncio.sleep(0.2)
        assert not s._ended.is_set()                   # still listening after the result
        clock["now"] = 30.0                            # past the 20 s idle timeout
        await asyncio.sleep(0.7)                       # the watchdog checks every 0.5 s
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert states[-1] == "idle"
    assert s.transcript == ["On it.", "Done."]
    creates = [k for k, _ in conn.sent if k == "response.create"]
    assert len(creates) == 1          # "On it" only: the result is commentary, and nothing follows it


CAPTIONS = VoiceConfig(idle_timeout_secs=20.0, max_session_secs=600.0, output="captions")


def _reply(*deltas: str) -> list[dict]:
    text = "".join(deltas)
    del text
    return [_delegated(), *(_spoke(d) for d in deltas), _done()]


def _captions_session(conn: FakeConnection, clock: dict, **kw):
    captions: list[dict] = []
    s, states, mic = _session(conn, [FakeAgent(None, None)], clock=clock, on_caption=captions.append,
                              caption_tick_secs=0.001, turn_gap_secs=0.2,
                              config=kw.pop("config", CAPTIONS), **kw)
    return s, states, captions


async def _quiet(clock: dict, at: float = 0.3) -> None:
    """Live sends no transcript-done event: a reply is final once it goes quiet."""
    clock["now"] = at
    await asyncio.sleep(0.02)


def test_captions_mode_streams_text_to_the_robot_and_plays_nothing() -> None:
    conn = FakeConnection(_reply("Ten past ", "three."))
    clock = {"now": 0.0}
    s, states, captions = _captions_session(conn, clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        await _quiet(clock)
        # page 0 goes up at the first whole word (hold 2 s while it fills), then the final
        # text refills it as the last page: 15 chars -> clamp(15/12 + 0.8 + 3, 6, 10) = 6 s,
        # less the 0.3 s the turn took to go quiet
        assert captions == [
            {"cmd": "caption", "page": 0, "of": 0, "lines": ["Ten past"], "hold_ms": 2000, "chirp": True, "final": False},
            {"cmd": "caption", "page": 0, "of": 1, "lines": ["Ten past three."], "hold_ms": 5700, "chirp": False,
             "final": True},
        ]
        assert states[-1] == "speaking"                 # the phase holds while the page is read
        clock["now"] = 6.4
        await asyncio.sleep(0.01)
        assert captions[-1] == {"cmd": "caption", "clear": True}
        assert states[-1] == "listening"
        conn.feed(_tool_call("end_conversation"), None)
        await task
    asyncio.run(go())
    assert s.speaker.played == b"" and s.transcript == ["Ten past three."]
    assert states[-1] == "idle"


def test_state_stays_speaking_until_the_last_page_is_held() -> None:
    conn = FakeConnection(_reply("Ten past ", "three."))
    clock = {"now": 0.0}
    s, states, captions = _captions_session(conn, clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        await _quiet(clock)
        assert states[-1] == "speaking" and captions[-1]["final"] is True
        clock["now"] = 3.0
        await asyncio.sleep(0.01)
        assert states[-1] == "speaking"                 # 3 s in: still on the page
        conn.feed(_heard("wait"))   # barge-in
        await asyncio.sleep(0.01)
        assert states[-1] == "listening" and captions[-1] == {"cmd": "caption", "clear": True}
        conn.feed(_tool_call("end_conversation"), None)
        await task
    asyncio.run(go())


def test_idle_watchdog_waits_for_captions() -> None:
    conn = FakeConnection(_reply("Ten past ", "three."))
    clock = {"now": 0.0}
    s, _, captions = _captions_session(conn, clock, config=VoiceConfig(idle_timeout_secs=5.0, max_session_secs=600.0,
                                                                        output="captions"))

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        await _quiet(clock)
        assert captions[-1]["hold_ms"] == 5700
        clock["now"] = 5.5                              # > idle 5 s, but the page is still held
        await asyncio.sleep(0.6)
        assert not s._ended.is_set()
        clock["now"] = 7.0                              # page cleared at 6 s; idle since 0
        await asyncio.sleep(0.6)
        assert s._ended.is_set() and captions[-1] == {"cmd": "caption", "clear": True}
        conn.feed(None)
        await task
    asyncio.run(go())


def test_run_drains_captions_before_idle() -> None:
    conn = FakeConnection(_reply("Ten past ", "three.") + [_tool_call("end_conversation"), None])
    clock = {"now": 0.0}
    s, states, captions = _captions_session(conn, clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        assert not task.done() and states[-1] != "idle"       # holding the page, not idle yet
        assert captions[-1]["final"] is True
        clock["now"] = 6.5
        await asyncio.sleep(0.05)
        assert task.done()
        await task
    asyncio.run(go())
    assert captions[-1] == {"cmd": "caption", "clear": True} and states[-1] == "idle"


def test_hush_clears_the_caption_at_once() -> None:
    conn = FakeConnection(_reply("Ten past ", "three."))
    clock = {"now": 0.0}
    s, states, captions = _captions_session(conn, clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        await _quiet(clock)
        assert captions[-1]["final"] is True
        task.cancel()                                   # the daemon's touch hush
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert captions[-1] == {"cmd": "caption", "clear": True} and states[-1] == "idle"


def test_new_response_chains_pages() -> None:
    conn = FakeConnection(_reply("Ten past ", "three."))
    clock = {"now": 0.0}
    s, states, captions = _captions_session(conn, clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        await _quiet(clock)
        n = len(captions)
        clock["now"] = 0.5
        conn.feed(*_reply("Sure, ", "on it."))          # a second reply while the page is held
        await asyncio.sleep(0.02)
        assert states[-1] == "speaking" and "thinking" not in states[states.index("speaking"):]
        assert captions[n:] == []                       # nothing until the demoted first page ran out
        clock["now"] = 2.9                              # clamp(15/12 + 0.8, 2, 9) = 2.05 s after 0, dwell 2 s
        await asyncio.sleep(0.02)
        assert captions[n]["page"] == 0 and captions[n]["chirp"] is True and captions[n]["lines"] == ["Sure, on it."]
        assert not any("clear" in c for c in captions)
        clock["now"] = 9.0
        await asyncio.sleep(0.02)
        assert captions[-1] == {"cmd": "caption", "clear": True} and states[-1] == "listening"
        conn.feed(_tool_call("end_conversation"), None)
        await task
    asyncio.run(go())


def test_configured_caption_cps() -> None:
    assert configured({"CC_BUDDY_CAPTION_CPS": "8"}).caption_cps == 8.0
    assert configured({"CC_BUDDY_CAPTION_CPS": "2"}).caption_cps == 5.0
    assert configured({"CC_BUDDY_CAPTION_CPS": "99"}).caption_cps == 30.0
    assert configured({"CC_BUDDY_CAPTION_CPS": "fast"}).caption_cps == 12.0
    assert configured({}).caption_cps == 12.0


def test_session_config_captions_vs_audio() -> None:
    # gpt-live-1 always speaks, so the two modes differ only in the instructions and
    # in whether the daemon plays the audio it receives
    cap = session_config(VoiceConfig(output="captions"))
    assert cap["audio"]["output"]["voice"] == "marin"
    assert "17 characters" in cap["instructions"] and "one page at a time" in cap["instructions"]
    aud = session_config(VoiceConfig(output="audio"))
    assert aud["audio"]["output"]["voice"] == "marin"
    assert "17 characters" not in aud["instructions"]
    assert configured({}).output == "captions"
    assert configured({"CC_BUDDY_VOICE_OUTPUT": "audio"}).output == "audio"
    assert configured({"CC_BUDDY_VOICE_OUTPUT": "loud"}).output == "captions"
    # audio mode never touches the caption callback, even when one is wired
    captions: list[dict] = []
    conn = FakeConnection(_reply("Yeah?") + [_tool_call("end_conversation"), None])
    s, _, _ = _session(conn, [FakeAgent(None, None)], on_caption=captions.append)
    asyncio.run(s.run())
    assert captions == [] and s.transcript == ["Yeah?"]   # the turn is flushed as the session closes


def test_progress_events_become_caption_pages_while_working() -> None:
    """A task's helper sentences ("opened Safari") show on the robot as pages, at most
    one per progress_min_gap_secs, only when no reply page is up."""
    from cc_buddy_bridge.computer_agent import AgentEvent as Ev

    clock = {"now": 0.0}
    conn = FakeConnection()
    s, states, captions = _captions_session(conn, clock)
    s.state = "working"
    s._on_agent_event(Ev("progress", "opened Safari", 1))
    assert captions and captions[-1]["lines"] == ["opened Safari"] and captions[-1]["final"] is True
    n = len(captions)
    s._on_agent_event(Ev("progress", "typed the search", 1))      # same instant: gap not elapsed → dropped
    assert len(captions) == n
    clock["now"] = 20.0
    s._pager.reset(clock["now"])                                   # the page has cleared
    s._on_agent_event(Ev("progress", "typed the search", 2))
    assert captions[-1]["lines"] == ["typed the search"]


def test_conversation_close_cancels_the_task_with_a_reason() -> None:
    agent = FakeAgent(None, None)
    conn = FakeConnection([_tool_call("start_task", "c1", goal="g")])
    s, _, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        s.end()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert agent.cancelled and agent.reason == "the conversation closed"


# ---- "go explore" -------------------------------------------------------------------------------

def test_go_explore_ends_the_conversation_and_fires_the_callback() -> None:
    fired: list[bool] = []
    conn = FakeConnection([_tool_call("go_explore"), None])
    s, states, _ = _session(conn, [FakeAgent(None, None)], on_explore=lambda: fired.append(True))
    asyncio.run(s.run())
    assert conn.tool_outputs() == [{"ok": True}]
    assert fired == [True] and s.explore_requested
    assert conn.kinds()[-1] == "response.item.create"     # no further response is requested
    assert states[-1] == "idle"


def test_go_explore_is_refused_while_a_task_runs() -> None:
    agent = FakeAgent(None, None)
    conn = FakeConnection([_tool_call("start_task", "c1", goal="open mail")])
    fired: list[bool] = []
    s, _, _ = _session(conn, [agent], on_explore=lambda: fired.append(True))

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        conn.feed(_tool_call("go_explore", "c2"))
        await asyncio.sleep(0.02)
        agent.release.set()
        await asyncio.sleep(0.02)
        conn.feed(_tool_call("end_conversation", "c3"), None)
        await task
    asyncio.run(go())
    outs = conn.tool_outputs()
    assert outs[1] == {"ok": False, "reason": "a task is running; stop it first"}
    assert fired == [] and not s.explore_requested


def test_go_explore_without_a_callback_still_ends() -> None:
    conn = FakeConnection([_tool_call("go_explore"), None])
    s, states, _ = _session(conn, [FakeAgent(None, None)])
    asyncio.run(s.run())
    assert s.explore_requested and states[-1] == "idle"


def test_instructions_mention_go_explore() -> None:
    # the voice half only has to know to delegate; the tool workflow is the backend's
    from cc_buddy_bridge.voice_agent import BACKEND_INSTRUCTIONS, INSTRUCTIONS
    assert "explore" in INSTRUCTIONS
    assert "Go explore" in BACKEND_INSTRUCTIONS and "go_explore" in BACKEND_INSTRUCTIONS


def test_prompts_do_not_let_a_started_task_read_as_done() -> None:
    """2026-09-10: the backend said "Spotify playback requested." and the voice turned it into
    "It's playing now." 12 s before the task finished."""
    from cc_buddy_bridge.voice_agent import BACKEND_INSTRUCTIONS, INSTRUCTIONS
    assert "Starting a task is not finishing it" in INSTRUCTIONS and 'INSTEAD: "On it."' in INSTRUCTIONS
    # the backend stays silent after start_task (its "On it." doubled the voice's own), and
    # never asks a clarifying question (the "what city?" round trip cost 10.4 s)
    assert "reply with an empty message" in BACKEND_INSTRUCTIONS
    assert "return exactly: On it" not in BACKEND_INSTRUCTIONS
    assert "Never ask the owner a clarifying question" in BACKEND_INSTRUCTIONS


def test_idle_timeout_closes_when_the_server_goes_quiet() -> None:
    """3/3 logged idle closes hung until the next Live event (+91 s, +129 s, +93 min)."""
    clock = {"now": 0.0}
    conn = FakeConnection()                        # nothing arrives after session.started
    s, states, _ = _session(conn, [FakeAgent(None, None)], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        clock["now"] = 30.0                        # past the 20 s idle timeout
        await asyncio.wait_for(task, timeout=2.0)  # ends with NO further event — no feed(None)
    asyncio.run(go())
    assert s._ended.is_set() and states[-1] == "idle"


def test_started_task_goes_working_at_once_and_tells_the_voice_quietly() -> None:
    agent = FakeAgent(None, None)
    # "listening" first: task events never set "working" from there, so only start_task can
    conn = FakeConnection([_heard("open my mail"), _tool_call("start_task", goal="open mail")])
    s, states, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        assert states[-1] == "working"
        quiet = [kw["content"] for k, kw in conn.sent if k == "session.thinking.append"]
        assert len(quiet) == 1 and "open mail" in quiet[0] and "Nothing is done yet" in quiet[0]
        agent.release.set()
        await asyncio.sleep(0.02)
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())


def test_stopped_task_is_not_done_and_gives_the_owners_reason() -> None:
    agent = FakeAgent(None, None)
    conn = FakeConnection([_tool_call("start_task", "c1", goal="open mail")])
    s, states, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        conn.feed(_tool_call("stop_task", "c2"))
        await asyncio.sleep(0.02)
        assert agent.cancelled and agent.reason == "you asked me to stop"
        s._on_agent_event(AgentEvent("cancelled", "Stopped."))
        assert states[-1] == "listening"            # no done nod for a stopped task
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())


def test_closing_session_speaks_no_result() -> None:
    """A hush used Task.cancel() but the result path checked only `_ended`, so a hushed
    conversation still tried to speak its result (15:50:04.623)."""
    agent = FakeAgent(None, None, final="Mail is open.")
    conn = FakeConnection([_tool_call("start_task", "c1", goal="open mail")])
    s, _, _ = _session(conn, [agent])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        task.cancel()                               # the hush path
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    # the greeting is the only thing the voice was handed: no result of any kind after the hush
    assert len(conn.commentary()) == 1 and "Greet" in conn.commentary()[0]


# ---- eyes, head, standing orders (2026-09-10) -------------------------------------------------

def _thinking(conn: FakeConnection) -> list[str]:
    return [kw["content"] for k, kw in conn.sent if k == "session.thinking.append"]


class FakeScene:
    def __init__(self, view: dict | None = None) -> None:
        self.on_note = None
        self.started = self.stopped = 0
        self.view = view or {"ok": True, "view": "A person holding a green mug.", "seen_at": "16:43:05",
                             "age_secs": 1.0, "stale": False}
        self.located: list[str] = []

    def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def look(self, fresh_secs: float = 4.0, newer_than=None) -> dict:
        await asyncio.sleep(0)
        return self.view

    async def locate(self, target: str, newer_than=None) -> dict:
        self.located.append(target)
        return {"ok": True, "visible": True, "x": 0.0, "y": 0.0, "what": target, "yaw": 0, "pitch": 45}


class FakeHead:
    def __init__(self) -> None:
        self.moves: list[tuple] = []
        self.yaw, self.pitch = 0.0, 45.0

    def clock(self) -> float:
        return 0.0

    async def sleep(self, secs: float) -> None:
        await asyncio.sleep(0)

    async def move(self, yaw=None, pitch=None, relative=False, hold_secs=15.0) -> dict:
        self.moves.append((yaw, pitch, relative, hold_secs))
        if yaw is not None:
            self.yaw = self.yaw + yaw if relative else yaw
        if pitch is not None:
            self.pitch = self.pitch + pitch if relative else pitch
        return {"ok": True, "yaw": int(self.yaw), "pitch": int(self.pitch), "facing": "x", "hold_secs": hold_secs}

    async def face_owner(self) -> dict:
        return await self.move(0, 45, hold_secs=3.0)


def test_scene_notes_reach_the_voice_as_silent_context_and_stop_with_the_session() -> None:
    conn = FakeConnection()
    scene = FakeScene()
    s, _, _ = _session(conn, [FakeAgent(None, None)], scene=scene)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        assert scene.started == 1 and scene.on_note is not None
        scene.on_note("[vision 16:43:05] A person holding a green mug.")
        await asyncio.sleep(0.01)
        assert "[vision 16:43:05] A person holding a green mug." in _thinking(conn)
        conn.feed(_tool_call("end_conversation"), None)
        await task
    asyncio.run(go())
    assert scene.stopped == 1 and scene.on_note is None


def test_look_tool_answers_from_a_background_task() -> None:
    conn = FakeConnection([_tool_call("look", "c1")])
    s, _, _ = _session(conn, [FakeAgent(None, None)], scene=FakeScene())

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())
    assert conn.tool_outputs()[0]["view"] == "A person holding a green mug."


def test_look_without_vision_says_why() -> None:
    conn = FakeConnection([_tool_call("look", "c1")])
    s, _, _ = _session(conn, [FakeAgent(None, None)])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())
    assert conn.tool_outputs()[0] == {"ok": False, "reason": "vision is not set up on this computer"}


def test_move_head_passes_the_backend_numbers_through() -> None:
    head = FakeHead()
    conn = FakeConnection([_tool_call("move_head", "c1", yaw=-15, relative=True),
                           _tool_call("move_head", "c2", yaw=120, pitch=45, relative=False, hold_secs=30),
                           _tool_call("move_head", "c3", yaw="left", relative=True),
                           _tool_call("end_conversation", "c9"), None])
    s, _, _ = _session(conn, [FakeAgent(None, None)], head=head)
    asyncio.run(s.run())
    assert head.moves[0] == (-15.0, None, True, 15.0)
    assert head.moves[1] == (120.0, 45.0, False, 30.0)
    assert head.moves[2] == (None, None, True, 15.0)          # a non-number is dropped, not guessed
    assert conn.tool_outputs()[1]["yaw"] == 120


def test_look_around_and_find_run_through_head_and_scene() -> None:
    head, scene = FakeHead(), FakeScene()
    conn = FakeConnection([_tool_call("look_around", "c1")])
    s, _, _ = _session(conn, [FakeAgent(None, None)], head=head, scene=scene)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.1)
        conn.feed(_tool_call("find", "c2", target="the door"))
        await asyncio.sleep(0.1)
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())
    outs = conn.tool_outputs()
    around = next(o for o in outs if "views" in o)
    assert around["ok"] is True and len(around["views"]) == 5
    found = next(o for o in outs if "found" in o)
    assert found["found"] is True and scene.located == ["the door"]


@pytest.mark.parametrize("words", ["Okay, bye buddy.", "I want you to leave now.", "Stop listening."])
def test_goodbye_in_the_owners_words_ends_the_session(words: str) -> None:
    clock = {"now": 0.0}
    conn = FakeConnection()
    s, states, mic = _session(conn, [FakeAgent(None, None)], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard(words))
        await asyncio.sleep(0.01)
        conn.feed(_spoke("Bye!"))                       # buddy's goodbye closes the user turn
        await asyncio.sleep(0.01)
        assert s._farewell and not s._ended.is_set()    # not before the goodbye has been said
        clock["now"] += TURN_GAP_SECS + 0.1             # the goodbye goes quiet...
        await asyncio.sleep(0.1)
        clock["now"] += 1.0
        await asyncio.sleep(0.7)                        # ...and the watchdog closes the session
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert states[-1] == "idle"
    assert any("[leaving]" in t for t in _thinking(conn))
    assert s.tool_calls == []                           # it never depended on the model calling a tool


def test_goodbye_closes_even_if_buddy_says_nothing_back() -> None:
    clock = {"now": 0.0}
    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard("goodbye"))
        await asyncio.sleep(0.01)
        clock["now"] += TURN_GAP_SECS + 0.1             # the user turn closes on the gap
        await asyncio.sleep(0.1)
        assert s._farewell and not s._ended.is_set()
        clock["now"] += FAREWELL_MAX_SECS
        await asyncio.sleep(0.7)
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())


def test_the_classifier_catches_a_goodbye_the_phrase_table_does_not_know() -> None:
    clock = {"now": 0.0}
    seen: list[str] = []

    async def intent(text: str):
        seen.append(text)
        return "leave" if "heading out" in text else None

    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)], clock=clock, intent=intent)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard("alright little guy, I'm heading out"), _spoke("See ya."))
        await asyncio.sleep(0.05)
        assert s._farewell
        clock["now"] += TURN_GAP_SECS + FAREWELL_QUIET_SECS + 0.5
        await asyncio.sleep(0.7)
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert seen == ["alright little guy, I'm heading out"]


def test_leave_the_tab_open_does_not_end_anything() -> None:
    clock = {"now": 0.0}

    async def intent(text: str):
        return None                                       # the model says: not about buddy

    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)], clock=clock, intent=intent)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard("leave the tab open"), _spoke("On it."))
        await asyncio.sleep(0.05)
        clock["now"] += 8.0
        await asyncio.sleep(0.7)
        assert not s._farewell and not s._ended.is_set()
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())


def test_goodbye_during_a_task_stops_the_mic_and_waits_for_the_result() -> None:
    agent = FakeAgent(None, None, final="Spotify is playing.")
    clock = {"now": 0.0}
    conn = FakeConnection([_tool_call("start_task", "c1", goal="play music")])
    s, _, mic = _session(conn, [agent], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.02)
        conn.feed(_done(), _heard("thanks, bye"), _spoke("Bye!"))
        await asyncio.sleep(0.02)
        assert s._farewell and s.task_running and not agent.cancelled   # the task is not killed
        before = len([k for k in conn.kinds() if k == "session.input_audio.append"])
        mic.put_nowait(b"\x00\x01" * 50)
        await asyncio.sleep(0.02)
        after = len([k for k in conn.kinds() if k == "session.input_audio.append"])
        assert after == before                                          # buddy stopped listening
        clock["now"] += FAREWELL_MAX_SECS + 1.0
        await asyncio.sleep(0.7)
        assert not s._ended.is_set()                                    # still waiting on the task
        agent.release.set()
        await asyncio.sleep(0.02)
        assert "Spotify is playing." in conn.commentary()[-1]           # the result is said first
        conn.feed(_spoke("Spotify is playing."))
        await asyncio.sleep(0.01)
        clock["now"] += TURN_GAP_SECS + FAREWELL_QUIET_SECS + 0.2
        await asyncio.sleep(0.7)
        assert s._ended.is_set()
        conn.feed(None)
        await task
    asyncio.run(go())
    assert not agent.cancelled


def test_mute_and_unmute_by_voice_keep_the_conversation_going() -> None:
    clock = {"now": 0.0}
    sound = {"on": True}
    calls: list[bool] = []

    def on_sound(on: bool) -> None:
        calls.append(on)
        sound["on"] = on

    pcm = base64.b64encode(b"\x00\x10" * 50).decode()
    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)], clock=clock, on_sound=on_sound,
                       muted=lambda: not sound["on"])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard("mute"), _spoke("Okay."))
        await asyncio.sleep(0.02)
        assert calls == [False] and not s._ended.is_set() and not s._farewell
        conn.feed({"type": "session.output_audio.delta", "delta": pcm})
        await asyncio.sleep(0.01)
        assert s.speaker.played == b""                                  # muted: the Mac plays nothing
        clock["now"] += TURN_GAP_SECS + 0.1
        conn.feed(_heard("unmute yourself"), _spoke("Back."))
        await asyncio.sleep(0.02)
        assert calls == [False, True]
        conn.feed({"type": "session.output_audio.delta", "delta": pcm})
        await asyncio.sleep(0.01)
        assert s.speaker.played == b"\x00\x10" * 50
        conn.feed(_tool_call("set_sound", "c1", on=False), _tool_call("set_sound", "c2", on="loud"))
        await asyncio.sleep(0.02)
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())
    notes = _thinking(conn)
    assert any(n.startswith("[sound] You are muted") for n in notes)
    assert any(n == "[sound] Your sound is back on." for n in notes)
    assert calls == [False, True, False]
    assert conn.tool_outputs()[0] == {"ok": True, "sound": "off"}
    assert conn.tool_outputs()[1] == {"ok": False, "reason": "on must be true or false"}


def test_a_slow_look_holds_off_the_idle_close_and_never_logs_the_view(caplog) -> None:
    clock = {"now": 0.0}
    gate = asyncio.Event()

    class SlowScene(FakeScene):
        async def look(self, fresh_secs: float = 4.0, newer_than=None) -> dict:
            await gate.wait()
            return self.view

    conn = FakeConnection([_tool_call("look", "c1")])
    s, _, _ = _session(conn, [FakeAgent(None, None)], clock=clock, scene=SlowScene())

    async def go():
        with caplog.at_level("INFO"):
            task = asyncio.create_task(s.run())
            await asyncio.sleep(0.02)
            clock["now"] += 60.0                       # far past the 20 s idle timeout
            await asyncio.sleep(0.7)
            assert not s._ended.is_set()               # a look in flight is not idleness
            gate.set()
            await asyncio.sleep(0.05)
            assert conn.tool_outputs()[0]["ok"] is True
            conn.feed(_tool_call("end_conversation", "c9"), None)
            await task
    asyncio.run(go())
    assert not any("green mug" in r.getMessage() for r in caplog.records)


def test_a_head_request_the_voice_did_not_delegate_is_routed_to_the_backend() -> None:
    """17:43 on the bench: "look a bit up and to your left" got "Okay, looking." and no tool call."""
    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)])

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard("Look a bit up and to your left."), _spoke("Okay, looking."))
        await asyncio.sleep(LOOK_ROUTE_DELAY_SECS + 0.2)
        assert conn.user_messages() == ["[look request] Look a bit up and to your left."]
        assert conn.kinds()[-1] == "response.create"          # the backend is asked to act on it
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())


def test_a_head_request_the_voice_delegated_is_not_routed_twice() -> None:
    clock = {"now": 0.0}
    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)], clock=clock)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        clock["now"] = 5.0
        conn.feed(_heard("look a bit more left"))
        await asyncio.sleep(0.01)
        clock["now"] = 5.3
        conn.feed(_delegated(), _spoke("Turning."))          # the voice delegated it itself
        await asyncio.sleep(LOOK_ROUTE_DELAY_SECS + 0.2)
        assert conn.user_messages() == []
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())


def test_the_classifier_routes_a_head_request_the_table_does_not_know() -> None:
    async def intent(text: str):
        return "look" if "keys" in text else None

    conn = FakeConnection()
    s, _, _ = _session(conn, [FakeAgent(None, None)], intent=intent)

    async def go():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.01)
        conn.feed(_heard("where did I leave my keys"), _spoke("Let me see."))
        await asyncio.sleep(LOOK_ROUTE_DELAY_SECS + 0.2)
        assert conn.user_messages() == ["[look request] where did I leave my keys"]
        conn.feed(_tool_call("end_conversation", "c9"), None)
        await task
    asyncio.run(go())


def test_a_muted_session_starts_by_telling_the_voice() -> None:
    conn = FakeConnection([_tool_call("end_conversation"), None])
    s, _, _ = _session(conn, [FakeAgent(None, None)], muted=lambda: True)
    asyncio.run(s.run())
    assert _thinking(conn)[0].startswith("[sound] You are muted")

