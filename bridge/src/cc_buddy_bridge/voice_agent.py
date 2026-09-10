"""Voice: after "hey buddy", a spoken conversation that can run the computer.

One `VoiceSession` is one conversation. It opens a Live API session
(gpt-live-1, full duplex: it listens while it speaks and does its own
turn-taking), pipes the daemon's microphone into it (ears.py `subscribe()`),
and delegates every decision that needs tools to a Responses backend with
seven tools:

    start_task(goal)      run a gpt-6-astra computer-use task (computer_agent.py)
    steer_task(text)      change what the running task is doing, mid-task
    stop_task()           cancel it
    task_status()         what the task is up to
    answer_question(text) relay the human's answer to the task's ask_user
    go_explore()          "go explore" / "look around": ends the conversation and
                          the daemon starts a manual explore (explore.py)
    end_conversation()    "bye buddy"

The task runs in the background while the conversation continues, so the
human can talk over it: "no, the other tab", "stop", "how's it going".
When the task finishes, its final message is handed back to the voice
model to say out loud. When the task needs a yes/no (`ask_user`), the
question is spoken and the next answer goes back through answer_question.

The robot mirrors every phase over the wire (`{"cmd":"agent","state":...}`):
wake, listening, thinking, speaking, working, asking, done, error, idle.
It is not the one doing the work — the daemon is — but it acts as if it
were: head down toward the desk and quick eyes while working, a nod on
done, a wince on error (firmware body.cpp / eyes.cpp).

Output: by default buddy does NOT speak through the Mac. gpt-live-1 has no
text-only mode — it always generates speech — so captions mode reads
`session.output_transcript.delta` and simply never plays `session.output_audio.delta`.
Every reply is wrapped into pages of 4 lines x 17 chars and shown on the
robot's screen one page at a time at reading pace (caption_pager.py); the robot
chirps once per page and keeps its speaking pose until the last page has been
held (owner request 2026-09-06). CC_BUDDY_VOICE_OUTPUT=audio plays that same
audio through the Mac speaker instead.

Transcript deltas carry no turn boundary ("these events do not define complete
turns"), so `_Turns` below closes a caption page on a speaker change or a gap
of `TURN_GAP_SECS` with nothing more said. The SDK ships `AsyncTranscriptGrouper`
for this, but it runs its own daemon timers; `_Turns` uses the session's
injectable clock instead, so every caption test stays deterministic.

The session ends on end_conversation, after IDLE_TIMEOUT with nothing said
and no task running, or at MAX_SESSION. Every seam (connection, speaker,
mic queue, agent factory, clock) is injectable, so the whole state machine
is unit-tested with fakes; the real wiring lives in `open_session()`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .caption_pager import CaptionPager, Event, PagerConfig, caption_instructions
from .computer_agent import AgentConfig, AgentEvent, ComputerAgent

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-live-1"
# The Live model is the voice; every tool call is made by a Responses backend it
# delegates to. gpt-6-astra is the repo's strongest model and is already on the
# account for computer use; CC_BUDDY_LIVE_BACKEND_MODEL trades quality for
# latency (gpt-5-mini answers faster and calls start_task less reliably).
DEFAULT_BACKEND_MODEL = "gpt-6-astra"
DEFAULT_BACKEND_EFFORT = "low"
DEFAULT_VOICE = "marin"
DEFAULT_IDLE_TIMEOUT_SECS = 20.0
DEFAULT_MAX_SESSION_SECS = 600.0
SPEAKER_TAIL_SECS = 0.4        # mic stays muted this long after the speaker drains (audio mode only)
TURN_GAP_SECS = 1.2            # silence that closes a caption page, absent a transcript-done event
SAMPLE_RATE = 24000

# The Live model owns voice, timing and interruptions. Tool workflow lives in
# the backend prompt, per OpenAI's Live delegation guidance.
INSTRUCTIONS = """You are buddy, a small desk robot with a cheerful, curious personality, talking with your owner.
You just heard your wake word. Answer in one or two short spoken sentences; no lists, no markdown, no
offers of things you "can help with" — you are a pet, not an assistant menu.

