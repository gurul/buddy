"""Voice: after "hey buddy", a spoken conversation that can run the computer.

One `VoiceSession` is one conversation. It opens a Realtime API session
(gpt-realtime-2.1-mini, speech in / speech out, semantic turn detection),
pipes the daemon's microphone into it (ears.py `subscribe()`), plays the
replies through the Mac speaker, and gives the model five tools:

    start_task(goal)      run a gpt-6-astra computer-use task (computer_agent.py)
    steer_task(text)      change what the running task is doing, mid-task
    stop_task()           cancel it
    task_status()         what the task is up to
    answer_question(text) relay the human's answer to the task's ask_user
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

Output: by default buddy does NOT speak through the Mac — the model answers
in text, every reply streams to the robot as a caption on its screen
({"cmd":"caption"}) and the robot babbles beep-boops while the caption grows
(owner request 2026-09-06). CC_BUDDY_VOICE_OUTPUT=audio brings the spoken
voice back through the Mac speaker.

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

from .computer_agent import AgentConfig, AgentEvent, ComputerAgent

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-realtime-2.1-mini"
DEFAULT_VOICE = "marin"
DEFAULT_IDLE_TIMEOUT_SECS = 20.0
DEFAULT_MAX_SESSION_SECS = 600.0
SPEAKER_TAIL_SECS = 0.4        # mic stays muted this long after the speaker drains
SAMPLE_RATE = 24000

INSTRUCTIONS = """You are buddy, a small desk robot with a cheerful, curious personality, talking with your owner.
You just heard your wake word. Answer in one or two short spoken sentences; no lists, no markdown, no
offers of things you "can help with" — you are a pet, not an assistant menu. Wait for the owner to talk.

You can operate your owner's Mac for them:
- When they ask you to do something on the computer, call start_task with a precise goal in your own
  words, then say one short line like "On it." Do not narrate steps you have not seen.
- While a task runs, keep listening. "stop" / "cancel" / "never mind" → call stop_task at once.
  Corrections or additions ("use Safari instead", "also save it") → call steer_task with the text.
  "How's it going?" → call task_status and summarise in one line.
- When a message tagged [task finished] arrives, tell the owner the result in one short line and say a
  two-word goodbye; the conversation ends right after.
- When a message tagged [task question] arrives, ask the owner that exact question out loud, wait for
  their answer, then call answer_question with their answer as plain words ("yes", "no", "the second one").
