"""What buddy remembers of talking with you.

buddy already remembers what it SEES: diary.py keeps a memory stream of looks, a
profile it rewrites nightly, and a starred layer only the owner may add to. It
remembered nothing of what was SAID. Every conversation opened from nothing, and
that is what made it feel like a stranger each time (owner request 2026-09-11).

This module is the read half of the fix, and it is deliberately the cheapest
thing that works.

### Where the memory lives

A **claude-debrief store** of its own, at ``~/.config/cc-buddy-bridge/debrief``.
That is the owner's own layered-memory system, already on this machine, and it is
buddy's store for SPOKEN conversations — not for Claude Code sessions, which keep
their own stores in their own repos. Three layers, unchanged from the design the
owner wrote:

    sessions/<date>/<time>-<id>.md   episodic, one per conversation, immutable
    <date>-<slug>.md                 the day, aggregated
    INDEX.md                         what is true now, rewired not appended
    HIGHLIGHTS.md                    the starred layer, never pruned

### Why the opening prompt does not search

Measured on this machine, 2026-09-11: a semantic search over this store costs
212 ms cold and 57 ms warm. Reading the newest notes off disk costs 0.13 ms. The
speed is not the point — correctness is. Asked "what did we talk about
yesterday", the semantic search returned a note about opening lines, because
similarity has no clock. A conversation opens on a recency question, so this
module answers it by reading the newest thing, and search is left to a
mid-conversation tool where a second is affordable and the question is genuinely
about meaning.

### The two rules that keep it from being creepy

1. **At most one clause.** `opening_brief` returns one short phrase or nothing.
   A robot that recites a dossier at you is worse company than one that forgot.
2. **What buddy said is quotable; what buddy saw is not.** This module reads only
   the spoken store. The camera's memory reaches a conversation through its own
   tool, marked as something buddy thinks it saw, never as fact.

Silence is a valid answer, and it is the right one on the first ever
conversation: an empty store returns "", the instructions are byte-identical to
before, and buddy greets exactly as it always did. It never announces that it has
no memories — that is a robot narrating its own plumbing.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_STORE = "~/.config/cc-buddy-bridge/debrief"
DEFAULT_NOTES = "~/.config/cc-buddy-bridge/notes"
STAMP_FILE = "last_conversation"
# A fresh `era-debrief install` seeds INDEX.md and HIGHLIGHTS.md with a worked
# example so a clone renders something. Those lines are somebody else's fiction
# about payments and idempotency keys, and buddy must never quote them back as a
# memory of its owner. Both markers are checked, because the banner sits at the
# top of a file and the mark sits on individual rows.
TEMPLATE_BANNER = "> **Template.**"
EXAMPLE_MARK = "*(example)*"
# buddy's own notes carry this in their frontmatter. It is what lets buddy quote
# itself the next morning without waiting for anything to be curated.
SOURCE_MARK = "source: buddy-voice"
MAX_BRIEF_CHARS = 240
# A debt of buddy's own is the best thing it can open with, so it is preferred
# over any other open thread.
DEBT_PREFIX = "buddy owes"
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_DAILY = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")


@dataclass(frozen=True)
class RecallConfig:
    store: Path
    notes: Path

    @property
    def stamp_path(self) -> Path:
        return self.notes / STAMP_FILE

    @property
    def sessions_dir(self) -> Path:
        return self.store / "sessions"

    # The simplified memory (owner, 2026-09-23): three stores and an archive under one root.
    @property
    def transcripts_dir(self) -> Path:
        return self.store / "transcripts"

    @property
    def records_dir(self) -> Path:
        return self.store / "records"

    @property
    def mem0_dir(self) -> Path:
        return self.store / "mem0"

    @property
    def archive_dir(self) -> Path:
        return self.store / "archive"


def configured(environ: Any = None) -> RecallConfig:
    env = os.environ if environ is None else environ
    store = (env.get("CC_BUDDY_DEBRIEF_DIR") or "").strip() or DEFAULT_STORE
    notes = (env.get("CC_BUDDY_NOTES_DIR") or "").strip() or DEFAULT_NOTES
    return RecallConfig(store=Path(store).expanduser(), notes=Path(notes).expanduser())


# ---- the stamp: when buddy last talked with its owner ---------------------------------

def note_conversation_time(cfg: RecallConfig, now: Optional[datetime] = None) -> None:
    """Record that a conversation just happened. One integer, written last.

    Failure is swallowed: a robot that cannot write a timestamp should still
    have had the conversation.
    """
    when = now or datetime.now()
    try:
        cfg.notes.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(cfg.notes, 0o700)
        except OSError:
            pass
        cfg.stamp_path.write_text(f"{int(when.timestamp())}\n", encoding="utf-8")
    except OSError as e:
        log.warning("recall: could not record the conversation time: %s", e)


def _gap_clause(cfg: RecallConfig, now: datetime) -> str:
    """"earlier today" / "yesterday" / "N days ago", or "" when unknowable.

    A clock that has gone backwards, a corrupt file and a missing file are all
    the same answer: say nothing about time.
    """
    try:
        raw = cfg.stamp_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        then = datetime.fromtimestamp(int(float(raw)))
    except (ValueError, OverflowError, OSError):
        return ""
    delta = now - then
    secs = delta.total_seconds()
    if secs < 0:
        return ""                       # the stamp is in the future: say nothing
    days = (now.date() - then.date()).days
    if days <= 0:
        return "" if secs < 900 else "earlier today"
    if days == 1:
        return "yesterday"
    if days <= 6:
        return f"{days} days ago"
    if days <= 13:
        return "last week"
    return "a while ago"


# ---- the one thing worth carrying over -------------------------------------------------

def _usable(text: str) -> bool:
    if not text or EXAMPLE_MARK in text:
        return False
    stripped = text.strip()
    if len(stripped) < 8 or stripped.endswith("?"):
        return False
    # A bullet that is only a link, a path or a bare identifier is not a memory.
    return bool(re.search(r"[a-zA-Z]{3,}\s+[a-zA-Z]{2,}", stripped))


def _bullets_under(text: str, heading_words: tuple[str, ...]) -> list[str]:
    """Bullets under the first heading whose words match, in file order."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        head = _HEADING.match(line)
        if head is not None:
            low = head.group(1).lower()
            inside = any(w in low for w in heading_words)
            continue
        if not inside:
            continue
        b = _BULLET.match(line)
        if b is not None:
            out.append(b.group(1))
    return out


