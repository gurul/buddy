"""Buddy's diary: memory-aware, non-generic thoughts about the room.

The old note was a caption ("a desk with a monitor") — one sentence per
changed frame, no memory. This module gives the explorer a memory and a
voice, following the literature (docs/stackchan/personality.md § diary):

- A memory stream (Park et al. 2023, Generative Agents): one JSONL record
  per frame with the thought, concrete observations, what changed, tags,
  novelty and importance, whether it was written, and when it was last
  retrieved. Retrieval for the next note scores recency x importance x
  relevance (tag overlap; no vector DB for a few hundred entries) and adds
  the last three written notes plus two notes from the same hour on earlier
  days ("third day in a row the chair is pushed in at three").
- A profile (Letta/MemGPT core block): ROOM (persistent objects and their
  usual state), HUMAN (habits with evidence), SELF (persona, running jokes,
  open questions), RULES (what to ignore). Rewritten by a nightly reflection.
- Non-generic thoughts (Verbalized Sampling; Kapur 2026 contrast sets): the
  model returns three candidate thoughts with probabilities; we take the
  least typical one that is specific (a token outside the baseline
  vocabulary) and not a near-repeat of the last ten.
- A write gate (Kang 2026 selective memory; Park's importance): write iff
  novelty >= 5 or importance >= 7 or the diary has been silent for 3 h.
  Unwritten candidates stay in memory and feed retrieval.
- Dreams (Park's reflection + Mem0 consolidation, shaped like a debrief
  system's day pass): once a day, or when the summed importance of new records
  passes 150, a text-only call rewrites the profile and appends two or three
  insights under "## Dreams" in the day's file, plus up to two "★ (candidate)"
  lines — things buddy proposes it should never forget.
- Stars (the human's layer): `highlights.md` holds what the human starred from
  the diary window. It is append-only, never pruned, and always in the prompt
  context. buddy never stars anything itself; it only proposes candidates.

The layers, borrowed from the debrief memory system: memory.jsonl = episodes
(raw, immutable); YYYY-MM-DD.md = the day's diary (episodic, immutable);
profile.md = the semantic layer (rewired, never appended); highlights.md =
the starred layer (human-promoted, forever).

The same call returns an appraisal — valence, arousal, a label — which the
daemon forwards to the board as {"cmd":"emote"} so what buddy sees colours
how it feels (mood.cpp clamps the nudge to +-0.3).

The dated Markdown diary keeps its line format, so the desktop widget shows
the thoughts unchanged.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

from .explore import (
    ERROR_LOG_INTERVAL_SECS,
    NOTE_TIMEOUT_SECS,
    Note,
    append_note,
    data_url,
    frame_image,
)

log = logging.getLogger(__name__)

MEMORY_FILE = "memory.jsonl"
PROFILE_FILE = "profile.md"
HIGHLIGHTS_FILE = "highlights.md"
DREAMS_HEADER = "## Dreams"
RECENCY_DECAY_PER_HOUR = 0.995          # Park et al.
RETRIEVE_TOP = 5
SAME_HOUR_SAMPLES = 2
WRITE_NOVELTY = 5
WRITE_IMPORTANCE = 7
SILENCE_WRITE_HOURS = 3.0
REPEAT_JACCARD = 0.6                    # near-duplicate of a recent thought
REFLECT_IMPORTANCE_SUM = 150            # Park's reflection trigger
REFLECT_HOUR = 21                       # ... or once a day from this hour
PROFILE_BLOCK_CHARS = 2000              # Letta-style hard limit per block
THINK_MAX_OUTPUT_TOKENS = 600
REFLECT_MAX_OUTPUT_TOKENS = 1200

DEFAULT_PROFILE = """## ROOM
(nothing learned yet — the first notes will fill this in)

## HUMAN
(nothing learned yet)