- When the owner says goodbye, thanks, or "that's all", call end_conversation after a two-word farewell.
Never claim to have done something you did not do."""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "start_task",
     "description": "Start a computer-use task on the owner's Mac. Returns immediately; the task runs in the background.",
     "parameters": {"type": "object", "properties": {"goal": {"type": "string",
                    "description": "What to accomplish, precisely, in one or two sentences."}},
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
    {"type": "function", "name": "end_conversation",
     "description": "End the conversation (the owner said goodbye or is done).",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
]


CAPTION_MAX_CHARS = 240            # what fits the robot's caption band (3 lines) with a tail
CAPTION_THROTTLE_SECS = 0.15


@dataclass(frozen=True)
class VoiceConfig:
    model: str = DEFAULT_MODEL
    voice: str = DEFAULT_VOICE
    idle_timeout_secs: float = DEFAULT_IDLE_TIMEOUT_SECS
    max_session_secs: float = DEFAULT_MAX_SESSION_SECS
    output: str = "captions"      # "captions" (text → robot screen + beeps) or "audio" (Mac speaker)


def configured(environ: Any = None) -> VoiceConfig:
    env = os.environ if environ is None else environ
    model = (env.get("CC_BUDDY_REALTIME_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
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
    return VoiceConfig(model=model, voice=voice, idle_timeout_secs=idle, output=out)


CAPTION_INSTRUCTIONS = """
Your replies are not spoken: they are shown as a caption on your own small screen (three lines of 26
characters) while you beep. Keep every reply under 110 characters, plain words, no emoji."""


def session_config(config: VoiceConfig) -> dict[str, Any]:
    """The session.update payload (GA Realtime shape, openai 3.8)."""
    captions = config.output == "captions"
    audio: dict[str, Any] = {
        "input": {
            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
            "turn_detection": {"type": "semantic_vad", "eagerness": "medium",
                               "create_response": True, "interrupt_response": True},
        },
    }
    if not captions:
        audio["output"] = {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": config.voice}
    return {
        "type": "realtime",
        "model": config.model,
        "instructions": INSTRUCTIONS + (CAPTION_INSTRUCTIONS if captions else ""),
        "output_modalities": ["text"] if captions else ["audio"],
        "audio": audio,
        "tools": TOOLS,
        "tool_choice": "auto",
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


# ---- the session ---------------------------------------------------------------------------

class VoiceSession:
    """One conversation. See the module docstring."""

    def __init__(
        self,
        connection: Any,                                   # realtime connection (async ctx manager already entered)
        mic: "asyncio.Queue[bytes]",                        # from Ears.subscribe()
        speaker: Any,                                       # Speaker-like: play/stop/busy
        agent_factory: Callable[[Callable[[AgentEvent], None], Callable[[str], Awaitable[str]]], ComputerAgent],
        on_state: Callable[[str], None],
        config: Optional[VoiceConfig] = None,
        agent_config: Optional[AgentConfig] = None,
        clock: Callable[[], float] = time.monotonic,
        agent_enabled: bool = True,
        on_caption: Optional[Callable[[str, bool], None]] = None,
    ) -> None:
        self.conn = connection
        self.on_caption = on_caption
        self._caption = ""
        self._caption_sent_at = float("-inf")
        self.mic = mic
        self.speaker = speaker
        self.agent_factory = agent_factory
        self.on_state = on_state
        self.config = config or VoiceConfig()
        self.agent_config = agent_config or AgentConfig()
        self._clock = clock
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
        # The Realtime API allows one response at a time. A function_call_arguments.done
        # event arrives BEFORE that response's response.done, so a response.create sent
        # right after a tool result fails with "already has an active response". Track
        # the in-flight response and defer our creates until it is done.
        self._response_active = False
        self._response_wanted = False
        self._speaking_until = 0.0
        # After a task finishes, buddy says the result and the conversation
        # closes — it must not sit there listening (owner request 2026-09-06).
        # Set when the [task finished] message goes in; acted on when the
        # response that speaks it is done.
        self._end_after_response = False

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
        await self.conn.session.update(session=session_config(self.config))
        await self.conn.response.create(response={"instructions": "Say a one- or two-word greeting, like 'Yeah?'"})
        self._response_active = True
        pump = asyncio.create_task(self._pump_mic(), name="voice-mic")
        watchdog = asyncio.create_task(self._watchdog(), name="voice-watchdog")
        try:
            await self._events()
        finally:
            pump.cancel()
            watchdog.cancel()
            await asyncio.gather(pump, watchdog, return_exceptions=True)
            if self.task_running and self.agent is not None:
                self.agent.cancel()
            if self._agent_task is not None:
                await asyncio.gather(self._agent_task, return_exceptions=True)
            await self._drain_speaker()
            self.speaker.stop()
            self._set("idle")

    def end(self) -> None:
        self._ended.set()

    async def _drain_speaker(self, timeout: float = 15.0) -> None:
        """Let a queued reply finish playing (barge-in and errors skip this)."""
        deadline = self._clock() + timeout
        while self.speaker.busy and self._clock() < deadline:
            await asyncio.sleep(0.05)

    async def _pump_mic(self) -> None:
        # Half-duplex: the Mac mic hears the Mac speaker, and with no echo
        # cancellation buddy would answer its own greeting in a loop (bench
        # 2026-09-06 09:01). While a reply is playing (plus a short tail) the
        # mic is not forwarded. Cost: no barge-in mid-sentence; say it after.
        while not self._ended.is_set():
            raw = await self.mic.get()
            if self.speaker.busy:
                self._speaking_until = self._clock() + SPEAKER_TAIL_SECS
                continue
            if self._clock() < self._speaking_until:
                continue
            await self.conn.input_audio_buffer.append(audio=base64.b64encode(raw).decode("ascii"))

    async def _watchdog(self) -> None:
        while not self._ended.is_set():
            await asyncio.sleep(0.5)
            now = self._clock()
            if now - self._started_at > self.config.max_session_secs:
                log.info("voice: session hit its %.0f s cap", self.config.max_session_secs)
                self._ended.set()
            elif (not self.task_running and self._pending_answer is None and not self.speaker.busy
                  and now - self._last_activity > self.config.idle_timeout_secs):
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
        t = getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else None)
        if t == "input_audio_buffer.speech_started":
            self._last_activity = self._clock()
            self.speaker.stop()                     # barge-in: the human talks over buddy
            self._set("listening")
        elif t == "response.created":
            self._response_active = True
            self._set("thinking")
        elif t in ("response.output_audio.delta", "response.audio.delta"):
            delta = _attr(event, "delta")
            if delta:
                self.speaker.play(base64.b64decode(delta))
                self._set("speaking")
        elif t == "response.output_text.delta":
            delta = _attr(event, "delta")
            if delta:
                self._caption += delta
                self._set("speaking")
                self._emit_caption(final=False)
        elif t == "response.output_text.done":
            text = _attr(event, "text")
            if text:
                self._caption = text
                self.transcript.append(text)
                log.info("buddy: %s", text)
            self._emit_caption(final=True)
            self._caption = ""
        elif t in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            text = _attr(event, "transcript")
            if text:
                self.transcript.append(text)
                log.info("buddy: %s", text)
        elif t == "response.function_call_arguments.done":
            await self._tool(_attr(event, "name") or "", _attr(event, "call_id") or "", _attr(event, "arguments") or "{}")
        elif t == "response.done":
            self._response_active = False
            self._last_activity = self._clock()
            if self._end_after_response and not self._response_wanted:
                # the result has been spoken (the audio may still be draining;
                # the speaker is drained by the caller before "idle" lands)
                self._end_after_response = False
                self._ended.set()
                return
            self._set("working" if self.task_running else "listening")
            if self._response_wanted and not self._ended.is_set():
                self._response_wanted = False
                await self.conn.response.create()
        elif t == "error":
            err = _attr(event, "error")
            log.warning("voice: realtime error: %s", err)

    def _emit_caption(self, final: bool) -> None:
        """Throttled: the robot redraws on every line, ~7 a second is plenty."""
        if self.on_caption is None:
            return
        now = self._clock()
        if not final and now - self._caption_sent_at < CAPTION_THROTTLE_SECS:
            return
        self._caption_sent_at = now
        try:
            self.on_caption(self._caption[-CAPTION_MAX_CHARS:], final)
        except Exception:  # noqa: BLE001
            log.exception("voice: on_caption failed")

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
                self.agent.cancel()
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
        else:
            result = {"ok": False, "reason": f"unknown tool {name}"}
        await self.conn.conversation.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                       "output": json.dumps(result)})
        if name != "end_conversation":
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
            await self._say_from_task(f"[task finished] {final}")
        return final

    def _on_agent_event(self, ev: AgentEvent) -> None:
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
        await self._say_from_task(f"[task question] {question}")
        try:
            return await asyncio.wait_for(self._pending_answer, timeout=60.0)
        except asyncio.TimeoutError:
            return "no (no answer within a minute)"
        finally:
            self._pending_answer = None

    async def _say_from_task(self, text: str) -> None:
        await self.conn.conversation.item.create(item={"type": "message", "role": "user",
                                                       "content": [{"type": "input_text", "text": text}]})
        await self._request_response()

    async def _request_response(self) -> None:
        """response.create now, or as soon as the in-flight response is done."""
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
    on_caption: Optional[Callable[[str, bool], None]] = None,
) -> None:
    """Run one full conversation on the real Realtime API — captions to the robot,
    or the real speaker in audio mode."""
    from openai import AsyncOpenAI

    cfg = config or configured()
    client = AsyncOpenAI(api_key=api_key) if api_key else AsyncOpenAI()
    speaker: Any = NullSpeaker() if cfg.output == "captions" else Speaker()
    speaker.start()
    try:
        async with client.realtime.connect(model=cfg.model) as conn:
            session = VoiceSession(conn, mic, speaker, agent_factory, on_state, config=cfg,
                                   agent_enabled=agent_enabled, on_caption=on_caption)
            await session.run()
    finally:
        speaker.close()
