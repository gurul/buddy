"""buddy writes down what was said, and curates its own days.

recall.py is the read half. This is the write half, and the curation that keeps
the store from being a pile of drafts.

### Two calls, both cheap, both off the conversation's critical path

1. **After a conversation closes**, the turns held in RAM are distilled into one
   short note: a title, what was said that matters, what is still open, and what
   buddy itself owes the owner. The transcript is never written to disk — the
   owner asked for distilled memories only, so the words go to the model and the
   summary comes back, and the words are dropped.
2. **Once a day**, buddy aggregates its own drafts into a day file and adds a row
   to the index. In the owner's claude-debrief system a human does this pass;
   buddy's store is its own system and does it for itself (owner instruction,
   2026-09-11).

The one thing buddy still does not do for itself is **star**. A highlight is
permanent and never pruned, and a robot promoting its own conclusions to
never-forget is exactly the laundering the layered design exists to prevent. So
buddy proposes, with a `★ (candidate)` line in the day file, and the owner
promotes.

### Why the layers are worth keeping

The store's shape is not decoration. The session notes stay wrong on purpose:
they record what buddy believed that evening. The day file is what survives. The
index says what is true now. When buddy contradicts itself across two weeks, the
contradiction is visible instead of quietly overwritten.

### Failure is always silence

No key, no network, a refusing model, a full disk: the conversation still
happened, and nothing about the next one breaks. Every entry point swallows its
own errors and logs once.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence

from .recall import RecallConfig

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-nano"
DISTIL_TIMEOUT_SECS = 45.0
CURATE_TIMEOUT_SECS = 90.0
MAX_TURNS_SENT = 60          # a conversation longer than this is trimmed from the start
MAX_CHARS_PER_TURN = 600
DRAFT_STATUS = "machine-draft"
SOURCE = "buddy-voice"
CANDIDATE_MARK = "★ (candidate)"
# The heading buddy writes its own stars under, and the only place they are
# read back from.
OWN_SECTION_TITLE = "from talking"
EXAMPLE_MARK = "*(example)*"

DISTIL_PROMPT = """You are buddy, a small desk robot, writing down what you will want to remember about a
conversation you just had with your owner. You are writing for yourself, to read before the next one.

Keep what a person would keep: what they told you about themselves, what they asked you to do, what you
promised, what you failed at, anything that will still be true next week. Drop the pleasantries, the
wake word, anything you only said to fill a gap, and anything you inferred rather than heard.

Rules that matter:
- "owes" is for debts of your own: a question you did not answer, a task you did not finish, something you
  got wrong. Write each as a sentence starting with "buddy owes". These are the most valuable lines in the
  note, because opening with one is how you show you were paying attention.
- "open" is for threads that are theirs, not yours: something they said they would do, or wanted later.
- Attribute anything the owner said as something they said. Never record a guess as a fact.
- Write each line to survive without the conversation around it: name the thing, not "it".
- An empty list is the right answer when nothing belongs in it. A conversation about nothing gets a note
  that says so, with empty lists.
- "star" is for a claim worth remembering forever: a habit confirmed, a preference stated plainly, a
  lesson. At most one, usually none. You are proposing, not deciding.

Output JSON only:
{"title":"six words or fewer, no punctuation at the end",
 "said":["what the owner told you, one per line, at most four"],
 "open":["their threads, at most three"],
 "owes":["buddy owes ...", "at most three"],
 "star":["at most one claim worth never forgetting"],
 "nothing":true|false}"""

CURATE_PROMPT = """You are buddy, aggregating your own notes from one day of conversations with your owner
into a single day record. In your owner's memory system a human does this pass; you do it for yourself.

You are given the day's notes, each one written right after a conversation. Write the day.

- Merge what repeats. Two conversations about the same servo are one line, not two.
- Keep a contradiction visible: if the evening disagrees with the morning, say both and say which is later.
- Carry every unresolved "buddy owes" line forward. A debt only leaves when it is discharged, and nothing
  in these notes discharges it unless a later note says so.