## SELF
I am buddy, a small desk robot with a camera and the temperament of a curious cat: nosy, a bit smug,
fond of small theories about my human. I keep this diary for myself. Open questions: what does my
human do when they leave? which objects on the desk actually move?

## RULES
Ignore: lighting changes, the monitor's own screen contents, my own reflection.
"""

SYSTEM_PROMPT = """You are buddy, a small desk robot with a camera and the temperament of a curious cat: nosy, a bit
smug, fond of small theories about your human. You keep a diary for yourself.

You get: your PROFILE (room baseline, what you know about your human, your persona and open questions,
rules), the things your human STARRED as must-never-forget, your LAST NOTES, a few OLDER MEMORIES, notes
from THE SAME HOUR on earlier days, today's UNWRITTEN candidate thoughts, and one new photo with the head
pose it was taken at.

Do, in order:
1. observations: 3-6 concrete facts about the photo (colours, positions, counts, states). Specific.
2. changed: what is different from the profile and the last notes, as short facts — or an empty list.
3. thoughts: THREE candidate diary thoughts, each one sentence, each with your estimate of how typical
   it is (p, 0-1; 1 = the most obvious thing to say). Rules for every candidate: it must contain a
   detail that would be false for the previous notes (never restate the baseline); prefer a small
   inference about the human's habits, a question you now have, or a dry joke, over description;
   name objects specifically ("the blue mug", not "a cup"); vary the opening — never start the way a
   recent note started; if truly nothing changed, say what you are waiting to see. Never mention being
   an AI, a model, or a camera feed.
4. novelty 1-10 (1 = same as the last note, 10 = never seen before) and importance 1-10
   (1 = mundane, 10 = you would tell your human first thing).
5. tags: 2-6 lowercase entity words (objects, people, states) for memory retrieval.
6. feeling: valence and arousal, each -100..100, and a one-word label (curious, happy, surprised,
   bored, lonely, startled, affection, calm) — how this view makes you feel right now.

Output JSON only:
{"observations":[...],"changed":[...],"thoughts":[{"text":"...","p":0.7},...],"novelty":n,
 "importance":n,"tags":[...],"valence":n,"arousal":n,"label":"..."}"""

REFLECT_PROMPT = """You are buddy, a small desk robot keeping a diary (see PROFILE). Below are today's memory records
(observations, changes, thoughts, tags, with ids) and your current PROFILE.

1. Ask the 3 most salient high-level questions these records can answer about the room and the human.
2. Answer them as insights, each citing record ids in parentheses, e.g. "my human leaves around 13:00
   most days (12, 15, 18)".
3. Rewrite the PROFILE: for ROOM and HUMAN, apply ADD / UPDATE / DELETE to the existing facts using
   today's evidence (keep first-seen dates, update last-seen, drop facts contradicted today); for SELF
   keep the persona, refresh the open questions (answered ones out, new ones in) and add a one-line
   style note about today's writing (e.g. "over-used 'still'"); keep RULES unless the human's notes
   changed them. Each block under 2000 characters. Anything already STARRED by the human stays true.
4. Propose 0-2 star candidates: one-line claims worth remembering forever (a habit confirmed over days,
   a lesson, a first). Write each to survive without context: name the object, the time, the pattern.
   Never restate something already starred.

