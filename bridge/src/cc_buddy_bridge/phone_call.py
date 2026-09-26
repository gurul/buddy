"""Calling buddy from the phone: push to talk in the Telegram Mini App, with everything texting can do.

The owner asked on 2026-09-25 for voice calls with buddy over Telegram, then for push to talk "instead of live",
and for "full functionality like i am texting it". A Telegram bot cannot take a Telegram call, so the call lives in
buddy's Mini App (miniapp.py): **Call buddy** opens the microphone in Telegram's in-app browser, and a WebSocket
carries it to the daemon through the same Cloudflare tunnel the Mini App already uses.

* **It is the chat, spoken.** Each press of the button is one message to the same brain the owner texts
  (telegram.py): the audio is transcribed and handed to the chat exactly as if it had been typed, so every tool
  the chat has works on a call — mail, calendar, Drive, watch, Meet, notes, tasks on the Mac, app building,
  memory, the relays. Everything buddy then says in the chat is also spoken on the call, and the chat keeps the
  whole call in writing: "🎙 what you said", then buddy's replies.
* **Push to talk.** The phone sends audio only while the button is held (``talk``, the audio, ``done``). A press
  while buddy is speaking interrupts it: the phone drops what it was playing and buddy stops reading.
* **The wire.** ``GET /api/call`` upgrades to a WebSocket (RFC 6455, written here: the Mini App server is a small
  hand-written HTTP server). Audio is 24 kHz mono 16-bit PCM both ways, in binary frames. Text frames carry JSON:
  the phone's first message is its Telegram ``initData``; then ``talk``, ``done`` and ``end``; buddy sends
  ``state``, ``heard`` (what it transcribed), ``flush`` (drop the queued audio) and ``ended``.
* **Only the owner.** Nothing starts until the first message carries initData signed by this bot for an owner
  (miniapp.check_init_data, the check every Mini App call makes), within ``AUTH_SECS``. The upgrade is refused
  from any origin but the Mini App's own. One call at a time.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import struct
import time
import wave
from typing import Any, Awaitable, Callable, Iterator, Optional, Protocol

from . import spend

log = logging.getLogger(__name__)

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SAMPLE_RATE = 24000                     # what the phone sends and OpenAI's "pcm" speech format is
BYTES_PER_SEC = SAMPLE_RATE * 2
AUTH_SECS = 10.0                        # the first message (initData) must come this soon
MAX_FRAME_BYTES = 256 * 1024            # a 100 ms block is 4800 bytes; anything this big is not audio
MAX_PRESS_SECS = 120.0                  # one press holds at most this much audio; the rest is dropped
MIN_PRESS_SECS = 0.35                   # shorter than this is a tap, not words
MAX_CALL_SECS = 60 * 60                 # a call this long is ended
QUIET_SECS = 30.0                       # nothing from the phone this long (it sends a ping every 10 s): it is gone
MAX_SPOKEN_CHARS = 1500                 # a reply longer than this is read in part; the chat has all of it
DEFAULT_STT_MODEL = "gpt-4o-mini-transcribe"   # the room notes' model (notes.py)
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"   # the fallback voice, on OpenAI
DEFAULT_TTS_VOICE = "marin"             # the desk voice's (voice_agent.DEFAULT_VOICE)
# The call's voice, on OpenRouter (owner, 2026-09-25: "switch to these ... for telegram call"). Measured from this
# Mac the same day, median of three, one two-sentence reply: deepgram/aura-2 first sound 0.31 s, its free flux-tts
# 0.42 s, OpenAI gpt-4o-mini-tts streamed 1.15 s, google gemini-3.8-flash-lite-tts 1.92 s, hexgrad/kokoro-82m 7-10 s.
# The ears stay on OpenAI: its live session gave text 0.39-0.50 s after release whatever the press's length, and no
# OpenRouter transcription model beat that (deepgram/nova-3 0.69 s, whisper-large-v3-turbo 1.32 s, uploaded).
OPENROUTER_SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
DEFAULT_OR_TTS_MODEL = "deepgram/aura-2"
DEFAULT_OR_TTS_VOICE = "aura-2-thalia-en"
OR_TTS_USD_PER_CHAR = 0.00003           # deepgram/aura-2's listed price on OpenRouter (/api/v1/models, 2026-09-25)

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

class Closed(Exception):
    """The other side closed the WebSocket, or it broke."""


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")


def is_upgrade(headers: dict[str, str]) -> bool:
    return ("websocket" in headers.get("upgrade", "").lower()
            and "upgrade" in headers.get("connection", "").lower()
            and bool(headers.get("sec-websocket-key")))


def frame(opcode: int, payload: bytes) -> bytes:
    """One unmasked, final frame (a server never masks)."""
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


class WebSocket:
    """The server side of one WebSocket, over an asyncio stream the HTTP server already read the request from."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self._send_lock = asyncio.Lock()
        self.closed = False

    @classmethod
    async def accept(cls, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     headers: dict[str, str]) -> "WebSocket":
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept_key(headers['sec-websocket-key'])}\r\n\r\n").encode("ascii"))
        await writer.drain()
        return cls(reader, writer)

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            b1, b2 = await self.reader.readexactly(2)
            n = b2 & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", await self.reader.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", await self.reader.readexactly(8))
            if n > MAX_FRAME_BYTES:
                raise Closed("frame too large")
            if not b2 & 0x80:
                raise Closed("a client frame must be masked")
            mask = await self.reader.readexactly(4)
            data = await self.reader.readexactly(n)
        except (asyncio.IncompleteReadError, ConnectionError) as e:
            raise Closed(type(e).__name__) from None
        # unmask the whole payload at once: XOR with the 4-byte mask repeated to its length
        if not n:
            return bool(b1 & 0x80), b1 & 0x0F, b""
        key = (mask * (n // 4 + 1))[:n]
        payload = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")
        return bool(b1 & 0x80), b1 & 0x0F, payload

    async def recv(self) -> tuple[int, bytes]:
        """The next text or binary message (fragments joined). Pings are answered; a close raises Closed."""
        parts: list[bytes] = []
        first = 0
        while True:
            fin, op, payload = await self._read_frame()
            if op == OP_PING:
                await self._send(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                with contextlib.suppress(Exception):
                    await self._send(OP_CLOSE, payload[:2])
                self.closed = True
                raise Closed("closed by the phone")
            if op in (OP_TEXT, OP_BIN):
                first, parts = op, [payload]
            elif op == OP_CONT and parts:
                parts.append(payload)
            else:
                raise Closed("bad frame")
            if sum(len(p) for p in parts) > MAX_FRAME_BYTES:
                raise Closed("message too large")
            if fin:
                return first, b"".join(parts)

    async def _send(self, opcode: int, payload: bytes) -> None:
        if self.closed and opcode != OP_CLOSE:
            raise Closed("already closed")
        async with self._send_lock:
            try:
                self.writer.write(frame(opcode, payload))
                await self.writer.drain()
            except (ConnectionError, RuntimeError) as e:
                self.closed = True
                raise Closed(type(e).__name__) from None

    async def send_bytes(self, data: bytes) -> None:
        await self._send(OP_BIN, data)

    async def send_json(self, obj: dict[str, Any]) -> None:
        await self._send(OP_TEXT, json.dumps(obj).encode("utf-8"))

    async def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        with contextlib.suppress(Exception):
            await self._send(OP_CLOSE, struct.pack("!H", code))
        self.closed = True



# ---- the speech on both ends ---------------------------------------------------------------------

def to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


_MARKS = re.compile(r"[*_`#>|~]+|\[([^\]]*)\]\([^)]*\)")


def speakable(text: str) -> str:
    """A chat message as words to read out: no Markdown marks, a link read as its words, a bare URL as "a link",
    and a long message cut at a sentence with a pointer to the chat."""
    t = _MARKS.sub(lambda m: m.group(1) or " ", text or "")
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = " ".join(t.split())
    if len(t) > MAX_SPOKEN_CHARS:
        cut = t.rfind(". ", 0, MAX_SPOKEN_CHARS)
        t = t[: cut + 1 if cut > 200 else MAX_SPOKEN_CHARS] + " The rest is in the chat."
    return t


def sentences(text: str, limit: int = 220) -> list[str]:
    """Read in sentence-sized pieces, so the first one plays while the next is still being made."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if out and len(out[-1]) + len(p) < 90:
            out[-1] += " " + p                      # short sentences ride together: fewer, fuller requests
        else:
            out.append(p[:limit * 4])
    return out


class Voice(Protocol):
    def transcribe(self, pcm: bytes) -> str: ...
    def speak_stream(self, text: str) -> Iterator[bytes]: ...
    def live_ears(self) -> Optional["LiveEars"]: ...


class LiveEars:
    """Transcription while the owner is still talking (owner, 2026-09-25: "2 is quite important").

    One OpenAI realtime transcription session per call, opened when the call starts (connecting costs about
    0.8 s, measured live), and reused for every press. The press's audio is appended as it arrives; the server
    cuts it at pauses (server VAD) and transcribes each finished piece while the owner keeps talking, so on
    release only the last piece is left: commit it, wait for every piece's text, join them in order. Measured
    live, 2026-09-25: commit to text 1.0 s on an open session, against 1.5 s uploading the press after release.
    Any failure is the caller's cue to fall back to uploading the press (``Voice.transcribe``)."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key, self.model = api_key, model
        self.conn: Any = None
        self._manager: Any = None
        self._reader: Optional[asyncio.Task[Any]] = None
        self.order: list[str] = []
        self.done: dict[str, str] = {}
        self.changed = asyncio.Event()
        self.commit_at = 0
        self.commit_resolved = True
        self.broken = False
        self.fed = 0

    async def open(self) -> None:
        from openai import AsyncOpenAI

        self._manager = AsyncOpenAI(api_key=self.api_key).realtime.connect(extra_query={"intent": "transcription"})
        self.conn = await self._manager.__aenter__()
        await self.conn.session.update(session={"type": "transcription", "audio": {"input": {
            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
            "transcription": {"model": self.model},
            "turn_detection": {"type": "server_vad", "silence_duration_ms": 400, "prefix_padding_ms": 200}}}})
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        try:
            async for ev in self.conn:
                kind = getattr(ev, "type", "")
                if kind == "input_audio_buffer.committed":
                    self.order.append(ev.item_id)
                    self.commit_resolved = True
                elif kind == "conversation.item.input_audio_transcription.completed":
                    self.done[ev.item_id] = (ev.transcript or "").strip()
                elif kind == "conversation.item.input_audio_transcription.failed":
                    self.done[getattr(ev, "item_id", "")] = ""
                elif kind == "error":
                    code = str(getattr(getattr(ev, "error", None), "code", "") or "")
                    if "empty" in code or "buffer_too_small" in code:
                        self.commit_resolved = True    # the server had committed it all already
                    else:
                        log.warning("call: live transcription error %s", code or "?")
                self.changed.set()
        except Exception as e:  # noqa: BLE001
            log.info("call: the live transcription session closed (%s)", type(e).__name__)
        self.broken = True
        self.changed.set()

    async def begin(self) -> None:
        """A new press: nothing left over from the last one."""
        self.order, self.done, self.fed = [], {}, 0
        self.commit_resolved = True
        await self.conn.input_audio_buffer.clear()

    async def feed(self, pcm: bytes) -> None:
        self.fed += len(pcm)
        await self.conn.input_audio_buffer.append(audio=base64.b64encode(pcm).decode("ascii"))

    async def finish(self, timeout: float) -> str:
        """The press is over: commit the rest and wait for every piece's words, in order."""
        self.commit_resolved = False
        await self.conn.input_audio_buffer.commit()
        async with asyncio.timeout(timeout):
            while True:
                if self.broken:
                    raise ConnectionError("the live transcription session closed")
                if self.commit_resolved and all(i in self.done for i in self.order):
                    break
                self.changed.clear()
                await self.changed.wait()
        spend.record("openai", self.model, spend.CALLS, None, note=f"live transcription, {self.fed / BYTES_PER_SEC:.1f} s")
        return " ".join(self.done[i] for i in self.order if self.done.get(i)).strip()

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._manager is not None:
            with contextlib.suppress(Exception):
                await self._manager.__aexit__(None, None, None)


class OpenAIVoice:
    """Speech to text and text to speech on the daemon's OpenAI key (blocking: run on a thread)."""

    def __init__(self, api_key: str, stt_model: str = DEFAULT_STT_MODEL, tts_model: str = DEFAULT_TTS_MODEL,
                 voice: str = DEFAULT_TTS_VOICE) -> None:
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._key = api_key
        self.stt_model, self.tts_model, self.voice = stt_model, tts_model, voice
        self.live = True                                # LiveEars on (CC_BUDDY_CALL_LIVE_STT=0 turns it off)
        self.or_key, self.or_model, self.or_voice = "", DEFAULT_OR_TTS_MODEL, DEFAULT_OR_TTS_VOICE

    def transcribe(self, pcm: bytes) -> str:
        resp = self._client.audio.transcriptions.create(file=("call.wav", to_wav(pcm), "audio/wav"),
                                                        model=self.stt_model)
        spend.record_transcription(spend.CALLS, self.stt_model, resp, seconds=len(pcm) / BYTES_PER_SEC)
        return (getattr(resp, "text", "") or "").strip()

    def speak_stream(self, text: str) -> Iterator[bytes]:
        """The spoken words as they are made (blocking iterator): the first bytes play while the rest arrive.
        OpenRouter's fast voice first; OpenAI's if it fails before a sound was sent."""
        if self.or_key:
            sent = False
            try:
                import httpx

                with httpx.stream("POST", OPENROUTER_SPEECH_URL, timeout=httpx.Timeout(20.0, connect=5.0),
                                  headers={"Authorization": f"Bearer {self.or_key}"},
                                  json={"model": self.or_model, "voice": self.or_voice, "input": text,
                                        "response_format": "pcm"}) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"OpenRouter speech {resp.status_code}")
                    for chunk in resp.iter_bytes(4800):
                        sent = True
                        yield chunk
                spend.record("openrouter", self.or_model, spend.CALLS, len(text) * OR_TTS_USD_PER_CHAR,
                             note=f"speech, {len(text)} chars")
                return
            except Exception as e:  # noqa: BLE001 — the owner hears OpenAI's voice rather than nothing
                if sent:
                    raise
                log.warning("call: OpenRouter speech failed (%s); OpenAI's voice for this sentence", e)
        with self._client.audio.speech.with_streaming_response.create(
                model=self.tts_model, voice=self.voice, input=text, response_format="pcm") as resp:
            yield from resp.iter_bytes(4800)
        spend.record("openai", self.tts_model, spend.CALLS, None, note=f"speech, {len(text)} chars")

    def live_ears(self) -> Optional[LiveEars]:
        return LiveEars(self._key, self.stt_model) if self.live else None


def make_voice(environ: Any = None) -> Optional[Voice]:
    env = os.environ if environ is None else environ
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        log.warning("call: OPENAI_API_KEY is not set — calls are off")
        return None
    try:
        voice = OpenAIVoice(key, (env.get("CC_BUDDY_CALL_STT_MODEL") or DEFAULT_STT_MODEL).strip(),
                            (env.get("CC_BUDDY_CALL_TTS_MODEL") or DEFAULT_TTS_MODEL).strip(),
                            (env.get("CC_BUDDY_CALL_VOICE") or env.get("CC_BUDDY_VOICE_NAME") or DEFAULT_TTS_VOICE).strip())
        voice.live = (env.get("CC_BUDDY_CALL_LIVE_STT") or "1").strip().lower() not in ("0", "false", "no", "off")
        # the fast voice, when there is an OpenRouter key and CC_BUDDY_CALL_TTS_PROVIDER is not "openai"
        if (env.get("CC_BUDDY_CALL_TTS_PROVIDER") or "openrouter").strip().lower() != "openai":
            voice.or_key = (env.get("OPENROUTER_API_KEY") or "").strip()
            voice.or_model = (env.get("CC_BUDDY_CALL_OR_TTS_MODEL") or DEFAULT_OR_TTS_MODEL).strip()
            voice.or_voice = (env.get("CC_BUDDY_CALL_OR_VOICE") or DEFAULT_OR_TTS_VOICE).strip()
        return voice
    except ImportError as e:
        log.warning("call: the openai SDK is not importable (%s); calls are off", e)
        return None


# ---- the call ------------------------------------------------------------------------------------

class Brain(Protocol):
    """The chat, lent by the daemon (telegram.TelegramInlet): ``hear`` hands it the owner's words as a message;
    ``listen(say)`` has everything it then says to the owner also passed to ``say`` until ``listen(None)``."""

    def hear(self, text: str) -> None: ...
    def listen(self, say: Optional[Callable[[str], None]]) -> bool: ...


CheckOwner = Callable[[str], Optional[int]]


class Call:
    """One call: presses in, speech out. Each press is timed from release to buddy's first sound, per stage, in
    the log (the owner asked for less latency: measure it, do not guess)."""

    def __init__(self, ws: WebSocket, brain: Brain, voice: Voice, clock: Callable[[], float] = time.monotonic) -> None:
        self.ws, self.brain, self.voice, self.clock = ws, brain, voice, clock
        self.talking = False
        self.press = bytearray()
        self.lines: asyncio.Queue[str] = asyncio.Queue()
        self.reading: Optional[asyncio.Task[Any]] = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.presses = 0
        self.ears: Optional[LiveEars] = None
        self.ears_live = False                  # this press is going to the live session
        self.opening: Optional[asyncio.Task[Any]] = None
        self.released_at: Optional[float] = None   # the press being answered: when it was let go
        self.timing: dict[str, float] = {}

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _state(self, state: str, **extra: Any) -> None:
        if not self.ws.closed:
            with contextlib.suppress(Closed):
                await self.ws.send_json({"type": "state", "state": state, **extra})

    def say(self, text: str) -> None:
        """Something buddy said in the chat (or a sentence of it, streamed): read it out after what is playing."""
        words = speakable(text)
        if words:
            if self.released_at is not None and "brain" not in self.timing:
                self.timing["brain"] = self.clock() - self.released_at
            self.lines.put_nowait(words)

    async def _speech(self, text: str) -> Any:
        """The words' audio as it is made: the blocking stream runs on a thread and hands chunks over."""
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        def pump() -> None:
            try:
                for chunk in self.voice.speak_stream(text):
                    loop.call_soon_threadsafe(chunks.put_nowait, chunk)
            finally:
                loop.call_soon_threadsafe(chunks.put_nowait, None)

        worker = loop.run_in_executor(None, pump)
        try:
            while (chunk := await chunks.get()) is not None:
                yield chunk
        finally:
            await asyncio.gather(worker, return_exceptions=True)

    async def read_out(self) -> None:
        """The reader: one message at a time, a sentence at a time, streamed to the phone as it is made."""
        carry = b""
        while True:
            text = await self.lines.get()
            await self._state("speaking")
            for piece in sentences(text):
                async for chunk in self._speech(piece):
                    data = carry + chunk
                    carry = data[len(data) - len(data) % 2:]
                    data = data[: len(data) - len(data) % 2]
                    if data and not self.ws.closed:
                        if self.released_at is not None:
                            self._first_sound()
                        await self.ws.send_bytes(data)
            if self.lines.empty():
                await self._state("listening")

    def _first_sound(self) -> None:
        t = self.timing
        t["first_sound"] = self.clock() - (self.released_at or self.clock())
        self.released_at = None
        log.info("call: press %.1f s → heard in %.2f s (%s) → first reply %.2f s → first sound %.2f s after release",
                 t.get("press", 0), t.get("stt", 0), "live" if t.get("live") else "upload",
                 t.get("brain", 0), t["first_sound"])

    async def interrupt(self) -> None:
        """A press while buddy talks: stop reading, drop what is queued here and on the phone."""
        while not self.lines.empty():
            self.lines.get_nowait()
        if self.reading is not None and not self.reading.done():
            self.reading.cancel()
            await asyncio.gather(self.reading, return_exceptions=True)
        self.reading = asyncio.ensure_future(self.read_out())
        with contextlib.suppress(Closed):
            await self.ws.send_json({"type": "flush"})

    async def _open_ears(self) -> None:
        ears = self.voice.live_ears()
        if ears is None:
            return
        try:
            await ears.open()
            self.ears = ears
        except Exception as e:  # noqa: BLE001 — no live session: each press is uploaded after release
            log.warning("call: live transcription unavailable (%s); uploading presses instead", type(e).__name__)
            await ears.close()

    async def _begin_press(self) -> None:
        self.talking, self.press, self.ears_live = True, bytearray(), False
        await self.interrupt()
        if self.ears is not None and not self.ears.broken:
            try:
                await self.ears.begin()
                self.ears_live = True
            except Exception as e:  # noqa: BLE001
                log.info("call: live transcription dropped (%s)", type(e).__name__)
                self.ears = None

    async def _feed(self, data: bytes) -> None:
        if not self.talking or len(self.press) >= MAX_PRESS_SECS * BYTES_PER_SEC:
            return
        data = data[: len(data) - len(data) % 2]
        self.press.extend(data)
        if self.ears_live and self.ears is not None:
            try:
                await self.ears.feed(data)
            except Exception:  # noqa: BLE001 — the press is still kept whole, for the upload
                self.ears_live = False

    async def _transcribe(self, pcm: bytes, live: bool) -> tuple[str, bool]:
        if live and self.ears is not None:
            try:
                return await self.ears.finish(timeout=6.0), True
            except Exception as e:  # noqa: BLE001
                log.info("call: live transcription failed (%s); uploading the press", type(e).__name__)
                if self.ears is not None and self.ears.broken:
                    self.ears = None
        return await asyncio.to_thread(self.voice.transcribe, pcm), False

    async def heard(self, pcm: bytes, live: bool, released: float) -> None:
        """One press, done: its words, handed to the chat as the owner's."""
        if len(pcm) < MIN_PRESS_SECS * BYTES_PER_SEC:
            if live and self.ears is not None:
                with contextlib.suppress(Exception):
                    await self.ears.begin()           # nothing of a tap is left in the live session
            await self._state("listening", note="Hold the button while you talk.")
            return
        await self._state("thinking")
        try:
            text, was_live = await self._transcribe(pcm, live)
        except Exception as e:  # noqa: BLE001 — the type only: the error can quote what was said
            log.warning("call: transcription failed: %s", type(e).__name__)
            await self._state("listening", note="I couldn't make that out. Try again?")
            return
        if not text:
            await self._state("listening", note="I didn't catch anything. Try again?")
            return
        self.presses += 1
        self.timing = {"press": len(pcm) / BYTES_PER_SEC, "stt": self.clock() - released, "live": float(was_live)}
        self.released_at = released
        with contextlib.suppress(Closed):
            await self.ws.send_json({"type": "heard", "text": text})
        self.brain.hear(text)

    async def run(self) -> str:
        """Until the phone hangs up or the hour is up. Returns why it ended."""
        self.reading = asyncio.ensure_future(self.read_out())
        self.opening = asyncio.ensure_future(self._open_ears())       # while the owner reaches for the button
        if not self.brain.listen(self.say):
            return "buddy's chat isn't running"
        started = self.clock()
        try:
            while True:
                left = MAX_CALL_SECS - (self.clock() - started)
                if left <= 0:
                    return "the call reached its hour"
                try:
                    op, data = await asyncio.wait_for(self.ws.recv(), min(left, QUIET_SECS))
                except asyncio.TimeoutError:
                    # the phone slept or the page closed without a goodbye (live 2026-09-25: a call left open
                    # that way refused the next one with "already on a call")
                    return "the phone went quiet" if left > QUIET_SECS else "the call reached its hour"
                except Closed:
                    return "you hung up"
                if op == OP_BIN:
                    await self._feed(data)
                    continue
                try:
                    msg = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "talk":
                    await self._begin_press()
                elif kind == "done" and self.talking:
                    self.talking = False
                    self._spawn(self.heard(bytes(self.press), self.ears_live, self.clock()))
                    self.press = bytearray()
                elif kind == "end":
                    return "you hung up"
        finally:
            self.brain.listen(None)
            pending = [t for t in (*self.tasks, self.reading, self.opening) if t is not None and not t.done()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if self.ears is not None:
                await self.ears.close()


class PhoneCalls:
    """The ``/api/call`` endpoint: authenticates the phone, then runs one Call at a time."""

    def __init__(self, check_owner: CheckOwner, brain: Optional[Brain], voice: Optional[Voice],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.check_owner, self.brain, self.voice, self.clock = check_owner, brain, voice, clock
        self.active: Optional[Call] = None

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict[str, str]) -> None:
        ws = await WebSocket.accept(reader, writer, headers)
        try:
            await self._serve(ws)
        except Closed:
            pass
        finally:
            await ws.close()

    async def _serve(self, ws: WebSocket) -> None:
        try:
            op, data = await asyncio.wait_for(ws.recv(), AUTH_SECS)
            hello = json.loads(data.decode("utf-8")) if op == OP_TEXT else {}
        except (asyncio.TimeoutError, ValueError, UnicodeDecodeError):
            hello = {}
        user = self.check_owner(str(hello.get("initData") or "")) if isinstance(hello, dict) else None
        if user is None:
            await ws.send_json({"type": "ended", "reason": "Only buddy's owner can call."})
            return
        if self.brain is None or self.voice is None:
            await ws.send_json({"type": "ended", "reason": "Calls aren't set up on this computer."})
            return
        if self.active is not None:
            # the owner calling again means the old call is over (a closed page, a lost connection): end it
            old = self.active
            log.info("call: a new call replaces the one still open")
            await old.ws.close()
            async with asyncio.timeout(5):
                while self.active is old:
                    await asyncio.sleep(0.05)
        call = self.active = Call(ws, self.brain, self.voice, self.clock)
        log.info("call: the owner is on a call from the phone")
        started = self.clock()
        try:
            await ws.send_json({"type": "state", "state": "connected"})
            reason = await call.run()
        finally:
            self.active = None
            log.info("call: ended after %.0f s, %d press(es)", self.clock() - started, call.presses)
        if not ws.closed:
            with contextlib.suppress(Closed):
                await ws.send_json({"type": "ended", "reason": reason})