- "index" is one line for the semantic layer: what is true now about the owner or about you, as a result of
  today. At most two. Write them as standing facts, not as events.
- "star" proposes something for the permanent layer. At most one, usually none. You propose; the owner
  decides.

Output JSON only:
{"slug":"three-or-four-word-kebab-case",
 "title":"one line, no trailing punctuation",
 "happened":["what the day was, at most five lines"],
 "learned":["what you now know about your owner, at most three"],
 "owes":["still outstanding, verbatim from the notes where possible"],
 "index":["standing facts, at most two"],
 "star":["at most one"]}"""

# Asked for in the prompt above, enforced here. `json_object` cannot be used for
# either call: it requires the word "json" in the *input*, and the input is a
# transcript of what the owner said. Live probe, 2026-09-11: 400 every time.
_LIST = {"type": "array", "items": {"type": "string"}}


def _schema(**fields: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": fields, "required": sorted(fields)}


DISTIL_SCHEMA = _schema(title={"type": "string"}, said=_LIST, open=_LIST, owes=_LIST,
                        star=_LIST, nothing={"type": "boolean"})
CURATE_SCHEMA = _schema(slug={"type": "string"}, title={"type": "string"}, happened=_LIST,
                        learned=_LIST, owes=_LIST, index=_LIST, star=_LIST)


class ChatClient(Protocol):
    def distil(self, conversation: str) -> str: ...
    def curate(self, notes: str) -> str: ...


class OpenAIChatClient:
    """Two text-only Responses calls, JSON out. Same shape as diary.py's client."""

    def __init__(self, model: str, api_key: Optional[str] = None) -> None:
        import openai

        self.model = model
        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()

    def _json_call(self, instructions: str, body: str, max_tokens: int,
                   name: str, schema: dict) -> str:
        resp = self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=body,
            text={"format": {"type": "json_schema", "name": name, "strict": True,
                             "schema": schema}},
            max_output_tokens=max_tokens,
            reasoning={"effort": "low"},
        )
        text = (resp.output_text or "").strip()
        if not text:
            raise RuntimeError("empty reply")
        return text

    def distil(self, conversation: str) -> str:
        return self._json_call(DISTIL_PROMPT, conversation, 900, "distil", DISTIL_SCHEMA)

    def curate(self, notes: str) -> str:
        return self._json_call(CURATE_PROMPT, notes, 1400, "day", CURATE_SCHEMA)


def make_chat_client(model: str = DEFAULT_MODEL, environ: Any = None) -> Optional[ChatClient]:
    env = os.environ if environ is None else environ
    model = (env.get("CC_BUDDY_CHAT_MEMORY_MODEL") or model).strip() or DEFAULT_MODEL
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        log.warning("chat memory: OPENAI_API_KEY not set — buddy will talk but remember nothing it said")
        return None
    try:
        return OpenAIChatClient(model, api_key=key)
    except ImportError as e:
        log.warning("chat memory: openai SDK not importable (%s) — nothing will be remembered", e)
        return None


# ---- rendering a note ------------------------------------------------------------------

def _lines(value: Any, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:cap]:
        text = str(item or "").strip().lstrip("-*").strip()
        if text and len(text) > 3:
            out.append(text)
    return out


def _slug(text: str, fallback: str = "a-conversation") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    s = "-".join([w for w in s.split("-") if w][:5])
    return s or fallback


def render_note(distilled: dict[str, Any], when: datetime, session_id: str) -> str:
    """One conversation as a machine draft, in the store's own shape."""
    title = str(distilled.get("title") or "A conversation").strip().rstrip(".")
    said = _lines(distilled.get("said"), 4)
    open_threads = _lines(distilled.get("open"), 3)
    owes = [o if o.lower().startswith("buddy owes") else f"buddy owes {o}"
            for o in _lines(distilled.get("owes"), 3)]
    stars = _lines(distilled.get("star"), 1)

    parts = [
        "---",
        f"status: {DRAFT_STATUS}",
        f"source: {SOURCE}",
        f"session_id: {session_id}",
        f"ended: {when:%Y-%m-%d %H:%M}",
        "---",
        "",
        "> **Unverified.** buddy wrote this to itself right after talking. It has not been checked",
        "> against anything.",
        "",
        f"# {title}",
        "",
        "## What was said",
        "",
    ]
    parts.extend([f"- {s}" for s in said] or ["- (nothing worth keeping)"])
    parts.extend(["", "## Open threads", ""])
    both = owes + open_threads
    parts.extend([f"- {s}" for s in both] or ["- (none)"])
    if stars:
        parts.extend(["", "## Proposed for the permanent layer", ""])
        parts.extend([f"- {CANDIDATE_MARK} {s}" for s in stars])
    parts.append("")
    return "\n".join(parts)