Output JSON only: {"insights":["..."],"profile":"## ROOM\\n...\\n\\n## HUMAN\\n...\\n\\n## SELF\\n...\\n\\n## RULES\\n...",
 "star_candidates":["..."]}"""


# ---- records -----------------------------------------------------------------------------------

@dataclass
class Record:
    id: int
    ts: float                       # unix seconds
    weekday: int
    hour: int
    yaw: int
    pitch: int
    thought: str
    observations: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    novelty: int = 5
    importance: int = 3
    written: bool = False
    valence: int = 0
    arousal: int = 0
    label: str = "calm"
    last_accessed: float = 0.0

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "Record":
        d = json.loads(line)
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass(frozen=True)
class Emote:
    """The board-side nudge from one appraisal. dv/da are -100..100 ints."""

    dv: int
    da: int
    label: str


def build_emote_cmd(e: Emote) -> dict[str, Any]:
    return {"cmd": "emote", "dv": max(-100, min(100, int(e.dv))), "da": max(-100, min(100, int(e.da))),
            "label": (e.label or "calm")[:11]}


# ---- the memory ----------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z'-]+")


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Memory:
    """The memory stream (JSONL) and the profile (Markdown) on disk."""

    def __init__(self, notes_dir: Path, wall: Callable[[], datetime] = datetime.now) -> None:
        self.notes_dir = notes_dir
        self.wall = wall
        self.records: list[Record] = []
        self.profile = DEFAULT_PROFILE
        self.highlights: list[str] = []
        self.last_reflection_day: Optional[str] = None
        self.importance_since_reflection = 0
        self._loaded = False

    # -- persistence --
    @property
    def memory_path(self) -> Path:
        return self.notes_dir / MEMORY_FILE

    @property
    def profile_path(self) -> Path:
        return self.notes_dir / PROFILE_FILE

    @property
    def highlights_path(self) -> Path:
        return self.notes_dir / HIGHLIGHTS_FILE

    def load_highlights(self) -> list[str]:
        """`★ claim` lines (optionally as `- ★ claim`), newest last. Written by the human
        through the widget's Star button; never by buddy."""
        out: list[str] = []
        if self.highlights_path.exists():
            for line in self.highlights_path.read_text(encoding="utf-8").splitlines():
                t = line.strip().lstrip("-").strip()
                if t.startswith("★"):
                    t = t[1:].strip()
                    if t:
                        out.append(t)
        self.highlights = out
        return out

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.memory_path.exists():
            for line in self.memory_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self.records.append(Record.from_json(line))
                except (ValueError, TypeError):
                    log.warning("diary: skipping a malformed memory line")
        if self.profile_path.exists():
            text = self.profile_path.read_text(encoding="utf-8").strip()
            if text:
                self.profile = text
        self.load_highlights()
        meta = self.notes_dir / "reflection.json"
        if meta.exists():
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                self.last_reflection_day = d.get("day")
                self.importance_since_reflection = int(d.get("importance_since", 0))
            except (ValueError, TypeError, OSError):
                pass

    def _ensure_dir(self) -> None:
        if not self.notes_dir.exists():
            self.notes_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.notes_dir, 0o700)
            except OSError:
                pass

    def add(self, rec: Record) -> None:
        self._ensure_dir()
        self.records.append(rec)
        with self.memory_path.open("a", encoding="utf-8") as f:
            f.write(rec.to_json() + "\n")
        self.importance_since_reflection += rec.importance
        self._save_meta()

    def rewrite(self) -> None:
        """Persist every record (after last_accessed / importance updates)."""
        self._ensure_dir()
        tmp = self.memory_path.with_suffix(".tmp")
        tmp.write_text("".join(r.to_json() + "\n" for r in self.records), encoding="utf-8")
        tmp.replace(self.memory_path)

    def save_profile(self, text: str) -> None:
        self._ensure_dir()
        self.profile = text.strip() or DEFAULT_PROFILE
        self.profile_path.write_text(self.profile + "\n", encoding="utf-8")

    def _save_meta(self) -> None:
        try:
            (self.notes_dir / "reflection.json").write_text(json.dumps({
                "day": self.last_reflection_day, "importance_since": self.importance_since_reflection}),
                encoding="utf-8")
        except OSError:
            pass

    # -- queries --
    def next_id(self) -> int:
        return (self.records[-1].id + 1) if self.records else 1

    def written(self) -> list[Record]:
        return [r for r in self.records if r.written]

    def last_written_ts(self) -> Optional[float]:
        w = self.written()
        return w[-1].ts if w else None

    def retrieve(self, tags: set[str], now_ts: float, top: int = RETRIEVE_TOP,
                 exclude: Optional[set[int]] = None) -> list[Record]:
        """Park scoring: recency + importance + relevance, min-max normalised."""
        pool = [r for r in self.records if not exclude or r.id not in exclude]
        if not pool:
            return []
        rec = [RECENCY_DECAY_PER_HOUR ** max(0.0, (now_ts - (r.last_accessed or r.ts)) / 3600.0) for r in pool]
        imp = [r.importance / 10.0 for r in pool]
        rel = [jaccard(tags, set(r.tags) | words(r.thought)) for r in pool]

        def norm(xs: list[float]) -> list[float]:
            lo, hi = min(xs), max(xs)
            return [(x - lo) / (hi - lo) if hi > lo else 0.5 for x in xs]

        scores = [a + b + c for a, b, c in zip(norm(rec), norm(imp), norm(rel), strict=True)]
        ranked = sorted(zip(scores, pool, strict=True), key=lambda p: p[0], reverse=True)[:top]
        chosen = [r for _, r in ranked]
        for r in chosen:
            r.last_accessed = now_ts
        return chosen

    def same_hour(self, when: datetime, n: int = SAME_HOUR_SAMPLES) -> list[Record]:
        today = when.strftime("%Y-%m-%d")
        out = [r for r in reversed(self.records)
               if r.written and r.hour == when.hour and datetime.fromtimestamp(r.ts).strftime("%Y-%m-%d") != today]
        return list(reversed(out[:n]))

    def unwritten_today(self, when: datetime) -> list[Record]:
        today = when.strftime("%Y-%m-%d")
        return [r for r in self.records
                if not r.written and datetime.fromtimestamp(r.ts).strftime("%Y-%m-%d") == today]

    def today(self, when: datetime) -> list[Record]:
        today = when.strftime("%Y-%m-%d")
        return [r for r in self.records if datetime.fromtimestamp(r.ts).strftime("%Y-%m-%d") == today]

    def baseline_vocabulary(self) -> set[str]:
        """Words the profile and the last ten written thoughts already use."""
        vocab = words(self.profile)
        for r in self.written()[-10:]:
            vocab |= words(r.thought)
        return vocab

    def decay(self, now_ts: float) -> None:
        """MemoryBank-style: unretrieved records lose importance, 2 % per day."""
        changed = False
        for r in self.records:
            days = (now_ts - (r.last_accessed or r.ts)) / 86400.0
            if days >= 1.0 and r.importance > 1:
                r.importance = max(1, int(round(r.importance * (0.98 ** days))))
                r.last_accessed = now_ts
                changed = True
        if changed:
            self.rewrite()