def _pick_bullet(text: str) -> str:
    """A debt of buddy's own first, then any open thread."""
    bullets = [b for b in _bullets_under(text, ("open thread", "open loop", "next", "owe"))
               if _usable(b)]
    for b in bullets:
        if b.lower().startswith(DEBT_PREFIX):
            return b
    return bullets[0] if bullets else ""


def _newest_buddy_note(cfg: RecallConfig) -> str:
    """The newest conversation note buddy wrote itself, as text. "" when none."""
    d = cfg.sessions_dir
    if not d.is_dir():
        return ""
    files: list[Path] = []
    for day in d.iterdir():
        if day.is_dir():
            files.extend(p for p in day.iterdir() if p.suffix == ".md")
        elif day.suffix == ".md":
            files.append(day)
    for p in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if SOURCE_MARK in text and TEMPLATE_BANNER not in text:
            return text
    return ""


def _newest_daily(cfg: RecallConfig) -> str:
    """The newest aggregated day at the store root, as text. "" when none."""
    if not cfg.store.is_dir():
        return ""
    dailies = [p for p in cfg.store.iterdir() if p.is_file() and _DAILY.match(p.name)]
    for p in sorted(dailies, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if TEMPLATE_BANNER not in text:
            return text
    return ""


def opening_brief(cfg: RecallConfig, now: Optional[datetime] = None) -> str:
    """At most two clauses for the top of a conversation, or "".

    The gap comes first because it is what a person notices. Then one carried-over
    thing, preferring something buddy owes the owner. Never two memory clauses,
    never a question, never longer than MAX_BRIEF_CHARS.
    """
    when = now or datetime.now()
    try:
        gap = _gap_clause(cfg, when)
        carried = _pick_bullet(_newest_buddy_note(cfg)) or _pick_bullet(_newest_daily(cfg))
    except OSError as e:                              # pragma: no cover - defensive
        log.debug("recall: brief unavailable (%s)", e)
        return ""
    parts = []
    if gap:
        parts.append(f"You last talked {gap}.")
    if carried:
        parts.append(f"Still open from then: {carried}")
    brief = " ".join(parts).strip()
    if len(brief) > MAX_BRIEF_CHARS:
        brief = brief[:MAX_BRIEF_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return brief
