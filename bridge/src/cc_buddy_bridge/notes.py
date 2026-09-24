"""Taking notes: buddy listens to the room and writes down what was said.

Say **"start taking notes"** and buddy stops being a conversationalist and becomes
a recorder. Say **"stop taking notes"**, tap it, or run the command, and it writes
the meeting up.

### How it listens

The daemon already keeps one microphone stream open for the wake word (ears.py),
so this takes a subscription to that same stream rather than opening a second
one: 24 kHz mono, 100 ms blocks. Audio goes up in segments of a few seconds to
OpenAI's transcription model with the key the daemon already has. Nothing is
installed, no model runs locally, and nothing is streamed anywhere until the
owner asks for notes.

### Why the segment length is the interesting number

**While anything is subscribed to the microphone, the wake word is bypassed**
(`ears._on_block`: "muted: never wake on our own voice"). So while buddy is
taking notes it cannot hear "hey buddy", and the only way it learns to stop is by
reading its own transcript. That makes the segment length the stop latency: a
30-second segment means half a minute of recording after the owner says stop.
12 seconds is the compromise — short enough that stopping feels prompt, long
enough that a sentence rarely straddles two segments, and each segment carries
the tail of the last one as context so the model does not lose the thread.

Two stops have no latency at all and both exist for that reason: `cc-buddy-bridge
notes stop`, and a tap on the robot.

### What is kept

The owner chose transcript **and** notes (2026-09-11). So the file holds the
distilled notes — decisions, actions, questions left open — and the full
transcript underneath, because the whole point of notes on a meeting is being
able to go back to what was actually said. The file lives in the memory folder,
under ``transcripts/meetings/<date>/`` beside the conversations' transcripts
(owner, 2026-09-23), so ``memory_search`` finds it and forget reaches it. Two
meetings that start in the same minute get two files, never one over the other.

### What it costs to be wrong

Recording a room is not reversible. So: buddy says out loud that it has started,
the robot holds a distinct pose the whole time, the file says plainly when it
started and stopped, and there is no way to start it except by asking.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import logging
import os
import re
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .recall import RecallConfig
from .transcripts import MEETINGS_SUBDIR

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000                  # what ears.py hands out
DEFAULT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_SEGMENT_SECS = 12.0
DEFAULT_MAX_MINUTES = 180.0
# A segment carries this much of the previous one so a sentence that straddles
# the boundary still has its beginning.
OVERLAP_SECS = 0.6
TRANSCRIBE_TIMEOUT_SECS = 60.0
SUMMARY_TIMEOUT_SECS = 120.0
# Below this the segment is silence and is not worth an upload.
SILENCE_RMS = 120.0
# Meetings that start in the same minute under the same title get -2, -3, … up to this many files.
MAX_SAME_NAME = 50

# "stop taking notes", "that's enough notes", "stop the notes", "okay stop
# recording". Deliberately narrow and anchored on the tail of what was just said,
# because this fires inside a meeting where people say "stop" about other things.
_STOP = re.compile(
    r"\b(?:stop|end|finish|done with|that(?:'?s| is) enough|wrap up|no more)\b[^.?!]{0,24}"
    r"\b(?:not(?:e|es|ing)|record(?:ing)?|transcri\w*)\b"
    r"|\b(?:stop|end|finish)\s+(?:tak\w+\s+)?(?:the\s+)?not(?:e|es)\b"
    r"|\bbuddy,?\s+stop\b",
    re.IGNORECASE,
)

SUMMARY_PROMPT = """You are writing up notes from a recording someone asked their desk robot to take. You are
given a transcript, which may be a meeting, a lecture, a call, or one person thinking out loud. It was
transcribed automatically, so expect wrong words, missing punctuation and no reliable speaker labels.

Write the notes a competent person would write. Specifically:
- A title of six words or fewer, naming the actual subject. Not "Meeting notes".
- The gist: what this was, in one or two sentences.
- The points that carry information. Merge repetition. Drop the greetings, the scheduling chatter and the
  part where someone could not find the link.
- Decisions, only where something was actually decided. Say who decided it when the transcript makes that
  clear, and do not guess a name it does not give you.
- Actions, each one a thing somebody is meant to do. Name the person only when the transcript names them.
- Open questions: what was raised and not resolved. These are the most valuable lines and the easiest to
  lose.