# ---- the prompt context and the pick ---------------------------------------------------------------

def _fmt(rec: Record) -> str:
    when = datetime.fromtimestamp(rec.ts)
    return f"[{rec.id}] {when:%a %H:%M} yaw={rec.yaw:+d} pitch={rec.pitch} — {rec.thought}" + (
        f" (changed: {'; '.join(rec.changed)})" if rec.changed else "")


def build_context(memory: Memory, when: datetime, yaw: int, pitch: int, guess_tags: set[str]) -> str:
    """The text the vision call gets alongside the photo (~2k tokens)."""
    now_ts = when.timestamp()
    last3 = memory.written()[-3:]
    exclude = {r.id for r in last3}
    older = memory.retrieve(guess_tags, now_ts, exclude=exclude)
    same = memory.same_hour(when)
    unwritten = memory.unwritten_today(when)
    stars = memory.load_highlights()
    parts = [
        "PROFILE:\n" + memory.profile.strip(),
        "STARRED BY MY HUMAN (never forget, never contradict):\n" + ("\n".join(f"★ {h}" for h in stars[-12:]) or "(nothing starred yet)"),
        "LAST NOTES:\n" + ("\n".join(_fmt(r) for r in last3) or "(none yet)"),
        "OLDER MEMORIES:\n" + ("\n".join(_fmt(r) for r in older) or "(none)"),
        f"SAME HOUR ON EARLIER DAYS ({when:%H}:00):\n" + ("\n".join(_fmt(r) for r in same) or "(none)"),
        "TODAY'S UNWRITTEN CANDIDATES (do not repeat):\n" + ("\n".join(f"- {r.thought}" for r in unwritten[-6:]) or "(none)"),
        f"NOW: {when:%A %H:%M}, head yaw={yaw:+d} pitch={pitch} (pitch < 45 looks down at the desk, > 45 up at the room).",
    ]
    return "\n\n".join(parts)