def note_path(cfg: RecallConfig, when: datetime, session_id: str) -> Path:
    stem = session_id.split("-")[0][:8] or "session"
    return cfg.sessions_dir / f"{when:%Y-%m-%d}" / f"{when:%H%M}-{stem}.md"


def write_note(cfg: RecallConfig, text: str, when: datetime, session_id: str) -> Optional[Path]:
    """Write one draft, privately. Returns the path, or None on any failure."""
    try:
        p = note_path(cfg, when, session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        for d in (cfg.store, cfg.sessions_dir, p.parent):
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        p.write_text(text, encoding="utf-8")
        return p
    except OSError as e:
        log.warning("chat memory: could not write the note: %s", e)
        return None


# ---- the day pass ----------------------------------------------------------------------

def drafts_for_day(cfg: RecallConfig, day: str) -> list[Path]:
    d = cfg.sessions_dir / day
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".md")


def days_with_drafts(cfg: RecallConfig) -> list[str]:
    if not cfg.sessions_dir.is_dir():
        return []
    out = []
    for d in sorted(cfg.sessions_dir.iterdir()):
        if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name) and drafts_for_day(cfg, d.name):
            out.append(d.name)
    return out


def daily_exists(cfg: RecallConfig, day: str) -> bool:
    if not cfg.store.is_dir():
        return False
    return any(p.name.startswith(day + "-") and p.suffix == ".md" for p in cfg.store.iterdir())


def render_daily(curated: dict[str, Any], day: str) -> str:
    title = str(curated.get("title") or "A day of talking").strip().rstrip(".")
    parts = [
        "---",
        "status: curated",
        f"source: {SOURCE}",
        "curated_by: buddy",
        f"day: {day}",
        "---",
        "",
        f"# {title}",
        "",
        "## What happened",
        "",
    ]
    parts.extend([f"- {s}" for s in _lines(curated.get("happened"), 5)] or ["- (a quiet day)"])
    learned = _lines(curated.get("learned"), 3)
    if learned:
        parts.extend(["", "## What I know now", ""])
        parts.extend([f"- {s}" for s in learned])
    owes = _lines(curated.get("owes"), 5)
    if owes:
        parts.extend(["", "## Open threads", ""])
        parts.extend([f"- {s}" for s in owes])
    stars = _lines(curated.get("star"), 1)
    if stars:
        parts.extend(["", "## Proposed for the permanent layer", "",
                      "<!-- buddy proposes; only the owner promotes, with /debrief highlight -->"])
        parts.extend([f"- {CANDIDATE_MARK} {s}" for s in stars])
    parts.append("")
    return "\n".join(parts)


def append_index(cfg: RecallConfig, day: str, title: str, facts: Sequence[str], filename: str) -> None:
    """One row in the semantic layer.

    Appended, not rewired. Rewiring INDEX.md is a rewrite of the owner's own file
    by a model, and a bad rewrite loses what a whole month learned — so buddy adds
    and the owner prunes.
    """
    try:
        path = cfg.store / "INDEX.md"
        row = f"| {day} | [{title}]({filename}) | " + ("; ".join(facts) if facts else "—") + " |\n"
        header = ("\n## buddy's conversations\n\n"
                  "| Day | Record | What it established |\n|---|---|---|\n")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# INDEX\n"
        if "## buddy's conversations" not in existing:
            existing = existing.rstrip("\n") + "\n" + header
        path.write_text(existing.rstrip("\n") + "\n" + row, encoding="utf-8")
    except OSError as e:
        log.warning("chat memory: could not update the index: %s", e)


