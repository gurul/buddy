"""Hold to talk on the board: the Telegram Mini App's push-to-talk call, on the desk.

The owner asked on 2026-09-26 for the Voice PE's button to work "like telegram": hold it, talk, let go, and
buddy answers. That is phone_call.Call — each press is one message to the same brain the owner texts, every
reply read back — with the phone swapped for the desk:

* **The button** (``{"cmd":"ptt","on":bool}`` from the board) is the phone's ``talk`` / ``done``.
* **The Mac microphone** (the same device the wake word uses, ``CC_BUDDY_MIC``) is the phone's audio. It is
  opened only while the button is held.
* **The Mac speaker** plays buddy's reply, where the phone played it.
* **The board's ring** shows the call's state through ``{"cmd":"agent","state":..}``: listening, thinking,
  speaking.

``DeskLink`` is the stand-in for phone_call.WebSocket: the only change to the call itself is where its bytes
come from and go to. A call starts on the first press and ends after ``phone_call.QUIET_SECS`` without one,
like a phone that went quiet; the next press starts a new one. One call at a time across phone and desk: the
chat has a single listener.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import threading
from typing import Any, Awaitable, Callable, Optional

from .phone_call import OP_BIN, OP_TEXT, SAMPLE_RATE, Brain, Call, Closed, Voice

log = logging.getLogger(__name__)

BLOCK_SECS = 0.05          # mic block handed to the call: 50 ms, 2400 samples


class Mic:
    """The Mac microphone while the button is held: 24 kHz mono int16 blocks to ``on_block`` (any thread)."""

    def __init__(self, device: Optional[str], on_block: Callable[[bytes], None]) -> None:
        self.device, self.on_block = device, on_block
        self._stream: Any = None

    def open(self) -> bool:
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd

            from .ears import _find_input_device

            dev = _find_input_device(sd, self.device) if self.device else None

            def callback(indata: Any, _frames: int, _time: Any, _status: Any) -> None:
                self.on_block(bytes(indata))

            self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                             blocksize=int(SAMPLE_RATE * BLOCK_SECS), device=dev,
                                             callback=callback)
            self._stream.start()
            return True
        except Exception as e:  # noqa: BLE001 — no mic: the press is empty and the call says so
            log.warning("desk call: could not open the microphone (%s)", e)
            self._stream = None
            return False

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()


class Speaker:
    """buddy's reply on the Mac speaker. Writes block on a player thread; ``flush`` drops what is queued."""

    def __init__(self) -> None:
        self._q: queue.Queue[Optional[bytes]] = queue.Queue()
        self._gen = 0
        self._thread: Optional[threading.Thread] = None

    def play(self, pcm: bytes) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="desk-call-speaker", daemon=True)
            self._thread.start()
        self._q.put((self._gen, pcm))  # type: ignore[arg-type]

    def flush(self) -> None:
        self._gen += 1
        with contextlib.suppress(queue.Empty):
            while True:
                self._q.get_nowait()

    def close(self) -> None:
        self.flush()
        self._q.put(None)

    def _run(self) -> None:
        try:
            import sounddevice as sd

            out = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            out.start()
        except Exception as e:  # noqa: BLE001
            log.warning("desk call: could not open the speaker (%s)", e)
            return
        try:
            while (item := self._q.get()) is not None:
                gen, pcm = item  # type: ignore[misc]
                if gen == self._gen:
                    out.write(pcm)
        finally:
            with contextlib.suppress(Exception):
                out.stop()
                out.close()


Send = Callable[[dict[str, Any]], Awaitable[Any]]

# The call's states on the ring (firmware agent states). The call's "listening" means waiting for the next
# press, so the ring goes quiet; it turns blue only while the button is held (DeskCalls._down).
RING = {"thinking": "thinking", "speaking": "speaking"}
PING_SECS = 10.0           # the phone pings every 10 s; the desk pings while buddy is still working on a reply