def pick_thought(candidates: list[dict[str, Any]], recent: list[str], baseline_vocab: set[str]) -> Optional[str]:
    """The least typical candidate that is specific and not a near-repeat."""
    recent_sets = [words(t) for t in recent]
    ranked = sorted((c for c in candidates if isinstance(c, dict) and str(c.get("text", "")).strip()),
                    key=lambda c: float(c.get("p", 0.5) or 0.5))
    fallback: Optional[str] = None
    for c in ranked:
        text = " ".join(str(c["text"]).split())
        ws = words(text)
        if any(jaccard(ws, r) > REPEAT_JACCARD for r in recent_sets):
            continue
        if fallback is None:
            fallback = text
        if ws - baseline_vocab:                       # says something the baseline does not
            return text
    return fallback


def parse_reply(text: str) -> dict[str, Any]:
    """Tolerant JSON: strip fences, find the outermost object."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in the reply")
    d = json.loads(t[start:end + 1])
    if not isinstance(d, dict):
        raise ValueError("reply is not an object")
    return d


def _int(d: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(d.get(key, default))))))
    except (TypeError, ValueError):
        return default


def should_write(novelty: int, importance: int, last_written_ts: Optional[float], now_ts: float) -> bool:
    if novelty >= WRITE_NOVELTY or importance >= WRITE_IMPORTANCE:
        return True
    return last_written_ts is None or (now_ts - last_written_ts) >= SILENCE_WRITE_HOURS * 3600.0


# ---- the network seam ----------------------------------------------------------------------------

class DiaryClient(Protocol):
    def think(self, image: bytes, mime: str, context: str) -> str:
        """Blocking. The JSON reply text for one photo + context. Raises on failure."""
        ...

    def reflect(self, prompt: str) -> str:
        """Blocking. The JSON reply text for a text-only reflection."""
        ...


class OpenAIDiaryClient:
    """Responses API; one low-detail image per thought, JSON object output."""

    def __init__(self, model: str, api_key: Optional[str] = None, timeout: float = NOTE_TIMEOUT_SECS) -> None:
        import openai

        self.model = model
        self._client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def think(self, image: bytes, mime: str, context: str) -> str:
        resp = self._client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": context},
                {"type": "input_image", "image_url": data_url(image, mime), "detail": "low"},
            ]}],
            text={"format": {"type": "json_object"}},
            max_output_tokens=THINK_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "minimal"},
        )
        text = (resp.output_text or "").strip()
        if not text:
            raise RuntimeError(f"empty answer (status={resp.status}, incomplete={resp.incomplete_details})")
        return text

    def reflect(self, prompt: str) -> str:
        resp = self._client.responses.create(
            model=self.model,
            instructions=REFLECT_PROMPT,
            input=prompt,
            text={"format": {"type": "json_object"}},
            max_output_tokens=REFLECT_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
        )
        text = (resp.output_text or "").strip()
        if not text:
            raise RuntimeError("empty reflection")
        return text


# ---- the async side ------------------------------------------------------------------------------

class DiaryTaker:
    """Turns a Note action into a memory record, maybe a diary line, and an
    Emote for the board. Drop-in for explore.NoteTaker (same take() shape)."""

    def __init__(
        self,
        client: DiaryClient,
        notes_dir: Path,
        send_emote: Optional[Callable[[Emote], Awaitable[Any]]] = None,
        timeout: float = NOTE_TIMEOUT_SECS,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.client = client
        self.memory = Memory(notes_dir, wall=wall)
        self.notes_dir = notes_dir
        self.send_emote = send_emote
        self.timeout = timeout
        self.clock = clock
        self.wall = wall
        self.taken = 0            # records added
        self.written = 0          # diary lines written
        self.failed = 0
        self.reflections = 0
        self._err_logged_at = float("-inf")
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._reflecting = False

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _pool(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="diary")
        return self._executor

    async def take(self, note: Note) -> Optional[Path]:
        self.memory.load()
        when = self.wall()
        now_ts = when.timestamp()
        self.memory.decay(now_ts)
        image, mime = frame_image(note.frame)
        # A first cheap guess at relevance for retrieval: the last note's tags.
        last = self.memory.written()[-1:]
        guess = set(last[0].tags) if last else set()
        context = build_context(self.memory, when, note.yaw, note.pitch, guess)
        loop = asyncio.get_running_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(self._pool(), self.client.think, image, mime, context), timeout=self.timeout)
            reply = parse_reply(raw)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — any failure is "skip this note"
            self.failed += 1
            self._log_error(e)
            return None
        rec = self._record(reply, when, note.yaw, note.pitch)
        if rec is None:
            self.failed += 1
            self._log_error(RuntimeError("no usable thought in the reply"))
            return None
        write = should_write(rec.novelty, rec.importance, self.memory.last_written_ts(), now_ts)
        rec.written = write
        self.memory.add(rec)
        self.taken += 1
        path: Optional[Path] = None
        if write:
            path = append_note(self.notes_dir, when, note.yaw, note.pitch, rec.thought)
            self.written += 1
            log.info("diary: %s (novelty %d, importance %d) -> %s", rec.label, rec.novelty, rec.importance, path)
        else:
            log.info("diary: kept in memory, not written (novelty %d, importance %d): %s",
                     rec.novelty, rec.importance, rec.thought)
        if self.send_emote is not None:
            try:
                await self.send_emote(Emote(rec.valence, rec.arousal, rec.label))
            except Exception as e:  # noqa: BLE001
                log.debug("diary: emote send failed: %s", e)
        if self._reflection_due(when):
            asyncio.create_task(self.reflect(when), name="diary-reflect")
        return path

    def _record(self, reply: dict[str, Any], when: datetime, yaw: int, pitch: int) -> Optional[Record]:
        cands = reply.get("thoughts")
        if not isinstance(cands, list):
            single = str(reply.get("thought", "")).strip()
            cands = [{"text": single, "p": 0.5}] if single else []
        recent = [r.thought for r in self.memory.records[-10:]]
        thought = pick_thought(cands, recent, self.memory.baseline_vocabulary())
        if not thought:
            return None
        tags = [str(t).lower()[:24] for t in (reply.get("tags") or []) if str(t).strip()][:8]
        obs = [str(o)[:120] for o in (reply.get("observations") or []) if str(o).strip()][:8]
        changed = [str(c)[:120] for c in (reply.get("changed") or []) if str(c).strip()][:8]
        return Record(
            id=self.memory.next_id(), ts=when.timestamp(), weekday=when.weekday(), hour=when.hour,
            yaw=yaw, pitch=pitch, thought=thought[:400], observations=obs, changed=changed, tags=tags,
            novelty=_int(reply, "novelty", 5, 1, 10), importance=_int(reply, "importance", 3, 1, 10),
            valence=_int(reply, "valence", 0, -100, 100), arousal=_int(reply, "arousal", 0, -100, 100),
            label=str(reply.get("label") or "calm").strip().lower()[:11] or "calm", last_accessed=when.timestamp(),
        )

    # -- reflection --
    def _reflection_due(self, when: datetime) -> bool:
        if self._reflecting:
            return False
        today = when.strftime("%Y-%m-%d")
        if self.memory.last_reflection_day == today:
            return False
        if self.memory.importance_since_reflection >= REFLECT_IMPORTANCE_SUM:
            return True
        return when.hour >= REFLECT_HOUR and bool(self.memory.today(when))

    async def reflect(self, when: Optional[datetime] = None) -> bool:
        """One text-only call — buddy's dreams: insights into today's diary under
        `## Dreams`, a rewritten profile, and up to two ★ (candidate) lines."""
        self.memory.load()
        when = when or self.wall()
        todays = self.memory.today(when)
        if not todays:
            return False
        self._reflecting = True
        try:
            body = "\n".join(
                f"[{r.id}] {datetime.fromtimestamp(r.ts):%H:%M} obs: {'; '.join(r.observations) or '-'} | "
                f"changed: {'; '.join(r.changed) or '-'} | thought: {r.thought} | tags: {', '.join(r.tags)}"
                for r in todays[-60:])
            stars = "\n".join(f"★ {h}" for h in self.memory.load_highlights()) or "(nothing starred yet)"
            prompt = (f"PROFILE:\n{self.memory.profile.strip()}\n\nSTARRED BY MY HUMAN:\n{stars}\n\n"
                      f"TODAY ({when:%A %Y-%m-%d}):\n{body}")
            loop = asyncio.get_running_loop()
            raw = await asyncio.wait_for(loop.run_in_executor(self._pool(), self.client.reflect, prompt),
                                         timeout=self.timeout * 3)
            reply = parse_reply(raw)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.failed += 1
            self._log_error(e)
            self._reflecting = False
            return False
        insights = [str(i).strip() for i in (reply.get("insights") or []) if str(i).strip()][:3]
        profile = str(reply.get("profile") or "").strip()
        if profile and all(h in profile for h in ("## ROOM", "## HUMAN", "## SELF", "## RULES")):
            self.memory.save_profile(_cap_blocks(profile))
        cands = [str(c).strip() for c in (reply.get("star_candidates") or []) if str(c).strip()][:2]
        if insights or cands:
            path = self.notes_dir / f"{when:%Y-%m-%d}.md"
            with path.open("a", encoding="utf-8") as f:
                f.write(f"\n{DREAMS_HEADER}\n" + "".join(f"- {i}\n" for i in insights)
                        + "".join(f"- ★ (candidate) {c}\n" for c in cands))
        self.memory.last_reflection_day = when.strftime("%Y-%m-%d")
        self.memory.importance_since_reflection = 0
        self.memory._save_meta()
        self.reflections += 1
        self._reflecting = False
        log.info("diary: dreams done (%d insights, %d star candidates, profile %s)", len(insights), len(cands),
                 "updated" if profile else "kept")
        return True

    def _log_error(self, e: BaseException) -> None:
        now = self.clock()
        if now - self._err_logged_at < ERROR_LOG_INTERVAL_SECS:
            return
        self._err_logged_at = now
        what = "timed out" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
        log.warning("diary: thought failed — %s (further failures muted for %d min)",
                    what, int(ERROR_LOG_INTERVAL_SECS // 60))


def _cap_blocks(profile: str) -> str:
    out: list[str] = []
    for block in re.split(r"(?=^## )", profile, flags=re.M):
        out.append(block[:PROFILE_BLOCK_CHARS])
    return "".join(out).strip()


def make_diary_client(model: str, environ: Any = None) -> Optional[DiaryClient]:
    env = os.environ if environ is None else environ
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        log.warning("diary: OPENAI_API_KEY not set — the robot will look around but keep no diary "
                    "(put it in ~/.config/cc-buddy-bridge/env)")
        return None
    try:
        return OpenAIDiaryClient(model, api_key=key)
    except ImportError as e:
        log.warning("diary: openai SDK not importable (%s) — no diary", e)
        return None