You can operate your owner's Mac for them. The moment they ask for anything on the computer, delegate it
— say nothing first, do not guess an app, do not narrate steps you have not seen. When the work comes
back, tell them the result in one short line.

Delegate anything that changes what a running task is doing, ends the conversation, or sends you off to
explore. Chit-chat you answer yourself.

Never claim to have done something you did not do."""

BACKEND_INSTRUCTIONS = """You are the reasoning half of buddy, a small desk robot. You never speak; you
call tools and return one short line for buddy to say.

- A request to do something on the owner's Mac → call start_task at once, with the goal in the owner's own
  words plus any app or site they named. Never guess an app or hedge ("likely in a music app").
- While a task runs: "stop" / "cancel" / "never mind" → stop_task. A correction or addition ("use Safari
  instead", "also save it") → steer_task with the text. "How's it going?" → task_status, summarised in one
  line.
- A message tagged [task question] is a question buddy has just asked the owner out loud for the running
  task. When the owner answers, relay it with answer_question as plain words ("yes", "no", "the second one").
- Goodbye, thanks, "that's all", "stop listening", "go to sleep", "be quiet" or "never mind" with no task
  running → end_conversation.
- "Go explore", "look around", "go play", "check out the room" → go_explore. If they tell you to stop
  exploring or come back, you already stopped when you woke — say so and call end_conversation.

Return at most one short sentence. Never invent a result you did not get from a tool."""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "start_task",
     "description": "Start a computer-use task on the owner's Mac. Returns immediately; the task runs in the background.",
     "parameters": {"type": "object", "properties": {"goal": {"type": "string",
                    "description": "What to accomplish, in the owner's own words, naming any app or site they "
                                   "named. Do not add guesses."}},
                    "required": ["goal"], "additionalProperties": False}},
    {"type": "function", "name": "steer_task",
     "description": "Send a correction or extra instruction to the running task.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"], "additionalProperties": False}},
    {"type": "function", "name": "stop_task",
     "description": "Cancel the running task immediately.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "task_status",
     "description": "What the running task is doing right now.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "answer_question",
     "description": "Relay the owner's answer to the task's pending question.",
     "parameters": {"type": "object", "properties": {"answer": {"type": "string"}},
                    "required": ["answer"], "additionalProperties": False}},
    {"type": "function", "name": "go_explore",
     "description": "The owner told you to go explore / look around the room. Ends the conversation; "
                    "you then pan the room and take notes on your own.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "end_conversation",
     "description": "End the conversation (the owner said goodbye or is done).",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
]

DEFAULT_CAPTION_CPS = PagerConfig().read_cps


@dataclass(frozen=True)
class VoiceConfig:
    model: str = DEFAULT_MODEL
    backend_model: str = DEFAULT_BACKEND_MODEL
    backend_effort: str = DEFAULT_BACKEND_EFFORT
    voice: str = DEFAULT_VOICE
    idle_timeout_secs: float = DEFAULT_IDLE_TIMEOUT_SECS
    max_session_secs: float = DEFAULT_MAX_SESSION_SECS
    output: str = "captions"      # "captions" (text → robot screen + beeps) or "audio" (Mac speaker)
    caption_cps: float = DEFAULT_CAPTION_CPS   # reading rate the caption page holds derive from (5..30)


def configured(environ: Any = None) -> VoiceConfig:
    env = os.environ if environ is None else environ
    if env.get("CC_BUDDY_REALTIME_MODEL"):
        log.warning("voice: CC_BUDDY_REALTIME_MODEL is a Realtime-era name and is ignored; "
                    "set CC_BUDDY_LIVE_MODEL instead")
    model = (env.get("CC_BUDDY_LIVE_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    backend = (env.get("CC_BUDDY_LIVE_BACKEND_MODEL") or DEFAULT_BACKEND_MODEL).strip() or DEFAULT_BACKEND_MODEL
    effort = (env.get("CC_BUDDY_LIVE_BACKEND_EFFORT") or DEFAULT_BACKEND_EFFORT).strip().lower()
    if effort not in ("none", "minimal", "low", "medium", "high", "xhigh"):
        log.warning("voice: CC_BUDDY_LIVE_BACKEND_EFFORT=%r is not a reasoning effort; using %s",
                    effort, DEFAULT_BACKEND_EFFORT)
        effort = DEFAULT_BACKEND_EFFORT
    voice = (env.get("CC_BUDDY_VOICE_NAME") or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    idle = DEFAULT_IDLE_TIMEOUT_SECS
    raw = (env.get("CC_BUDDY_VOICE_IDLE_SECS") or "").strip()
    if raw:
        try:
            idle = max(5.0, float(raw))
        except ValueError:
            log.warning("voice: CC_BUDDY_VOICE_IDLE_SECS=%r is not a number; using %s", raw, idle)
    out = (env.get("CC_BUDDY_VOICE_OUTPUT") or "captions").strip().lower()
    if out not in ("captions", "audio"):
        log.warning("voice: CC_BUDDY_VOICE_OUTPUT=%r is not captions|audio; using captions", out)
        out = "captions"
    cps = DEFAULT_CAPTION_CPS
    raw = (env.get("CC_BUDDY_CAPTION_CPS") or "").strip()
    if raw:
        try:
            cps = min(30.0, max(5.0, float(raw)))
        except ValueError:
            log.warning("voice: CC_BUDDY_CAPTION_CPS=%r is not a number; using %s", raw, cps)
    return VoiceConfig(model=model, backend_model=backend, backend_effort=effort, voice=voice,
                       idle_timeout_secs=idle, output=out, caption_cps=cps)


def session_config(config: VoiceConfig) -> dict[str, Any]:
    """The `session.start` payload (Live API, openai 3.13).

    There is no `output_modalities` and no turn-detection block: gpt-live-1 is
    full duplex and always speaks. Captions mode reads the output transcript and
    drops the audio, so the voice is configured either way.
    """
    captions = config.output == "captions"
    return {
        "model": config.model,
        "instructions": INSTRUCTIONS + (caption_instructions(PagerConfig(read_cps=config.caption_cps))
                                        if captions else ""),
        "audio": {
            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
            "output": {"voice": config.voice},
        },
        "delegation": {
            "type": "responses",
            "responses": {
                "model": config.backend_model,
                "instructions": BACKEND_INSTRUCTIONS,
                "tools": TOOLS,
                "tool_choice": "auto",
                "reasoning": {"effort": config.backend_effort},
                "parallel_tool_calls": False,
            },
        },
    }


# ---- speaker (seam) ----------------------------------------------------------------------

class Speaker:
    """24 kHz int16 mono out through sounddevice; stop() drops what is queued (barge-in)."""

    def __init__(self) -> None:
        self._stream: Any = None
        self._buf = bytearray()
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            import sounddevice as sd
        except ImportError:
            log.warning("voice: sounddevice not importable — replies will be silent")
            return False

        def cb(outdata: Any, frames: int, _t: Any, _status: Any) -> None:
            need = frames * 2
            with self._lock:
                chunk = bytes(self._buf[:need])
                del self._buf[:need]
            if len(chunk) < need:
                chunk += b"\x00" * (need - len(chunk))
            outdata[:] = _to_frames(chunk, frames)

        try:
            self._stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=cb)
            self._stream.start()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("voice: could not open the speaker (%s)", e)
            self._stream = None
            return False

    def play(self, pcm: bytes) -> None:
        with self._lock:
            self._buf.extend(pcm)

    def stop(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._buf)

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None


class NullSpeaker:
    """Captions mode: nothing is played on the Mac; the robot beeps instead."""

    def start(self) -> bool:
        return True

    def play(self, pcm: bytes) -> None:
        pass

    def stop(self) -> None:
        pass

    @property
    def busy(self) -> bool:
        return False

    def close(self) -> None:
        pass


def _to_frames(chunk: bytes, frames: int) -> Any:
    import numpy as np

    return np.frombuffer(chunk, dtype=np.int16).reshape(frames, 1)


class _Turns:
    """Turn boundaries for a stream that has none.

    `session.output_transcript.delta` and `session.input_transcript.delta` carry
    no done event, so a turn is closed here: by a change of speaker, or by `gap`
    seconds with nothing more said. Driven by the session's clock, never a timer.
    """

    def __init__(self, gap: float = TURN_GAP_SECS) -> None:
        self.gap = gap
        self.speaker: Optional[str] = None
        self.text = ""
        self._last = float("-inf")

    def delta(self, who: str, text: str, now: float) -> Optional[tuple[str, str]]:
        """Add a fragment. Returns the turn it displaced, as (speaker, text)."""
        closed: Optional[tuple[str, str]] = None
        if self.speaker is not None and self.text and (who != self.speaker or now - self._last > self.gap):
            closed = (self.speaker, self.text)
            self.text = ""
        self.speaker = who
        self.text += text
        self._last = now
        return closed

    def due(self, now: float) -> Optional[tuple[str, str]]:
        """Close the open turn once it has gone quiet for `gap`."""
        if self.speaker is not None and self.text and now - self._last > self.gap:
            closed = (self.speaker, self.text)
            self.text, self.speaker = "", None
            return closed
        return None

    def flush(self) -> Optional[tuple[str, str]]:
        if self.speaker is not None and self.text:
            closed = (self.speaker, self.text)
            self.text, self.speaker = "", None
            return closed
        return None


# ---- the session ---------------------------------------------------------------------------

class VoiceSession:
    """One conversation. See the module docstring."""

    def __init__(
        self,
        connection: Any,                                   # Live connection (async ctx manager already entered)
        mic: "asyncio.Queue[bytes]",                        # from Ears.subscribe()
        speaker: Any,                                       # Speaker-like: play/stop/busy
        agent_factory: Callable[[Callable[[AgentEvent], None], Callable[[str], Awaitable[str]]], ComputerAgent],
        on_state: Callable[[str], None],
        config: Optional[VoiceConfig] = None,
        agent_config: Optional[AgentConfig] = None,
        clock: Callable[[], float] = time.monotonic,
        agent_enabled: bool = True,
        on_caption: Optional[Callable[[dict], None]] = None,
        caption_tick_secs: float = 0.05,
        on_explore: Optional[Callable[[], None]] = None,
        turn_gap_secs: float = TURN_GAP_SECS,
    ) -> None:
        self.conn = connection
        self.on_caption = on_caption
        self.on_explore = on_explore
        self.explore_requested = False
        self._caption = ""
        self.mic = mic
        self.speaker = speaker
        self.agent_factory = agent_factory
        self.on_state = on_state
        self.config = config or VoiceConfig()
        self.agent_config = agent_config or AgentConfig()
        self._clock = clock
        # Captions: the pager decides which page is up and for how long; the
        # board only draws. Polled after every text event and by _pager_loop.
        self._pager = CaptionPager(PagerConfig(read_cps=self.config.caption_cps))
        self._captions = self.config.output == "captions" and on_caption is not None
        self._caption_tick = caption_tick_secs
        self._state_after_captions: Optional[str] = None
        self.agent_enabled = agent_enabled
        self.state = "idle"
        self.agent: Optional[ComputerAgent] = None
        self._agent_task: Optional[asyncio.Task[str]] = None
        self._pending_answer: Optional[asyncio.Future[str]] = None
        self._ended = asyncio.Event()
        self._last_activity = self._clock()
        self._started_at = self._clock()
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.transcript: list[str] = []
        # The Responses backend runs one response at a time. A function call is
        # reported before that response completes, so a response.create sent right
        # after a tool result would be rejected. Track the in-flight backend
        # response and defer our creates until it completes.
        self._response_active = False
        self._response_wanted = False
        # Live has no transcript-done event; _Turns infers the boundary.
        self._turns = _Turns(turn_gap_secs)
        self._reply_open = False
        self._speaking_until = 0.0
        # After a task finishes, buddy says the result and the conversation
        # closes — it must not sit there listening (owner request 2026-09-06).
        # Set when the [task finished] message goes in; acted on when the
        # response that speaks it is done.
        self._end_after_response = False
        self._last_progress_at = float("-inf")

    # -- state --
    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            try:
                self.on_state(state)
            except Exception:  # noqa: BLE001
                log.exception("voice: on_state failed")

    @property
    def task_running(self) -> bool:
        # The agent's own `running` flips inside run(); between start_task and
        # the first yield the task exists but has not started — count that too.
        return self._agent_task is not None and not self._agent_task.done()

    # -- lifecycle --
    async def run(self) -> None:
        self._set("wake")
        self._started_at = self._last_activity = self._clock()
        await self.conn.session.start(session=session_config(self.config))
        await self._await_started()
        # A fixed greeting needs no backend round-trip: commentary is context the
        # Live model speaks itself.
        await self.conn.session.commentary.append(
            content="The owner just said your wake word. Greet them in one or two words, like 'Yeah?'.",
            delegation_id=None)
        pump = asyncio.create_task(self._pump_mic(), name="voice-mic")
        watchdog = asyncio.create_task(self._watchdog(), name="voice-watchdog")
        ticker = asyncio.create_task(self._tick_loop(), name="voice-ticks")
        cancelled = False
        try:
            await self._events()
        except asyncio.CancelledError:
            cancelled = True                        # hush (a touch on the robot): clear the board at once
            raise
        finally:
            pump.cancel()
            watchdog.cancel()
            await asyncio.gather(pump, watchdog, return_exceptions=True)
            if self.task_running and self.agent is not None:
                self.agent.cancel(reason="the conversation closed")
            if self._agent_task is not None:
                await asyncio.gather(self._agent_task, return_exceptions=True)
            ticker.cancel()                     # one poller at a time: the loop stops before the drain
            await asyncio.gather(ticker, return_exceptions=True)
            closing = self._turns.flush()       # the last turn never went quiet; close it now
            if closing is not None and not cancelled:
                self._close_turn(*closing)
            if self._captions:
                if cancelled:
                    self._emit_events(self._pager.reset(self._clock()))
                else:
                    try:
                        await self._drain_captions()
                    except asyncio.CancelledError:
                        self._emit_events(self._pager.reset(self._clock()))
                        raise

            await self._drain_speaker()
            self.speaker.stop()
            self._set("idle")

    async def _await_started(self, timeout: float = 15.0) -> None:
        """Audio sent before `session.started` is discarded, so wait for it."""
        async def wait() -> None:
            async for event in self.conn:
                t = _attr(event, "type")
                if t == "session.started":
                    return
                if t == "error":
                    raise RuntimeError(f"live: session start rejected: {_attr(event, 'error')}")
                await self.handle_event(event)
        try:
            await asyncio.wait_for(wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("voice: no session.started within %.0f s — continuing anyway", timeout)

    def end(self) -> None:
        self._ended.set()

    async def _drain_speaker(self, timeout: float = 15.0) -> None:
        """Let a queued reply finish playing (barge-in and errors skip this)."""
        deadline = self._clock() + timeout
        while self.speaker.busy and self._clock() < deadline:
            await asyncio.sleep(0.05)

    # -- captions --
    def _set_after_captions(self, state: str) -> None:
        """The phase flips when the last caption page has been read, not when the model stops."""
        if self._pager.busy:
            self._state_after_captions = state
        else:
            self._set(state)

    def _emit_events(self, events: list[Event]) -> None:
        if self.on_caption is None:
            return
        for e in events:
            try:
                self.on_caption(e.to_wire())
            except Exception:  # noqa: BLE001
                log.exception("voice: on_caption failed")

    def _flush_pager(self) -> None:
        if not self._captions:
            return
        self._emit_events(self._pager.poll(self._clock()))
        if not self._pager.busy and self._state_after_captions is not None:
            st, self._state_after_captions = self._state_after_captions, None
            if not self._response_active:
                self._set(st)

    async def _tick_loop(self) -> None:
        """Closes a turn that has gone quiet, and pages captions when they are on.

        Runs in both output modes: the turn boundary is what ends a conversation
        after a task result, and audio mode needs it too.
        """
        while not self._ended.is_set():
            await asyncio.sleep(self._caption_tick)
            self._close_due_turn()
            if self._captions:
                self._flush_pager()

    async def _drain_captions(self, timeout: float = 15.0) -> None:
        """Let the page on screen finish its hold before the session goes idle."""
        deadline = self._clock() + timeout
        while self._pager.busy and self._clock() < deadline:
            await asyncio.sleep(self._caption_tick)
            self._flush_pager()
        if self._pager.busy:
            self._emit_events(self._pager.reset(self._clock()))

    async def _pump_mic(self) -> None:
        # Captions mode plays nothing on the Mac, so there is no echo to dodge and
        # the mic runs full duplex — gpt-live-1 listens while it speaks and takes
        # the interruption itself.
        #
        # Audio mode still has no echo cancellation: the Mac mic hears the Mac
        # speaker, and buddy would answer its own greeting in a loop (bench
        # 2026-09-06 09:01). While a reply is playing, plus a short tail, the mic
        # is not forwarded. Cost: no barge-in mid-sentence; say it after.
        gate = self.config.output != "captions"
        while not self._ended.is_set():
            raw = await self.mic.get()
            if gate:
                if self.speaker.busy:
                    self._speaking_until = self._clock() + SPEAKER_TAIL_SECS
                    continue
                if self._clock() < self._speaking_until:
                    continue
            await self.conn.session.input_audio.append(audio=base64.b64encode(raw).decode("ascii"))

    async def _watchdog(self) -> None:
        while not self._ended.is_set():
            await asyncio.sleep(0.5)
            now = self._clock()
            if now - self._started_at > self.config.max_session_secs:
                log.info("voice: session hit its %.0f s cap", self.config.max_session_secs)
                self._ended.set()
            elif (not self.task_running and self._pending_answer is None and not self.speaker.busy
                  and not self._pager.busy and now - self._last_activity > self.config.idle_timeout_secs):
                log.info("voice: nothing said for %.0f s — closing", self.config.idle_timeout_secs)
                self._ended.set()

    async def _events(self) -> None:
        async for event in self.conn:
            if self._ended.is_set():
                return
            await self.handle_event(event)
            if self._ended.is_set():
                return

    async def handle_event(self, event: Any) -> None:
        t = _attr(event, "type")
        if t == "session.input_transcript.delta":
            self._transcript_delta("user", _attr(event, "delta") or "")
        elif t == "session.output_transcript.delta":
            self._transcript_delta("assistant", _attr(event, "delta") or "")
        elif t == "session.output_audio.delta":
            delta = _attr(event, "delta")
            if delta and not self._captions:
                self.speaker.play(base64.b64decode(delta))
                self._set("speaking")
        elif t == "session.delegation.created":
            # The Live model handed the turn to the backend: buddy is thinking.
            self._response_active = True
            if self._captions and not self._pager.busy:
                self._pager.begin_reply(self._clock())
                self._reply_open = True
            if not self._pager.busy:
                self._set("thinking")
        elif t == "response.event":
            await self._backend_event(_attr(event, "event") or {})
        elif t == "session.closed":
            self._ended.set()
        elif t == "error":
            log.warning("voice: live error: %s", _attr(event, "error"))

    async def _backend_event(self, ev: Any) -> None:
        """A Responses streaming event, nested inside a Live `response.event`."""
        et = _attr(ev, "type")
        if et == "response.created":
            self._response_active = True
        elif et == "response.output_item.done":
            item = _attr(ev, "item") or {}
            if _attr(item, "type") == "function_call":
                await self._tool(_attr(item, "name") or "",
                                 _attr(item, "call_id") or "",
                                 _attr(item, "arguments") or "{}")
        elif et in ("response.completed", "response.failed", "response.incomplete"):
            self._response_active = False
            self._last_activity = self._clock()
            if self._response_wanted and not self._ended.is_set():
                self._response_wanted = False
                await self.conn.response.create()
            elif self._turns.speaker != "assistant":
                # a tool-only response: nothing will be spoken, so the phase has to
                # move on without waiting for a caption page that never comes
                if self._captions and self._reply_open:
                    self._emit_events(self._pager.reset(self._clock()))
                    self._reply_open = False
                self._set_after_captions("working" if self.task_running else "listening")
        elif et == "response.output_text.done":
            # What the backend handed the voice. Logged because the voice speaks its own
            # words: without this line a wrong reply cannot be traced to either half.
            text = str(_attr(ev, "text") or "")
            if text:
                log.info("voice: backend said: %s", text[:160])
        elif et == "error":
            log.warning("voice: backend error: %s", _attr(ev, "error") or ev)

    # -- transcript turns --
    def _transcript_delta(self, who: str, text: str) -> None:
        if not text:
            return
        now = self._clock()
        closed = self._turns.delta(who, text, now)
        if closed is not None:
            self._close_turn(*closed)
        if who == "user":
            self._last_activity = now
            self.speaker.stop()                       # barge-in: the human talks over buddy
            if self._captions and (self._reply_open or self._pager.busy):
                self._emit_events(self._pager.reset(now))    # ... and over the page on screen
                self._reply_open = False
                self._state_after_captions = None
            self._set("listening")
            return
        self._set("speaking")
        if self._captions:
            if not self._reply_open:
                self._pager.begin_reply(now)
                self._reply_open = True
            self._pager.update(now, self._turns.text, False)
            self._flush_pager()

    def _close_due_turn(self) -> None:
        closed = self._turns.due(self._clock())
        if closed is not None:
            self._close_turn(*closed)

    def _close_turn(self, who: str, text: str) -> None:
        if who != "assistant" or not text:
            return
        now = self._clock()
        self.transcript.append(text)
        log.info("buddy: %s", text)
        self._last_activity = now
        if self._captions:
            if not self._reply_open:
                self._pager.begin_reply(now)
                self._reply_open = True
            self._pager.update(now, text, True)
            self._pager.end_reply(now)
            self._reply_open = False
            self._flush_pager()
        if self._end_after_response and not self._response_wanted and not self._response_active:
            # the result has been spoken, and nothing else is queued behind it:
            # the conversation closes (owner request 2026-09-06)
            self._end_after_response = False
            self._ended.set()
            return
        self._set_after_captions("working" if self.task_running else "listening")

    # -- tools --
    async def _tool(self, name: str, call_id: str, arguments: str) -> None:
        try:
            args = json.loads(arguments) if arguments else {}
        except ValueError:
            args = {}
        self.tool_calls.append((name, args))
        self._last_activity = self._clock()
        result: dict[str, Any]
        if name == "start_task":
            result = self._start_task(str(args.get("goal", "")).strip())
        elif name == "steer_task":
            ok = self.agent is not None and self.agent.steer(str(args.get("text", "")))
            result = {"ok": ok} if ok else {"ok": False, "reason": "no task is running"}
        elif name == "stop_task":
            if self.task_running and self.agent is not None:
                self.agent.cancel(reason="the conversation closed")
                result = {"ok": True}
            else:
                result = {"ok": False, "reason": "no task is running"}
        elif name == "task_status":
            result = self.agent.status() if self.agent is not None else {"running": False}
        elif name == "answer_question":
            ans = str(args.get("answer", "")).strip()
            if self._pending_answer is not None and not self._pending_answer.done():
                self._pending_answer.set_result(ans)
                result = {"ok": True}
            else:
                result = {"ok": False, "reason": "no question is pending"}
        elif name == "end_conversation":
            result = {"ok": True}
            self._ended.set()
        elif name == "go_explore":
            # Same shape as end_conversation: the send-off has been said, the
            # conversation closes, and the daemon starts the explore once
            # the board is free of the conversation pose.
            if self.task_running:
                result = {"ok": False, "reason": "a task is running; stop it first"}
            else:
                self.explore_requested = True
                if self.on_explore is not None:
                    try:
                        self.on_explore()
                    except Exception:  # noqa: BLE001
                        log.exception("voice: on_explore failed")
                result = {"ok": True}
                self._ended.set()
        else:
            result = {"ok": False, "reason": f"unknown tool {name}"}
        await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                   "output": json.dumps(result)})
        if not self._ended.is_set():
            await self._request_response()

    def _start_task(self, goal: str) -> dict[str, Any]:
        if not goal:
            return {"ok": False, "reason": "empty goal"}
        if not self.agent_enabled:
            return {"ok": False, "reason": "computer control is disabled (CC_BUDDY_COMPUTER_CONTROL=0)"}
        if self.task_running:
            return {"ok": False, "reason": "a task is already running; steer or stop it first"}
        self.agent = self.agent_factory(self._on_agent_event, self._ask_user)
        self._agent_task = asyncio.create_task(self._run_agent(goal), name="voice-agent")
        return {"ok": True, "goal": goal}

    async def _run_agent(self, goal: str) -> str:
        assert self.agent is not None
        final = await self.agent.run(goal)
        self._last_activity = self._clock()
        if not self._ended.is_set():
            self._end_after_response = True
            log.info("voice: task result to the voice: %s", final[:160])
            await self._speak(f"The computer task you started has finished. Tell the owner this result in one "
                              f"short line, then say a two-word goodbye: {final}")
        return final

    def _on_agent_event(self, ev: AgentEvent) -> None:
        if ev.kind == "progress":
            # A helper sentence from the task ("opened Safari") as a caption page
            # while the robot is working — when nothing else is on screen and at
            # least progress_min_gap_secs after the previous one.
            if self._captions and not self._pager.busy and self.state in ("working", "done", "idle", "listening"):
                now = self._clock()
                if now - self._last_progress_at >= self.agent_config.progress_min_gap_secs:
                    self._last_progress_at = now
                    self._pager.begin_reply(now)
                    self._pager.update(now, ev.text[:120], True)
                    self._flush_pager()
            return
        if ev.kind in ("started", "exec", "commentary", "turn"):
            if self.state not in ("speaking", "listening", "thinking", "asking"):
                self._set("working")
        elif ev.kind == "ask":
            self._set("asking")
        elif ev.kind == "final":
            self._set("done")
        elif ev.kind in ("cancelled", "error"):
            self._set("error" if ev.kind == "error" else "done")

    async def _ask_user(self, question: str) -> str:
        loop = asyncio.get_running_loop()
        self._pending_answer = loop.create_future()
        # The backend gets the question as context (no response): it is the half that
        # calls answer_question when the owner replies. The voice gets it to ask.
        await self._backend_note(f"[task question] {question}")
        await self._speak(f"The computer task needs an answer from the owner. Ask exactly this, then wait for "
                          f"their answer: {question}")
        try:
            return await asyncio.wait_for(self._pending_answer, timeout=60.0)
        except asyncio.TimeoutError:
            return "no (no answer within a minute)"
        finally:
            self._pending_answer = None

    async def _speak(self, content: str) -> None:
        """Hand the voice something to say (`session.commentary.append`).

        The SDK documents commentary as "speakable context … for a result the model
        should communicate". Routing a result through the backend instead
        (response.item.create + response.create) left the voice free to say anything:
        on 2026-09-10 a finished task, "Spotify is playing INTERGALACTIC.", reached the
        owner as "Okay, waiting."
        """
        await self.conn.session.commentary.append(content=content, delegation_id=None)

    async def _backend_note(self, text: str) -> None:
        """Context for the backend's next delegated turn; asks for no response."""
        await self.conn.response.item.create(item={"type": "message", "role": "user",
                                                   "content": [{"type": "input_text", "text": text}]})

    async def _request_response(self) -> None:
        """response.create now, or as soon as the in-flight backend response completes."""
        if self._response_active:
            self._response_wanted = True
        else:
            await self.conn.response.create()


def _attr(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


# ---- the real wiring --------------------------------------------------------------------------

async def open_session(
    mic: "asyncio.Queue[bytes]",
    on_state: Callable[[str], None],
    agent_factory: Callable[..., ComputerAgent],
    config: Optional[VoiceConfig] = None,
    agent_enabled: bool = True,
    api_key: Optional[str] = None,
    on_caption: Optional[Callable[[dict], None]] = None,
    on_explore: Optional[Callable[[], None]] = None,
) -> None:
    """Run one full conversation on the real Live API — captions to the robot,
    or the real speaker in audio mode."""
    from openai import AsyncOpenAI

    cfg = config or configured()
    client = AsyncOpenAI(api_key=api_key) if api_key else AsyncOpenAI()
    speaker: Any = NullSpeaker() if cfg.output == "captions" else Speaker()
    speaker.start()
    try:
        async with client.live.connect() as conn:
            session = VoiceSession(conn, mic, speaker, agent_factory, on_state, config=cfg,
                                   agent_enabled=agent_enabled, on_caption=on_caption,
                                   on_explore=on_explore)
            await session.run()
    finally:
        speaker.close()