class DeskLink:
    """What phone_call.Call needs from its WebSocket, served by the board, the Mac mic and the Mac speaker."""

    def __init__(self, send_board: Send, speaker: Any) -> None:
        self.send_board, self.speaker = send_board, speaker
        self.closed = False
        self.inbox: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self.ring = "idle"

    # ---- the call's side ----
    async def recv(self) -> tuple[int, bytes]:
        if self.closed:
            raise Closed()
        op, data = await self.inbox.get()
        if op < 0:
            raise Closed()
        return op, data

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise Closed()
        self.speaker.play(data)

    async def send_json(self, obj: dict[str, Any]) -> None:
        if self.closed:
            raise Closed()
        kind = obj.get("type")
        if kind == "flush":
            self.speaker.flush()
        elif kind == "state":
            await self.set_ring(RING.get(str(obj.get("state")), "idle"))
            if obj.get("note"):
                log.info("desk call: %s", obj["note"])
        elif kind == "heard":
            log.info("desk call: heard %d chars", len(str(obj.get("text") or "")))

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            self.closed = True
            self.inbox.put_nowait((-1, b""))

    # ---- the desk's side ----
    def talk(self) -> None:
        self.inbox.put_nowait((OP_TEXT, json.dumps({"type": "talk"}).encode()))

    def done(self) -> None:
        self.inbox.put_nowait((OP_TEXT, json.dumps({"type": "done"}).encode()))

    def ping(self) -> None:
        self.inbox.put_nowait((OP_TEXT, json.dumps({"type": "ping"}).encode()))

    def audio(self, pcm: bytes) -> None:
        self.inbox.put_nowait((OP_BIN, pcm))

    async def set_ring(self, state: str) -> None:
        if state != self.ring:
            self.ring = state
            with contextlib.suppress(Exception):
                await self.send_board({"cmd": "agent", "state": state})


class DeskCalls:
    """Presses from the board in, one Call at a time. ``busy`` says whether the chat already has a listener
    (a phone call), so the desk never takes it from the phone."""

    def __init__(self, brain: Optional[Brain], voice: Optional[Voice], send_board: Send,
                 mic_device: Optional[str] = None, busy: Callable[[], bool] = lambda: False,
                 mic_factory: Callable[..., Any] = Mic, speaker_factory: Callable[[], Any] = Speaker) -> None:
        self.brain, self.voice, self.send_board = brain, voice, send_board
        self.mic_device, self.busy = mic_device, busy
        self.mic_factory, self.speaker_factory = mic_factory, speaker_factory
        self.link: Optional[DeskLink] = None
        self.task: Optional[asyncio.Task[Any]] = None
        self.mic: Any = None
        self.held = False

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    async def press(self, on: bool) -> None:
        if on == self.held:
            return
        self.held = on
        if on:
            await self._down()
        else:
            self._up()

    async def _down(self) -> None:
        if self.brain is None or self.voice is None:
            log.warning("desk call: hold to talk needs the Telegram chat and an OpenAI key; not set up")
            return
        if not self.active:
            if self.busy():
                log.info("desk call: the phone is on a call; the button waits")
                return
            await self._start()
        assert self.link is not None
        loop = asyncio.get_running_loop()
        link = self.link

        def block(pcm: bytes) -> None:
            loop.call_soon_threadsafe(link.audio, pcm)

        link.talk()
        await link.set_ring("listening")
        self.mic = self.mic_factory(self.mic_device, block)
        self.mic.open()

    def _up(self) -> None:
        if self.mic is not None:
            self.mic.close()
            self.mic = None
        if self.link is not None and self.active:
            self.link.done()

    async def _start(self) -> None:
        speaker = self.speaker_factory()
        link = self.link = DeskLink(self.send_board, speaker)
        call = Call(link, self.brain, self.voice)  # type: ignore[arg-type]

        async def keepalive() -> None:
            # Call.run ends a call after QUIET_SECS without a message. A reply that takes longer (a tool, a
            # long read-out) must not be cut off, so ping while buddy thinks or speaks; once it goes quiet the
            # call ends QUIET_SECS later, like a phone put down.
            while not link.closed:
                await asyncio.sleep(PING_SECS)
                if link.ring in ("thinking", "speaking") or self.held:
                    link.ping()

        async def run() -> None:
            log.info("desk call: started from the board's button")
            reason = "?"
            pinger = asyncio.create_task(keepalive())
            try:
                reason = await call.run()
            finally:
                pinger.cancel()
                if self.mic is not None:
                    self.mic.close()
                    self.mic = None
                self.held = False
                speaker.close()
                await link.set_ring("idle")
                await link.close()
                log.info("desk call: ended (%s), %d press(es)", reason, call.presses)

        self.task = asyncio.create_task(run(), name="desk-call")

    async def stop(self) -> None:
        if self.link is not None:
            await self.link.close()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
