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

A finished task does not end the session (owner request 2026-09-10): buddy
says the result and keeps listening. The session ends on end_conversation, after IDLE_TIMEOUT with nothing said
and no task running, or at MAX_SESSION. Every seam (connection, speaker,
mic queue, agent factory, clock) is injectable, so the whole state machine
is unit-tested with fakes; the real wiring lives in `open_session()`.

Eyes, head and standing orders (owner requests 2026-09-10):

* gpt-live-1 takes no images (its model page lists image as unsupported), so
  scene.py describes the camera with a cheap image model. Each timestamped
  `[vision HH:MM:SS]` line reaches the voice as silent context, and the
  backend's `look` tool asks for a fresh view.
* head.py turns the head. The backend picks the pose from the owner's own
  words (`move_head`), pans the room (`look_around`) or searches (`find`).
* intent.py reads every finished user turn. "Go away" in any words closes
  the conversation once buddy's goodbye has been said (a running task keeps
  going with the mic off, and its result is said first). "Mute"/"unmute"
  switch sound off/on; the head and the lights keep moving.
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

from . import head as head_mod
from .caption_pager import CaptionPager, Event, PagerConfig, caption_instructions
from .computer_agent import AgentConfig, AgentEvent, ComputerAgent
from .intent import LEAVE, LOOK, MUTE, UNMUTE, fast_intent

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
FAREWELL_QUIET_SECS = 0.8      # after buddy's goodbye has been said, this much quiet closes the session
FAREWELL_MAX_SECS = 6.0        # a goodbye closes the session this long after it, answered or not
LOOK_ROUTE_DELAY_SECS = 0.6    # a head request waits this long for the voice to delegate it itself
MUTED_NOTE = ("[sound] You are muted: nobody hears you. You still move and light up, and your words still "
              "show on your screen, so keep them short.")
UNMUTED_NOTE = "[sound] Your sound is back on."
SAMPLE_RATE = 24000

# The Live model owns voice, timing and interruptions. Tool workflow lives in
# the backend prompt, per OpenAI's Live delegation guidance.
INSTRUCTIONS = """You are buddy, a small desk robot with a cheerful, curious personality, talking with your owner.
You just heard your wake word. Answer in one or two short spoken sentences; no lists, no markdown, no
offers of things you "can help with" — you are a pet, not an assistant menu.

You can operate your owner's Mac for them. The moment they ask for anything on the computer, delegate it
at once — do not guess an app, do not narrate steps you have not seen. A two-word acknowledgement as you
delegate is fine; say it once.

Starting a task is not finishing it. Until its result arrives, say only that you are on it.
  NOT: "It's playing now."   INSTEAD: "On it."
  NOT: "Done, it's open!"    INSTEAD: "Working on it."
When the result arrives, tell them it in one short line.

Delegate anything that changes what a running task is doing, ends the conversation, or sends you off to
explore. Chit-chat you answer yourself.

You have a camera. A line tagged [vision HH:MM:SS] is what it saw at that time. Asked what you see, answer
from the newest [vision] line in one short sentence, with only what that line says. If the newest line says
the camera is lost or you cannot make out the view, say you can't see right now. For a closer look ("what am
I holding?"), delegate.

You can turn your head. Delegate every request to look somewhere, look around, or find something; say only
a two-word acknowledgement until the result arrives.

When the owner says goodbye or wants you gone, say a two- or three-word goodbye and nothing more: you stop
listening after it. A [sound] line tells you whether you are muted; muted, nobody hears you, but your words
still show on your screen, so keep them short.

Never claim to have done something you did not do, or to see something no [vision] line or tool result
showed you."""