- Anything you could not make out, said plainly, rather than a confident guess. A transcript is noisy and a
  note that invents a number is worse than one that says the number was unclear.

Every list may be empty. An empty list is the honest answer for a recording where nothing of that kind
happened, and padding it is how notes stop being trusted.

Output JSON only:
{"title":"...",
 "gist":"one or two sentences",
 "points":["..."],
 "decisions":["..."],
 "actions":["..."],
 "questions":["..."],
 "unclear":["what you could not make out"]}"""

# The shape is enforced by the API rather than asked for in prose. `json_object`
# is not usable here: it requires the word "json" to appear in the *input*, and
# the input is the transcript of whatever was said in the room. Live probe,
# 2026-09-11: every summary came back 400 "Response input messages must contain
# the word 'json'" until this became a schema.
_LIST = {"type": "array", "items": {"type": "string"}}
SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "gist": {"type": "string"},
        "points": _LIST,
        "decisions": _LIST,
        "actions": _LIST,
        "questions": _LIST,
        "unclear": _LIST,
    },
    "required": ["title", "gist", "points", "decisions", "actions", "questions", "unclear"],
}


# How many words at a boundary are compared for a repeat. The overlap is 0.6 s,
# so a handful of words is the most that can be said twice.
MAX_OVERLAP_WORDS = 8


def _key(word: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", word.lower())


def stitch(previous: str, text: str) -> str:
    """Drop the words a segment repeats from the end of the one before it.

    Segments overlap by ``OVERLAP_SECS`` so that no word is cut in half. The cost
    is that the shared audio is transcribed twice, and the repeat arrives as its
    own line. A live recording ended one line "...as well as the summary?" and
    wrote a second line reading only "Summary?" (2026-09-11).
    """
    tokens = text.split()
    prev = [_key(w) for w in previous.split()]
    new = [_key(w) for w in tokens]
    for n in range(min(MAX_OVERLAP_WORDS, len(prev), len(new)), 0, -1):
        if prev[-n:] == new[:n]:
            return " ".join(tokens[n:])
    return text


class TranscribeClient(Protocol):
    def transcribe(self, wav: bytes, prompt: str) -> str: ...
    def summarize(self, transcript: str) -> str: ...


class OpenAINotesClient:
    """Transcription plus one summary call, on the key the daemon already has."""

    def __init__(self, model: str = DEFAULT_MODEL, summary_model: str = "gpt-5.4-nano",
                 api_key: Optional[str] = None) -> None:
        import openai

        self.model = model
        self.summary_model = summary_model
        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()

    def transcribe(self, wav: bytes, prompt: str = "") -> str:
        # `prompt` carries the tail of the previous segment: it is how the model
        # keeps names and jargon consistent across a boundary.
        resp = self._client.audio.transcriptions.create(
            file=("segment.wav", wav, "audio/wav"),
            model=self.model,
            **({"prompt": prompt} if prompt else {}),
        )
        return (getattr(resp, "text", "") or "").strip()

    def summarize(self, transcript: str) -> str:
        resp = self._client.responses.create(
            model=self.summary_model,
            instructions=SUMMARY_PROMPT,
            input=transcript,
            text={"format": {"type": "json_schema", "name": "notes", "strict": True,
                              "schema": SUMMARY_SCHEMA}},
            max_output_tokens=2000,
            reasoning={"effort": "low"},
        )
        text = (resp.output_text or "").strip()
        if not text:
            raise RuntimeError("empty summary")
        return text


def make_notes_client(environ: Any = None) -> Optional[TranscribeClient]:
    env = os.environ if environ is None else environ
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        log.warning("notes: OPENAI_API_KEY not set — buddy cannot take notes")
        return None
    model = (env.get("CC_BUDDY_NOTES_MODEL_STT") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        return OpenAINotesClient(model=model, api_key=key)
    except ImportError as e:
        log.warning("notes: openai SDK not importable (%s)", e)
        return None


@dataclass(frozen=True)
class NotesConfig:
    segment_secs: float = DEFAULT_SEGMENT_SECS
    max_minutes: float = DEFAULT_MAX_MINUTES
    keep_transcript: bool = True


def configured(environ: Any = None) -> NotesConfig:
    env = os.environ if environ is None else environ

    def num(name: str, default: float, lo: float, hi: float) -> float:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return min(hi, max(lo, float(raw)))
        except ValueError:
            log.warning("notes: %s=%r is not a number; using %s", name, raw, default)
            return default

    keep = (env.get("CC_BUDDY_NOTES_KEEP_TRANSCRIPT") or "1").strip().lower() not in \
        ("0", "false", "no", "off")
    return NotesConfig(
        segment_secs=num("CC_BUDDY_NOTES_SEGMENT_SECS", DEFAULT_SEGMENT_SECS, 4.0, 60.0),
        max_minutes=num("CC_BUDDY_NOTES_MAX_MINUTES", DEFAULT_MAX_MINUTES, 1.0, 600.0),
        keep_transcript=keep,
    )


# ---- audio -----------------------------------------------------------------------------

def to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Raw 16-bit mono PCM into a WAV container, in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def rms(pcm: bytes) -> float:
    """Loudness of a block, without numpy — this runs per segment, not per block."""
    if len(pcm) < 2:
        return 0.0
    import array

    a = array.array("h")
    a.frombytes(pcm[: (len(pcm) // 2) * 2])
    if not a:
        return 0.0
    total = 0
    step = max(1, len(a) // 4000)          # sample it; a full pass is wasted work
    n = 0
    for i in range(0, len(a), step):
        total += a[i] * a[i]
        n += 1
    return (total / n) ** 0.5 if n else 0.0


def wants_stop(text: str) -> bool:
    """Did somebody just ask for the notes to stop?"""
    return bool(_STOP.search(" ".join(str(text or "").split())))


# ---- the session -----------------------------------------------------------------------

@dataclass
class NotesState:
    active: bool = False
    started_at: float = 0.0
    started_wall: Optional[datetime] = None
    segments: int = 0
    words: int = 0
    failed: int = 0
    last_text: str = ""
    stopped_by: str = ""
    path: Optional[Path] = None


class RoomNotes:
    """One recording of the room, from "start notes" to the file it leaves behind.

    Named for what it records, not for what it produces: `NoteTaker` is already
    the diary's vision note taker in explore.py, and two classes with one name
    in one daemon is how the wrong one gets wired.
    """

    def __init__(
        self,
        cfg: RecallConfig,
        notes_cfg: NotesConfig,
        client: Optional[TranscribeClient],
        ears: Any,
        on_state: Optional[Callable[[str], None]] = None,
        say: Optional[Callable[[str], None]] = None,
        wall: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.notes_cfg = notes_cfg
        self.client = client
        self.ears = ears
        self.on_state = on_state
        self.say = say
        self.wall = wall
        self.clock = clock
        self.state = NotesState()
        self.lines: list[tuple[float, str]] = []      # (seconds from start, text)
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self._pool: Optional[concurrent.futures.ThreadPoolExecutor] = None

    # -- lifecycle --

    @property
    def active(self) -> bool:
        return self.state.active

    def _executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2,
                                                               thread_name_prefix="notes")
        return self._pool

    def start(self) -> dict[str, Any]:
        """Begin recording. Returns what to tell the owner."""
        if self.state.active:
            return {"ok": True, "already": True, "minutes": self.minutes()}
        if self.client is None:
            return {"ok": False, "error": "no OPENAI_API_KEY, so buddy cannot transcribe"}
        if self.ears is None or not getattr(self.ears, "listening", True):
            return {"ok": False, "error": "the microphone is not running"}
        self.state = NotesState(active=True, started_at=self.clock(), started_wall=self.wall())
        self.lines = []
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="notes")
        log.info("notes: started")
        if self.on_state is not None:
            self.on_state("notes")
        return {"ok": True, "started": True}

    def request_stop(self, reason: str = "asked") -> None:
        if self.state.active and not self.state.stopped_by:
            self.state.stopped_by = reason
            self._stop.set()

    async def stop(self, reason: str = "asked") -> dict[str, Any]:
        """Stop and write the file. Safe to call when nothing is running."""
        if not self.state.active:
            return {"ok": False, "error": "buddy is not taking notes"}
        self.request_stop(reason)
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=SUMMARY_TIMEOUT_SECS + 30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return {"ok": True, "path": str(self.state.path) if self.state.path else None,
                "minutes": round(self.minutes(), 1), "words": self.state.words}

    def minutes(self) -> float:
        if not self.state.started_at:
            return 0.0
        return (self.clock() - self.state.started_at) / 60.0

    def status(self) -> dict[str, Any]:
        return {"active": self.state.active, "minutes": round(self.minutes(), 1),
                "segments": self.state.segments, "words": self.state.words,
                "failed": self.state.failed, "last": self.state.last_text[-160:],
                "path": str(self.state.path) if self.state.path else None}

    # -- the loop --

    async def _run(self) -> None:
        mic = self.ears.subscribe()
        seg = bytearray()
        tail = b""
        target = int(self.notes_cfg.segment_secs * SAMPLE_RATE) * 2
        overlap = int(OVERLAP_SECS * SAMPLE_RATE) * 2
        # Segments are transcribed by ONE worker, in the order they were spoken.
        # Transcribing them concurrently scrambles the transcript — a slow segment
        # lands after the one that followed it — and leaves every segment's
        # context prompt empty, because the previous text has not come back yet.
        queue: "asyncio.Queue[Optional[tuple[bytes, float]]]" = asyncio.Queue()
        worker = asyncio.create_task(self._transcribe_worker(queue), name="notes-transcribe")
        try:
            while not self._stop.is_set():
                if self.minutes() >= self.notes_cfg.max_minutes:
                    log.warning("notes: hit the %.0f minute cap — stopping", self.notes_cfg.max_minutes)
                    self.state.stopped_by = self.state.stopped_by or "the time cap"
                    break
                try:
                    block = await asyncio.wait_for(mic.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                seg.extend(block)
                if len(seg) >= target:
                    audio = tail + bytes(seg)
                    tail = bytes(seg)[-overlap:] if overlap else b""
                    seg.clear()
                    queue.put_nowait((audio, self.clock() - self.state.started_at))
            if seg:                      # whatever is left is still speech
                queue.put_nowait((tail + bytes(seg), self.clock() - self.state.started_at))
        finally:
            self.ears.unsubscribe(mic)
            queue.put_nowait(None)
            try:
                await asyncio.wait_for(worker, timeout=TRANSCRIBE_TIMEOUT_SECS * 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                worker.cancel()
            await self._finish()

    async def _transcribe_worker(self, queue: "asyncio.Queue[Optional[tuple[bytes, float]]]") -> None:
        """One segment at a time, in the order it was said."""
        while True:
            item = await queue.get()
            if item is None:
                return
            audio, at = item
            await self._segment(audio, at)

    async def _segment(self, pcm: bytes, at: float) -> None:
        """One chunk of audio to text. A failure loses a segment, never the session."""
        if self.client is None:
            return
        level = rms(pcm)
        if level < SILENCE_RMS:
            return                      # nobody said anything; do not pay to find that out
        prompt = " ".join(t for _, t in self.lines[-2:])[-400:]
        loop = asyncio.get_running_loop()
        try:
            text = await asyncio.wait_for(
                loop.run_in_executor(self._executor(), self.client.transcribe, to_wav(pcm), prompt),
                timeout=TRANSCRIBE_TIMEOUT_SECS)
        except Exception as e:  # noqa: BLE001
            self.state.failed += 1
            log.warning("notes: a segment did not transcribe (%s: %s)", type(e).__name__, e)
            return
        text = " ".join(str(text or "").split())
        # The overlap is there so a word is never cut in half. It is not there to
        # be transcribed twice, so the repeat comes off before the line is kept.
        text = stitch(self.lines[-1][1] if self.lines else "", text)
        if not text:
            return
        self.lines.append((at, text))
        self.state.segments += 1
        self.state.words += len(text.split())
        self.state.last_text = text
        log.info("notes: +%d words (%.0f min in)", len(text.split()), at / 60.0)
        if wants_stop(text):
            log.info("notes: heard the stop in %r", text[-80:])
            self.request_stop("asked out loud")

    # -- the write-up --

    async def _finish(self) -> None:
        self.state.active = False
        if self.on_state is not None:
            self.on_state("idle")
        if not self.lines:
            log.info("notes: nothing was said, so nothing was written")
            if self.say is not None:
                self.say("nothing")
            return
        transcript = "\n".join(f"[{int(t // 60):02d}:{int(t % 60):02d}] {text}" for t, text in self.lines)
        summary: dict[str, Any] = {}
        if self.client is not None:
            loop = asyncio.get_running_loop()
            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(self._executor(), self.client.summarize, transcript),
                    timeout=SUMMARY_TIMEOUT_SECS)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    summary = parsed
            except Exception as e:  # noqa: BLE001
                log.warning("notes: could not write them up (%s: %s) — keeping the transcript",
                            type(e).__name__, e)
        self.state.path = write_notes(self.cfg, summary, transcript, self.state,
                                      keep_transcript=self.notes_cfg.keep_transcript)
        log.info("notes: %d words over %.1f min → %s", self.state.words, self.minutes(),
                 self.state.path.name if self.state.path else "(not written)")
        if self.say is not None:
            self.say(str(summary.get("title") or "your notes"))

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None


# ---- the file --------------------------------------------------------------------------

def _items(value: Any, cap: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [" ".join(str(v).split()) for v in value[:cap] if str(v).strip()]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return "-".join([w for w in s.split("-") if w][:5]) or "notes"


def notes_dir(cfg: RecallConfig) -> Path:
    """Meeting notes live beside the transcripts (owner, 2026-09-23: one memory folder), where
    ``memory_search`` finds them and forget reaches them."""
    return cfg.transcripts_dir / MEETINGS_SUBDIR


def render(summary: dict[str, Any], transcript: str, state: NotesState,
           keep_transcript: bool = True) -> str:
    started = state.started_wall or datetime.now()
    title = str(summary.get("title") or "Notes").strip().rstrip(".")
    parts = [
        "---",
        "status: notes",
        "source: buddy-notes",
        f"started: {started:%Y-%m-%d %H:%M}",
        f"minutes: {state.words and round(len(transcript.split()) / 130.0, 1) or 0}",
        f"words: {state.words}",
        f"stopped_by: {state.stopped_by or 'asked'}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    gist = str(summary.get("gist") or "").strip()
    if gist:
        parts.extend([gist, ""])
    for heading, key in (("Points", "points"), ("Decisions", "decisions"),
                         ("Actions", "actions"), ("Open questions", "questions")):
        rows = _items(summary.get(key))
        if rows:
            parts.extend([f"## {heading}", ""])
            parts.extend(f"- {r}" for r in rows)
            parts.append("")
    unclear = _items(summary.get("unclear"))
    if unclear:
        parts.extend(["## Not made out", "",
                      "<!-- the transcript was unclear here; the words below are buddy's best guess -->"])
        parts.extend(f"- {r}" for r in unclear)
        parts.append("")
    if not summary:
        parts.extend(["> The write-up failed, so this is the transcript alone.", ""])
    if keep_transcript:
        parts.extend(["## Transcript", "",
                      "<!-- automatic transcription: expect wrong words and no speaker labels -->",
                      "", transcript, ""])
    return "\n".join(parts)


def write_notes(cfg: RecallConfig, summary: dict[str, Any], transcript: str, state: NotesState,
                keep_transcript: bool = True) -> Optional[Path]:
    """One new file per meeting, 0600 in 0700 folders. Two meetings that start in the same minute with the
    same title get two files: the second is never written over the first."""
    started = state.started_wall or datetime.now()
    try:
        d = notes_dir(cfg) / f"{started:%Y-%m-%d}"
        d.mkdir(parents=True, exist_ok=True)
        for parent in (cfg.store, cfg.transcripts_dir, notes_dir(cfg), d):
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass
        stem = f"{started:%H%M}-{_slug(summary.get('title') or 'notes')}"
        data = render(summary, transcript, state, keep_transcript).encode("utf-8")
        for n in range(1, MAX_SAME_NAME + 1):
            path = d / (f"{stem}.md" if n == 1 else f"{stem}-{n}.md")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return path
        log.warning("notes: %d notes already share this minute and title — not written", MAX_SAME_NAME)
        return None
    except OSError as e:
        log.warning("notes: could not write the file (%s)", type(e).__name__)
        return None


def recent(cfg: RecallConfig, limit: int = 20) -> list[Path]:
    """The newest note files, newest first."""
    root = notes_dir(cfg)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for day in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        out.extend(sorted((f for f in day.iterdir() if f.suffix == ".md"), reverse=True))
        if len(out) >= limit:
            break
    return out[:limit]