# ---- the starred layer -----------------------------------------------------------------

def star(cfg: RecallConfig, text: str, when: Optional[datetime] = None) -> Optional[str]:
    """Promote one claim to the permanent layer, because the owner said so out loud.

    The rule the layered design turns on is that a human promotes and an agent only
    proposes. The owner's voice IS the human, so "remember that" is a promotion —
    it is the owner deciding, spoken instead of typed (owner instruction
    2026-09-11: starring goes through the voice, nothing through the terminal).

    Append-only, never pruned, and deduplicated so saying it twice does not write
    it twice. Returns the line as stored, or None when there was nothing to keep.
    """
    claim = " ".join(str(text or "").split()).strip(" .,;:").strip()
    if len(claim) < 4:
        return None
    claim = claim[0].upper() + claim[1:] if claim[0].islower() else claim
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    line = f"- ★ {claim} ({stamp})"
    try:
        cfg.store.mkdir(parents=True, exist_ok=True)
        path = cfg.store / "HIGHLIGHTS.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# HIGHLIGHTS\n"
        lowered = claim.lower()
        for row in _stars_from(existing, 200):
            if lowered in row.lower():
                return claim          # already remembered forever; saying it twice is not two facts
        if "## From talking" not in existing:
            existing = existing.rstrip("\n") + "\n\n## From talking\n\n"
        path.write_text(existing.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
        log.info("chat memory: starred %r", claim[:80])
        return claim
    except OSError as e:
        log.warning("chat memory: could not star it: %s", e)
        return None


def _stars_from(text: str, limit: int) -> list[str]:
    """★ lines from buddy's own section only, newest last.

    Scoped to the section `star()` writes rather than scanning the file, because a
    fresh `era-debrief install` seeds HIGHLIGHTS.md with a worked example that
    carries ★ lines of its own — about somebody else's outage, not about the
    owner. Filtering on the example marker was not enough: not every seeded entry
    carries one, and the first live read put a Cloud Armor rule on the widget as
    buddy's memory of its owner (2026-09-11). Reading only buddy's own section
    makes that impossible rather than unlikely.
    """
    out: list[str] = []
    inside = False
    for row in text.splitlines():
        stripped = row.strip()
        if stripped.startswith("#"):
            inside = stripped.lstrip("# ").lower().startswith(OWN_SECTION_TITLE)
            continue
        if not inside:
            continue
        line = stripped.lstrip("-").strip()
        if line.startswith("★"):
            claim = line[1:].strip()
            if claim and EXAMPLE_MARK not in claim:
                out.append(claim)
    return out[-limit:]


def stars(cfg: RecallConfig, limit: int = 12) -> list[str]:
    """The starred claims, newest last. Used by the widget and the opening brief."""
    try:
        return _stars_from((cfg.store / "HIGHLIGHTS.md").read_text(encoding="utf-8"), limit)
    except OSError:
        return []


# ---- the async side --------------------------------------------------------------------

@dataclass
class ChatMemoryStats:
    notes: int = 0
    days: int = 0
    failed: int = 0


class ChatMemory:
    """Writes a note per conversation, and curates its own days.

    Both jobs run in a worker thread, off the event loop, because both are one
    blocking model call. Neither ever delays the board, a reply, or the next wake
    word: the conversation is over by the time either starts.
    """

    def __init__(
        self,
        cfg: RecallConfig,
        client: Optional[ChatClient],
        wall: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.wall = wall
        self.stats = ChatMemoryStats()
        self._pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._busy = False

    def stop(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def _executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                               thread_name_prefix="chat-memory")
        return self._pool

    # -- one conversation --

    @staticmethod
    def transcript_text(turns: Sequence[tuple[str, str]]) -> str:
        kept = list(turns)[-MAX_TURNS_SENT:]
        out = []
        for who, text in kept:
            speaker = "OWNER" if who == "user" else "BUDDY"
            body = " ".join(str(text or "").split())[:MAX_CHARS_PER_TURN]
            if body:
                out.append(f"{speaker}: {body}")
        return "\n".join(out)

    async def remember(self, turns: Sequence[tuple[str, str]], session_id: str = "") -> Optional[Path]:
        """Distil one finished conversation into a draft. None when nothing was kept."""
        if self.client is None:
            return None
        body = self.transcript_text(turns)
        owner_said = sum(1 for who, t in turns if who == "user" and str(t).strip())
        if owner_said < 1 or len(body) < 40:
            return None                      # a wake word and a goodbye is not a conversation
        loop = asyncio.get_running_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(self._executor(), self.client.distil, body),
                timeout=DISTIL_TIMEOUT_SECS)
            distilled = json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            self.stats.failed += 1
            log.warning("chat memory: could not distil the conversation (%s: %s)", type(e).__name__, e)
            return None
        if not isinstance(distilled, dict):
            self.stats.failed += 1
            return None
        if distilled.get("nothing") is True and not _lines(distilled.get("owes"), 3):
            log.info("chat memory: nothing worth keeping from that one")
            return None
        when = self.wall()
        path = write_note(self.cfg, render_note(distilled, when, session_id or "session"), when,
                          session_id or "session")
        if path is not None:
            self.stats.notes += 1
            log.info("chat memory: wrote %s", path.name)
        return path

    # -- the day pass, automatic --

    def due_days(self, now: Optional[datetime] = None) -> list[str]:
        """Days that have drafts and no day record yet, excluding today.

        Today is excluded because the day is not over: curating at noon would
        write a record that the afternoon contradicts.
        """
        when = now or self.wall()
        today = when.strftime("%Y-%m-%d")
        return [d for d in days_with_drafts(self.cfg)
                if d != today and not daily_exists(self.cfg, d)]

    async def curate_day(self, day: str) -> Optional[Path]:
        """Aggregate one day's drafts into a day record, and index it."""
        if self.client is None or self._busy:
            return None
        drafts = drafts_for_day(self.cfg, day)
        if not drafts:
            return None
        body_parts = []
        for p in drafts:
            try:
                body_parts.append(f"--- note: {p.name}\n{p.read_text(encoding='utf-8')}")
            except OSError:
                continue
        if not body_parts:
            return None
        self._busy = True
        loop = asyncio.get_running_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(self._executor(), self.client.curate, "\n\n".join(body_parts)),
                timeout=CURATE_TIMEOUT_SECS)
            curated = json.loads(raw)
            if not isinstance(curated, dict):
                raise ValueError("not an object")
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            self.stats.failed += 1
            log.warning("chat memory: could not curate %s (%s: %s)", day, type(e).__name__, e)
            return None
        finally:
            self._busy = False
        title = str(curated.get("title") or "A day of talking").strip().rstrip(".")
        name = f"{day}-{_slug(curated.get('slug') or title)}.md"
        try:
            self.cfg.store.mkdir(parents=True, exist_ok=True)
            (self.cfg.store / name).write_text(render_daily(curated, day), encoding="utf-8")
        except OSError as e:
            self.stats.failed += 1
            log.warning("chat memory: could not write the day record: %s", e)
            return None
        append_index(self.cfg, day, title, _lines(curated.get("index"), 2), name)
        self.stats.days += 1
        log.info("chat memory: curated %s from %d conversation(s) → %s", day, len(drafts), name)
        return self.cfg.store / name

    async def curate_loop(self, shutdown: "asyncio.Event", interval_secs: float = 1800.0) -> None:
        """Check for an uncurated day every half hour. buddy's own day pass.

        A loop rather than a nightly alarm because the daemon is not guaranteed to
        be awake at any particular hour, and a day that was missed should still be
        written the next time the machine is on.
        """
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval_secs)
                return
            except asyncio.TimeoutError:
                pass
            for day in self.due_days():
                if shutdown.is_set():
                    return
                await self.curate_day(day)
                await asyncio.sleep(1.0)