BACKEND_INSTRUCTIONS = """You are the reasoning half of buddy, a small desk robot. You never speak; you
call tools and return one short line for buddy to say.

- A request to do something on the owner's Mac → call start_task at once, with the goal in the owner's own
  words plus any app or site they named. Never guess an app or hedge ("likely in a music app"). When
  start_task returns ok, reply with an empty message: buddy has already acknowledged the request, and the
  result reaches buddy on its own when the task finishes.
- Never ask the owner a clarifying question. Call start_task with their words as they are; the task can
  ask them itself if it truly needs an answer.
- While a task runs: "stop" / "cancel" / "never mind" → stop_task. A correction or addition ("use Safari
  instead", "also save it") → steer_task with the text. "How's it going?" → task_status, summarised in one
  line.
- A message tagged [look request] is the owner's own words asking buddy to turn its head, look somewhere,
  look around or find something. Act on it now with move_head, look_around, find or look — never answer it
  without one of those tools.
- A message tagged [task question] is a question buddy has just asked the owner out loud for the running
  task. When the owner answers, relay it with answer_question as plain words ("yes", "no", "the second one").
- Goodbye, thanks-that's-all, "stop listening", "go to sleep", "I want you to leave", or "never mind" with no
  task running → end_conversation. A running task keeps going after a goodbye; its result is said first.
- "Go explore", "go play", "go wander off" → go_explore. If they tell you to stop exploring or come back, you
  already stopped when you woke — say so and call end_conversation.
- "What do you see?", "what am I holding?", "look at this" → look, then answer from its view in one short
  line with only what the view says. If it says the camera is not connected, or the view is stale, say that.
- Turning the head: you choose the numbers from the owner's words. Angles are the robot's own. yaw 0 faces
  the owner; negative turns to the robot's left, positive to its right; ±120 is as far as the neck turns, so
  "behind you" is 120 (or -120 if they said left). pitch 45 is level, 5 is down at the desk, 85 is up at the
  ceiling. The owner faces the robot, so "my left" is the robot's right. "A bit" is about 15 degrees; plain
  "left"/"right" about 70; "all the way" 120. "More", "a bit further", "back a little" are relative moves
  from where the head is now (relative true). "Look at me" / "look back here" is yaw 0, pitch 45.
  A pose → move_head. "Look around", "what's around you", "check out the room" → look_around, then say what
  you saw in one or two short sentences. "Look at the door", "find my keys", "where's the mug?" → find with
  the thing in the owner's words; say where it is ("on my left") or that you could not see it.
- "Mute", "no sound", "be quiet", "stop beeping" → set_sound with on false. "Unmute", "sound back on" →
  set_sound with on true. Sound never ends the conversation.

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
     "description": "The owner sent you off to explore the room on your own. Ends the conversation; "
                    "you then pan the room and take notes by yourself.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "end_conversation",
     "description": "End the conversation (the owner said goodbye or is done).",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "look",
     "description": "Look through your camera now. Returns a one-sentence view, the time it was seen, and "
                    "whether it is stale; or why nothing can be seen.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "move_head",
     "description": "Turn your head to a pose, or by an offset. Robot-centred degrees: yaw 0 faces the owner, "
                    "negative = your left, positive = your right, limit ±120. pitch 45 level, 5 down at the "
                    "desk, 85 up at the ceiling. Leave out an axis to keep it.",
     "parameters": {"type": "object", "properties": {
         "yaw": {"type": "number", "description": "Degrees; absolute, or an offset when relative is true."},
         "pitch": {"type": "number", "description": "Degrees; absolute, or an offset when relative is true."},
         "relative": {"type": "boolean", "description": "true: add to the current pose ('a bit more left')."},
         "hold_secs": {"type": "number", "description": "How long to hold the pose, 1-60 s. Default 15."}},
         "required": ["relative"], "additionalProperties": False}},
    {"type": "function", "name": "look_around",
     "description": "Pan your head from your far left to your far right, looking at each stop, then face the "
                    "owner again. Returns what you saw in each direction. Takes several seconds.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "find",
     "description": "Search for something with your camera — first where you are looking, then across the "
                    "room — and turn to face it. Takes several seconds.",
     "parameters": {"type": "object", "properties": {"target": {"type": "string",
                    "description": "What to find, in the owner's own words."}},
                    "required": ["target"], "additionalProperties": False}},
    {"type": "function", "name": "set_sound",
     "description": "Mute or unmute yourself. Muted: no beeps, chirps or voice; you still move and light up.",
     "parameters": {"type": "object", "properties": {"on": {"type": "boolean",
                    "description": "false mutes you, true turns sound back on."}},
                    "required": ["on"], "additionalProperties": False}},
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
        scene: Any = None,                                  # scene.SceneWatcher-like: start/stop/look/locate/on_note
        head: Any = None,                                   # head.Head-like: move/yaw/pitch/clock/sleep
        intent: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,   # intent.make_classifier(...)
        on_sound: Optional[Callable[[bool], None]] = None,  # the owner muted (False) / unmuted (True)
        muted: Callable[[], bool] = lambda: False,
    ) -> None:
        self.conn = connection
        self.scene = scene
        self.head = head
        self.intent = intent
        self.on_sound = on_sound
        self.muted = muted
        # "Go away" was heard: the mic stops, and the session closes once buddy's
        # goodbye (or a running task's result) has been said.
        self._farewell = False
        self._farewell_since = float("-inf")
        self._task_done_at = float("-inf")
        self._last_reply_at = float("-inf")
        self._slow_tasks: set[asyncio.Future[None]] = set()
        # Head requests: the voice model often says "Looking." without delegating
        # (log, 2026-09-10 17:43), so the session routes them itself unless the
        # voice delegated that turn or a head tool already ran.
        self._user_turn_started_at = float("-inf")
        self._delegated_at = float("-inf")
        self._last_head_tool_at = float("-inf")
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
        if self.muted():
            await self._quiet(MUTED_NOTE)
        if self.scene is not None:
            self.scene.on_note = self._on_scene_note
            self.scene.start()
        pump = asyncio.create_task(self._pump_mic(), name="voice-mic")
        watchdog = asyncio.create_task(self._watchdog(), name="voice-watchdog")
        ticker = asyncio.create_task(self._tick_loop(), name="voice-ticks")
        cancelled = False
        try:
            await self._until_ended()
        except asyncio.CancelledError:
            cancelled = True                        # hush (a touch on the robot): clear the board at once
            raise
        finally:
            self._ended.set()                       # every exit path: nothing may speak into a closing session
            pump.cancel()
            watchdog.cancel()
            for t in self._slow_tasks:
                t.cancel()
            await asyncio.gather(pump, watchdog, *self._slow_tasks, return_exceptions=True)
            await self._stop_scene()                # the camera stops being looked at the moment we close
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
            if self._farewell:
                continue                            # "go away": buddy has stopped listening
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
            elif self._farewell and self._farewell_due(now):
                log.info("voice: goodbye said — closing")
                self._ended.set()
            elif (not self.task_running and self._pending_answer is None and not self.speaker.busy
                  and not self._pager.busy and not self._slow_tasks
                  and now - self._last_activity > self.config.idle_timeout_secs):
                log.info("voice: nothing said for %.0f s — closing", self.config.idle_timeout_secs)
                self._ended.set()

    async def _until_ended(self) -> None:
        """Serve Live events until the session ends — by an event, or by the watchdog.

        The watchdog's idle timeout and session cap only set `_ended`, and `_events`
        checks it only when the next Live event arrives. When the server went quiet
        the session never closed: all three idle closes in the log hung (+91 s,
        +129 s, +93 min), while the keepalive kept the frozen phase alive on the
        board and the wake word stayed blocked (timer review, 2026-09-10).
        """
        events = asyncio.create_task(self._events(), name="voice-events")
        ended = asyncio.create_task(self._ended.wait(), name="voice-ended")
        try:
            await asyncio.wait({events, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (events, ended):
                t.cancel()
            await asyncio.gather(events, ended, return_exceptions=True)
        if events.done() and not events.cancelled() and events.exception() is not None:
            raise events.exception()  # type: ignore[misc]

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
            if delta and not self._captions and not self.muted():
                self.speaker.play(base64.b64decode(delta))
                self._set("speaking")
        elif t == "session.delegation.created":
            # The Live model handed the turn to the backend: buddy is thinking.
            self._response_active = True
            self._delegated_at = self._clock()
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
            if closed is not None or self._turns.text == text:
                self._user_turn_started_at = now        # the first words of a new turn of the owner's
            self._last_activity = now
            self.speaker.stop()                       # barge-in: the human talks over buddy
            if self._captions and (self._reply_open or self._pager.busy):
                self._emit_events(self._pager.reset(now))    # ... and over the page on screen
                self._reply_open = False
                self._state_after_captions = None
            self._set("listening")
            return
        self._last_reply_at = now                     # buddy's last spoken word (a goodbye waits on it)
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
        if not text:
            return
        if who == "user":
            self._on_user_turn(text)
            return
        if who != "assistant":
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
        self._set_after_captions("working" if self.task_running else "listening")

    # -- tools --
    async def _tool(self, name: str, call_id: str, arguments: str) -> None:
        try:
            args = json.loads(arguments) if arguments else {}
        except ValueError:
            args = {}
        self.tool_calls.append((name, args))
        self._last_activity = self._clock()
        if name in ("move_head", "look_around", "find", "look"):
            self._last_head_tool_at = self._clock()
        result: dict[str, Any]
        if name == "start_task":
            result = self._start_task(str(args.get("goal", "")).strip())
        elif name == "steer_task":
            ok = self.agent is not None and self.agent.steer(str(args.get("text", "")))
            result = {"ok": ok} if ok else {"ok": False, "reason": "no task is running"}
        elif name == "stop_task":
            if self.task_running and self.agent is not None:
                self.agent.cancel(reason="you asked me to stop")
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
        elif name == "move_head":
            result = await self._move_head(args)
        elif name == "set_sound":
            on = args.get("on")
            if isinstance(on, bool):
                self._set_sound(on, "the backend")
                result = {"ok": True, "sound": "on" if on else "off"}
            else:
                result = {"ok": False, "reason": "on must be true or false"}
        elif name in ("look", "look_around", "find"):
            # Seconds of camera and head work: answered from a background task, so
            # Live events (the owner talking, captions) keep flowing meanwhile.
            self._slow_tool(name, call_id, args)
            return
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
        # Started, not done. The voice gets that as silent context, and the face goes to
        # "working" once the reply on screen has been read — it used to wait on the
        # backend's own reply and came up 8-11 s late (task review, 2026-09-10).
        self._bg(self._quiet(f"A computer task has started: {goal}. Nothing is done yet; its result "
                             "arrives on its own when it finishes."))
        self._set_after_captions("working")
        return {"ok": True, "goal": goal}

    # -- eyes and head --
    def _slow_tool(self, name: str, call_id: str, args: dict[str, Any]) -> None:
        async def run() -> None:
            try:
                if name == "look":
                    result = await self._look()
                elif name == "look_around":
                    result = await self._look_around()
                else:
                    result = await self._find(str(args.get("target", "")))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("voice: %s failed", name)
                result = {"ok": False, "reason": f"{name} failed"}
            if self._ended.is_set():
                return
            self._last_activity = self._clock()
            # What the camera saw is never logged: only whether the call worked.
            log.info("voice: %s → ok=%s%s", name, result.get("ok"),
                     f" found={result['found']}" if "found" in result else "")
            await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                       "output": json.dumps(result)})
            if not self._ended.is_set():
                await self._request_response()

        task = asyncio.ensure_future(run())
        self._slow_tasks.add(task)
        task.add_done_callback(self._slow_tasks.discard)

    async def _look(self) -> dict[str, Any]:
        if self.scene is None:
            return {"ok": False, "reason": "vision is not set up on this computer"}
        return await self.scene.look()

    async def _look_around(self) -> dict[str, Any]:
        if self.head is None or self.scene is None:
            return {"ok": False, "reason": "head control or vision is not set up on this computer"}
        return await head_mod.look_around(self.head, self.scene)

    async def _find(self, target: str) -> dict[str, Any]:
        if self.head is None or self.scene is None:
            return {"ok": False, "reason": "head control or vision is not set up on this computer"}
        return await head_mod.find(self.head, self.scene, target)

    async def _move_head(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.head is None:
            return {"ok": False, "reason": "head control is not set up on this computer"}

        def num(key: str) -> Optional[float]:
            v = args.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        hold = num("hold_secs")
        return await self.head.move(num("yaw"), num("pitch"), relative=args.get("relative") is True,
                                    hold_secs=head_mod.DEFAULT_HOLD_SECS if hold is None else hold)

    def _on_scene_note(self, note: str) -> None:
        """scene.py saw something (or lost the camera): silent context for the voice."""
        if not self._ended.is_set():
            self._bg(self._quiet(note))

    async def _stop_scene(self) -> None:
        if self.scene is not None:
            self.scene.on_note = None
            await self.scene.stop()

    # -- standing orders: go away, mute --
    def _on_user_turn(self, text: str) -> None:
        if self._ended.is_set() or self._farewell:
            return
        # The owner's turn ends when buddy starts answering it (or on a silence gap),
        # so this instant is also where buddy's reply to it begins.
        said_at = self._clock()
        label = fast_intent(text)
        if label is not None:
            self._apply_intent(label, text, "phrase", said_at)
        elif self.intent is not None:
            self._bg(self._classify(text, said_at))

    async def _classify(self, text: str, said_at: float) -> None:
        assert self.intent is not None
        label = await self.intent(text)
        if label is not None and not self._ended.is_set():
            self._apply_intent(label, text, "classifier", said_at)

    def _apply_intent(self, label: str, text: str, how: str, said_at: float) -> None:
        log.info("voice: %r means %s (%s)", text[:80], label, how)
        if label == LEAVE:
            self._begin_farewell(said_at)
        elif label == MUTE:
            self._set_sound(False, how)
        elif label == UNMUTE:
            self._set_sound(True, how)
        elif label == LOOK:
            self._bg(self._route_look(text, self._user_turn_started_at))

    async def _route_look(self, text: str, turn_started_at: float) -> None:
        """Hand a head request to the backend unless the voice already delegated this
        turn: a relative move ("a bit more left") must never run twice."""
        await asyncio.sleep(LOOK_ROUTE_DELAY_SECS)
        if self._ended.is_set():
            return
        if self._delegated_at >= turn_started_at or self._last_head_tool_at >= turn_started_at:
            log.info("voice: head request already delegated by the voice — not routing it again")
            return
        log.info("voice: routing a head request the voice did not delegate")
        await self._backend_note(f"[look request] {text}")
        await self._request_response()

    def _begin_farewell(self, said_at: float) -> None:
        """Stamped with when the owner finished saying it, not when a classifier
        answered: buddy's goodbye may already be on its way by then."""
        if self._farewell:
            return
        self._farewell = True
        self._farewell_since = said_at
        if self.task_running:
            log.info("voice: the owner said goodbye — mic off; the task finishes and its result is said first")
        else:
            log.info("voice: the owner said goodbye — closing after buddy's goodbye")
        self._bg(self._quiet("[leaving] The owner is done talking. Say a two- or three-word goodbye, nothing else."))

    def _farewell_due(self, now: float) -> bool:
        """The goodbye has been said back (or never will be) and nothing is still being said."""
        if self.task_running or self._pending_answer is not None:
            return False
        start = max(self._farewell_since, self._task_done_at)
        if now - start >= FAREWELL_MAX_SECS:
            return True
        said_back = self._last_reply_at >= start      # the reply that closed the goodbye turn counts
        open_reply = self._turns.speaker == "assistant" and bool(self._turns.text)
        return (said_back and not open_reply and not self.speaker.busy
                and now - self._last_reply_at >= FAREWELL_QUIET_SECS)

    def _set_sound(self, on: bool, how: str) -> None:
        was_muted = self.muted()
        if self.on_sound is not None:
            try:
                self.on_sound(on)
            except Exception:  # noqa: BLE001
                log.exception("voice: on_sound failed")
        if not on:
            self.speaker.stop()                     # audio mode: drop what is queued at once
        if was_muted == (not on):
            return                                  # no change: nothing new to tell the voice
        log.info("voice: sound %s (%s)", "on" if on else "off", how)
        if not self._ended.is_set():
            self._bg(self._quiet(UNMUTED_NOTE if on else MUTED_NOTE))

    def _bg(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        task.add_done_callback(lambda t: t.cancelled() or t.exception() is None
                               or log.warning("voice: background send failed: %s", t.exception()))

    async def _run_agent(self, goal: str) -> str:
        assert self.agent is not None
        final = await self.agent.run(goal)
        self._last_activity = self._task_done_at = self._clock()
        if not self._ended.is_set():
            # The conversation stays open after the result: the owner ends it with a
            # goodbye, or the idle timeout does (owner request 2026-09-10, reversing
            # the 2026-09-06 close-after-task).
            log.info("voice: task result to the voice: %s", final[:160])
            await self._speak(f"The computer task you started has finished. Tell the owner this result in one "
                              f"short line. Do not say goodbye: keep listening until they end the conversation. "
                              f"The result: {final}")
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
        elif ev.kind == "error":
            self._set("error")
        elif ev.kind == "cancelled":
            self._set("listening")                  # a stopped task is not a finished one: no done nod

    async def _ask_user(self, question: str) -> str:
        if self._farewell:
            return "no (the owner has ended the conversation and is not listening)"
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

    async def _quiet(self, content: str) -> None:
        """Silent context for the voice (`session.thinking.append`): it can shape later
        speech but asks for none."""
        await self.conn.session.thinking.append(content=content, delegation_id=None)

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
    scene: Any = None,
    head: Any = None,
    intent: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
    on_sound: Optional[Callable[[bool], None]] = None,
    muted: Callable[[], bool] = lambda: False,
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
                                   on_explore=on_explore, scene=scene, head=head, intent=intent,
                                   on_sound=on_sound, muted=muted)
            await session.run()
    finally:
        speaker.close()
