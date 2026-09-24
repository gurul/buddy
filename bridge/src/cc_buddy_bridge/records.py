"""Records: what buddy knows about its owner, as files the owner can read and edit.

recall.py answers "when did we last talk and what about" in one clause, which is
right for a wake-word conversation. A text chat is different: the owner is not in
the room, the questions run longer, and "what was the name of that restaurant"
needs a lookup rather than a greeting. This is that layer. It follows the shape of
the memory Instinct (the iMessage assistant) was found to use — reverse-engineered
by Dhravya Shah, 2026-09-20 — because that shape has one property the owner cares
about: **the agent never writes its own memory.**

    <debrief>/records/<id>.md      one typed record per thing: a preference, a person,
                                   a project, a place. Frontmatter: id, type, aliases.
                                   Body: dated facts, and [[id]] links to other records.
    <debrief>/records/profile.md   the one-pager every text turn starts with: life
                                   context, how much to ask before acting, how to talk,
                                   and an index of the records with their aliases.
    git                            the whole debrief store is a repository; every
                                   reconcile is one commit, so an old fact is in history
                                   and a wrong edit is one revert away.

Three rules, all code:

1. **The agent reads; it never writes.** The text brain gets ``memory_search`` and
   ``memory_get`` — a keyword search over ids, aliases and fact lines, no vectors —
   and nothing else. What it learns in a chat reaches the records only through the
   nightly reconcile, from the distilled session notes chat_memory.py already
   writes. A robot promoting its own conclusions mid-conversation is exactly the
   laundering the layered store exists to prevent.
2. **Reconcile once a day, from the day's notes, into full records.** The model is
   shown the current records and the day's notes and returns the records that
   change, whole: it can shorten, merge, turn examples into traits, and replace a
   wrong fact with a dated correction. Owner edits survive because the current
   file is always the input. Nothing is deleted from history: git keeps it.
3. **Aliases are the index.** Search is grep, so every record carries the words the
   owner might use for it. The profile lists them, so the agent knows what it can
   look up before it looks.

It ships OFF (``RECORDS_DEFAULT``): reconciling is one model call a day, and the
records dir is created only once the switch is on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .chat_memory import stars as starred_claims
from .recall import RecallConfig

log = logging.getLogger(__name__)

RECORDS_DEFAULT = False
DEFAULT_MODEL = "gpt-5.4-nano"            # the same model chat_memory.py distils and curates with
RECONCILE_TIMEOUT_SECS = 120.0
TYPES = ("preference", "person", "organization", "project", "place", "routine", "conversation")
PROFILE_ID = "profile"
MAX_PROFILE_CHARS = 6000                   # ~1.5k tokens: the one-pager stays one page
MAX_STARS = 40                             # the owner's "remember that …" lines a reconcile reads
MAX_RECORD_CHARS = 4000
MAX_HITS = 8
MAX_HIT_CHARS = 200
STATE_FILE = ".reconciled"                 # one day per line: what the reconciler has already read
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LINK = re.compile(r"\[\[([a-z0-9-]+)\]\]")
_WORD = re.compile(r"[a-z0-9]+")

MEMORY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function", "name": "memory_search", "strict": True,
        "description": "Search what you know about your owner: a keyword search over your memory records "
                       "(preferences, people, projects, places). Returns matching lines with their record id "
                       "and date. Use it before answering anything about their life, taste or plans.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                       "properties": {"query": {"type": "string",
                                                "description": "A few keywords, the way the owner would say it."}}},
    },
    {
        "type": "function", "name": "memory_get", "strict": True,
        "description": "Read one whole memory record by id (ids are listed in your profile and in search results).",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["id"],
                       "properties": {"id": {"type": "string"}}},
    },
]


# ---- records on disk --------------------------------------------------------------------------

@dataclass
class Record:
    id: str
    type: str
    aliases: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    updated: str = ""

    @property
    def links(self) -> list[str]:
        seen: list[str] = []
        for fact in self.facts:
            for target in _LINK.findall(fact):
                if target not in seen and target != self.id:
                    seen.append(target)
        return seen


def parse_record(text: str) -> Optional[Record]:
    """A record file → Record, or None when it is not one. Tolerant: the owner edits these by hand."""
    if not text.startswith("---"):
        return None
    m = re.match(r"---\n(.*?)\n---[ \t]*(?:\n|$)(.*)", text, re.S)
    if m is None:
        return None
    head, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip()
    rid = meta.get("id", "")
    if not _ID.match(rid):
        return None
    rtype = meta.get("type", "").strip().lower() or "preference"
    raw_aliases = meta.get("aliases", "").strip().strip("[]")
    aliases = [a.strip().strip("\"'") for a in raw_aliases.split(",") if a.strip().strip("\"'")]
    facts = [m.group(1).strip() for m in (re.match(r"^\s*[-*]\s+(.+?)\s*$", ln) for ln in body.splitlines()) if m]
    return Record(id=rid, type=rtype, aliases=aliases, facts=facts, updated=meta.get("updated", ""))


def render_record(rec: Record) -> str:
    aliases = ", ".join(rec.aliases)
    lines = ["---", f"id: {rec.id}", f"type: {rec.type}", f"aliases: [{aliases}]"]
    if rec.updated:
        lines.append(f"updated: {rec.updated}")
    lines.append("---")
    lines.extend(f"- {fact}" for fact in rec.facts)
    return "\n".join(lines) + "\n"


def records_dir(cfg: RecallConfig) -> Path:
    return cfg.store / "records"


def load_records(cfg: RecallConfig) -> dict[str, Record]:
    out: dict[str, Record] = {}
    folder = records_dir(cfg)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.md")):
        if path.stem == PROFILE_ID:
            continue
        try:
            rec = parse_record(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if rec is not None and rec.id == path.stem:
            out[rec.id] = rec
    return out


# ---- what the agent may do: search and read ---------------------------------------------------

def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def search(records: dict[str, Record], query: str, limit: int = MAX_HITS) -> list[dict[str, Any]]:
    """Keyword search, no vectors. A hit is one fact line. Score: query words found in the record's id or
    aliases count three (that is what aliases are for), words in the line itself count one."""
    words = [w for w in _tokens(query) if len(w) > 1]
    if not words:
        return []
    hits: list[tuple[int, str, str]] = []
    for rec in records.values():
        name_words = set(_tokens(rec.id + " " + " ".join(rec.aliases)))
        name_score = 3 * sum(1 for w in words if w in name_words or any(w in n for n in name_words if len(w) > 3))
        for fact in rec.facts:
            fact_words = set(_tokens(fact))
            score = name_score + sum(1 for w in words if w in fact_words)
            if score > 0:
                hits.append((score, rec.id, fact))
    hits.sort(key=lambda h: (-h[0], h[1]))
    return [{"id": rid, "line": fact[:MAX_HIT_CHARS]} for _, rid, fact in hits[:limit]]


def get(records: dict[str, Record], rid: str) -> Optional[str]:
    rec = records.get(rid.strip().lower())
    return render_record(rec)[:MAX_RECORD_CHARS] if rec is not None else None


def render_index(records: dict[str, Record]) -> str:
    """The part of the profile that says what can be looked up."""
    if not records:
        return ""
    rows = [f"- {rec.id} ({rec.type}): {', '.join(rec.aliases[:6])}" if rec.aliases else f"- {rec.id} ({rec.type})"
            for rec in records.values()]
    return "Records you can search or read by id:\n" + "\n".join(rows)


_STAR_DATE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)\s*$")
_UPDATED = re.compile(r"^updated:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)


def owner_stars(cfg: RecallConfig) -> list[str]:
    """What the owner said to remember for good ("remember that …"), their words, oldest first."""
    try:
        return starred_claims(cfg, MAX_STARS)
    except Exception:  # noqa: BLE001 - a missing or unreadable HIGHLIGHTS.md is just no stars
        return []


def _stars_after(stars: list[str], day: str) -> list[str]:
    """Stars dated after `day`: the ones no reconcile has read yet. An undated star counts as new."""
    out = []
    for claim in stars:
        m = _STAR_DATE.search(claim)
        if m is None or not day or m.group(1) > day:
            out.append(claim)
    return out


def profile(cfg: RecallConfig) -> str:
    """The one-pager, bounded, for the start of a turn. "" when there is none yet.

    A star is the owner's own "remember that …", and it has to count on the next message, not after
    tonight's reconcile. So stars the profile has not absorbed yet (dated after its `updated:`) go on
    top, verbatim. With no profile yet, the stars alone are the page."""
    path = records_dir(cfg) / f"{PROFILE_ID}.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    m = _UPDATED.search(text)
    fresh = _stars_after(owner_stars(cfg), m.group(1) if m else "")
    if fresh:
        block = "## They asked you to remember (since the page below was written)\n" + \
                "\n".join(f"- {c}" for c in fresh)
        text = block + ("\n\n" + text if text else "")
    if len(text) > MAX_PROFILE_CHARS:
        text = text[:MAX_PROFILE_CHARS].rsplit("\n", 1)[0] + "\n…"
    return text


class RecordsReader:
    """What the text brain is lent: read-only, re-read from disk per call (the owner may have edited)."""

    def __init__(self, cfg: RecallConfig) -> None:
        self.cfg = cfg

    def profile(self) -> str:
        return profile(self.cfg)

    def search(self, query: str) -> dict[str, Any]:
        hits = search(load_records(self.cfg), query)
        return {"ok": True, "hits": hits} if hits else {"ok": True, "hits": [], "note": "nothing matched"}

    def get(self, rid: str) -> dict[str, Any]:
        text = get(load_records(self.cfg), rid)
        return {"ok": True, "record": text} if text else {"ok": False, "reason": f"no record {rid!r}"}


# ---- git: history is how nothing is lost -------------------------------------------------------

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)


def ensure_repo(store: Path) -> bool:
    """The debrief store as a git repository. False (with one log line) when git is not there."""
    if shutil.which("git") is None:
        log.warning("records: git is not installed — records will be kept, but without history")
        return False
    try:
        top = _git(store, "rev-parse", "--show-toplevel")
        if top.returncode == 0:
            if Path(top.stdout.strip()).resolve() == store.resolve():
                seal(store)                   # an existing store repository gets the same lock, every start
            return True
        r = _git(store, "init", "-q")
        if r.returncode != 0:
            log.warning("records: git init failed: %s", r.stderr.strip())
            return False
        # A repository nobody has to configure: commits are buddy's, on this machine only.
        _git(store, "config", "user.name", "buddy")
        _git(store, "config", "user.email", "buddy@localhost")
        seal(store)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("records: git unavailable (%s)", e)
        return False


# Every URL shape git can push to, rewritten to one it cannot. A second lock beside the pre-push hook,
# because `git push --no-verify` skips hooks but never skips pushInsteadOf.
_PUSH_PREFIXES = ("https://", "http://", "ssh://", "git://", "git@", "file://", "/", "~", ".")
_NO_PUSH_URL = "local-only-never-pushed:"
_PRE_PUSH = """#!/bin/sh
# buddy's memory lives on this computer only (records.py seal). Pushing it anywhere is refused.
echo "buddy memory is local-only: push refused" >&2
exit 1
"""


def seal(store: Path) -> None:
    """Lock the store's repository to this computer: no remote, no push, ever.

    The owner's memory is private (owner instruction, 2026-09-23: "nothing should leak out ever"). The
    history exists so a bad reconcile is one revert away, not so it can go anywhere. So on every start any
    remote is removed, every push URL is rewritten to one that cannot resolve, and a pre-push hook refuses
    — three independent locks, so none of them failing alone lets it out."""
    for name in _git(store, "remote").stdout.split():
        _git(store, "remote", "remove", name)
        log.warning("records: removed git remote %r from the memory store — it stays on this computer", name)
    for prefix in _PUSH_PREFIXES:
        _git(store, "config", "--replace-all", f"url.{_NO_PUSH_URL}.pushInsteadOf", prefix, f"^{re.escape(prefix)}$")
    hooks = store / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(_PRE_PUSH, encoding="utf-8")
    (hooks / "pre-push").chmod(0o755)
    _git(store, "config", "core.hooksPath", str(hooks))   # a global hooksPath must not bypass it


def commit(store: Path, message: str) -> Optional[str]:
    """Commit everything under the store. The short hash, or None when there was nothing or git failed."""
    try:
        top = _git(store, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return None
        # Forced, because the debrief installer drops a `*` .gitignore in the store (it keeps memory out of a
        # project's PRs), which made every add stage nothing and every reconcile go without history. Only in
        # the store's own repository: were the store inside some other repository, forcing would put the
        # owner's memory in it, so then nothing is committed at all.
        if Path(top.stdout.strip()).resolve() != store.resolve():
            log.warning("records: %s is inside another git repository — not committing to it", store)
            return None
        _git(store, "add", "-A", "--force", ".")
        if _git(store, "diff", "--cached", "--quiet").returncode == 0:
            return None
        r = _git(store, "commit", "-q", "-m", message)
        if r.returncode != 0:
            log.warning("records: commit failed: %s", r.stderr.strip())
            return None
        return _git(store, "rev-parse", "--short", "HEAD").stdout.strip() or None
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("records: commit failed (%s)", e)
        return None


# ---- the reconcile: the only writer -------------------------------------------------------------

RECONCILE_PROMPT = """You are buddy, a small desk robot, keeping the records of what you know about your owner.
You are shown the records as they are now and your notes from one day of conversations. Return the records
that should change, whole, and the profile as it should read now.

Records are facts about durable things: a preference, a person, an organization, a project, a place, a
routine. One record per thing, id in lowercase-with-hyphens, with aliases: every word the owner might use
for it. Each fact is one line, stated as fact, with its date: "Loves pasta (said 2026-09-15)." Link related
records with [[id]] inside a fact. Keep records short: merge examples into traits, drop incidental detail,
move what belongs to a project into that project's record. Never delete a fact silently — when a fact is
wrong now, replace it with the correction and its date: "Now prefers the 9 am slot (changed 2026-09-20;
was 8 am)." Anything the owner said to forget is removed, and that is the only removal. Only what the owner
said is a fact about them; what the notes say buddy did is not. Return only records that change or are new.

The profile is the one page you read before every conversation. Three short sections, plain prose or a few
bullets each: "Life context" (their name first, when you know it, then who they are, what is going on, the people and projects that matter now),
"Acting on their behalf" (what they want done without asking and what needs a yes first, from what they
have said), "How they like to talk" (tone, length, what annoys them). Dates where they matter. Nothing
that is not in the records or the notes.

You may also be shown what the owner said to remember for good, in their words, each with its date. Those
are the most reliable facts you have: fold every real fact about them (their name, the people and things
they care about) into the records and the profile, and keep it there. A line that is not a fact about
them — a stray question, a half sentence the microphone caught — you leave out."""

_LIST = {"type": "array", "items": {"type": "string"}}
RECONCILE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["records", "forget", "profile"],
    "properties": {
        "records": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "type", "aliases", "facts"],
            "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": list(TYPES)},
                           "aliases": _LIST, "facts": _LIST}}},
        "forget": {"type": "array", "items": {"type": "string"},
                   "description": "ids of records the owner asked to forget entirely"},
        "profile": {"type": "object", "additionalProperties": False,
                    "required": ["life_context", "acting", "talking"],
                    "properties": {"life_context": _LIST, "acting": _LIST, "talking": _LIST}},
    },
}


def render_profile(prof: dict[str, Any], records: dict[str, Record], day: str) -> str:
    def section(title: str, key: str) -> str:
        lines = [str(x).strip() for x in (prof.get(key) or []) if str(x).strip()]
        return f"## {title}\n" + ("\n".join(f"- {ln}" for ln in lines) if lines else "- (nothing yet)")

    parts = [f"---\nid: {PROFILE_ID}\nupdated: {day}\n---",
             "# What buddy knows about its owner",
             f"Reconciled from conversations up to {day}. A fact may be a day or two behind.",
             section("Life context", "life_context"),
             section("Acting on their behalf", "acting"),
             section("How they like to talk", "talking")]
    index = render_index(records)
    if index:
        parts.append("## " + index.replace(":\n", "\n", 1))
    return "\n\n".join(parts) + "\n"


def day_notes(cfg: RecallConfig, day: str) -> str:
    """Everything chat_memory.py wrote for one day: the day file and the session notes under it."""
    parts: list[str] = []
    for path in sorted(cfg.store.glob(f"{day}-*.md")):
        try:
            parts.append(f"--- day: {path.name}\n{path.read_text(encoding='utf-8')}")
        except OSError:
            continue
    for path in sorted((cfg.sessions_dir / day).glob("*.md")) if (cfg.sessions_dir / day).is_dir() else []:
        try:
            parts.append(f"--- note: {path.name}\n{path.read_text(encoding='utf-8')}")
        except OSError:
            continue
    return "\n\n".join(parts)


def reconciled_days(cfg: RecallConfig) -> set[str]:
    try:
        return {ln.strip() for ln in (records_dir(cfg) / STATE_FILE).read_text().splitlines() if ln.strip()}
    except OSError:
        return set()


def due_days(cfg: RecallConfig, now: datetime) -> list[str]:
    """Days with a curated day file (chat_memory's pass has run) that the records have not read yet.
    Today is never due: its day file does not exist until tomorrow."""
    today = now.strftime("%Y-%m-%d")
    done = reconciled_days(cfg)
    days = sorted({p.name[:10] for p in cfg.store.glob("????-??-??-*.md")})
    return [d for d in days if d != today and d not in done]


def apply(cfg: RecallConfig, result: dict[str, Any], day: str) -> tuple[int, int]:
    """Write the reconcile result: changed records, forgotten records, the profile, the state line.
    → (records written, records forgotten). Malformed entries are skipped, never fatal."""
    folder = records_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    written = forgotten = 0
    for raw in result.get("records") or []:
        if not isinstance(raw, dict):
            continue
        rid = str(raw.get("id") or "").strip().lower()
        if not _ID.match(rid) or rid == PROFILE_ID:
            continue
        facts = [str(f).strip() for f in (raw.get("facts") or []) if str(f).strip()]
        if not facts:
            continue
        rec = Record(id=rid, type=str(raw.get("type") or "preference") if raw.get("type") in TYPES else "preference",
                     aliases=[str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()][:12],
                     facts=facts[:40], updated=day)
        (folder / f"{rid}.md").write_text(render_record(rec), encoding="utf-8")
        written += 1
    for rid in result.get("forget") or []:
        rid = str(rid).strip().lower()
        path = folder / f"{rid}.md"
        if _ID.match(rid) and rid != PROFILE_ID and path.exists():
            path.unlink()                     # gone from the working tree; git history still has it
            forgotten += 1
    prof = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    (folder / f"{PROFILE_ID}.md").write_text(render_profile(prof, load_records(cfg), day), encoding="utf-8")
    with (folder / STATE_FILE).open("a", encoding="utf-8") as fh:
        fh.write(day + "\n")
    return written, forgotten


class Reconciler:
    """The nightly pass. `client` has one method, ``reconcile(body) -> json text`` (chat_memory's client
    shape); the daemon gives it the real one, tests a fake."""

    def __init__(self, cfg: RecallConfig, client: Any, wall: Callable[[], datetime] = datetime.now) -> None:
        self.cfg = cfg
        self.client = client
        self.wall = wall
        self.git = ensure_repo(cfg.store) if cfg.store.is_dir() else False

    async def reconcile_day(self, day: str) -> Optional[str]:
        """One day's notes into the records. The commit hash, "" when written without git, None on failure."""
        notes = day_notes(self.cfg, day)
        if not notes.strip():
            return None
        current = load_records(self.cfg)
        body = "## Records now\n\n" + ("\n\n".join(render_record(r) for r in current.values()) or "(none yet)")
        stars = owner_stars(self.cfg)
        if stars:
            body += "\n\n## What the owner said to remember for good\n\n" + "\n".join(f"- {c}" for c in stars)
        body += f"\n\n## Notes from {day}\n\n{notes}"
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self.client.reconcile, body), timeout=RECONCILE_TIMEOUT_SECS)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("not an object")
        except (asyncio.TimeoutError, ValueError, Exception) as e:  # noqa: BLE001
            log.warning("records: could not reconcile %s (%s: %s)", day, type(e).__name__, e)
            return None
        if not self.git and self.cfg.store.is_dir():
            self.git = ensure_repo(self.cfg.store)
        if self.git:
            # Whatever is on disk now — the owner's hand edits included — goes into history first, as its
            # own commit, so the reconcile's diff is exactly what the model changed.
            commit(self.cfg.store, f"records: as found before reconciling {day}")
        try:
            written, forgotten = apply(self.cfg, result, day)
        except OSError as e:
            log.warning("records: could not write the records: %s", e)
            return None
        sha = commit(self.cfg.store, f"records: reconcile {day} ({written} changed, {forgotten} forgotten)") \
            if self.git else None
        log.info("records: %s → %d record(s) changed, %d forgotten, profile rewritten%s", day, written, forgotten,
                 f", commit {sha}" if sha else "")
        return sha or ""

    async def loop(self, shutdown: "asyncio.Event", interval_secs: float = 1800.0) -> None:
        """Like chat_memory.curate_loop: every half hour, any due day. Runs after the curate pass has had
        its turn, since a day is due only once its day file exists."""
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval_secs)
                return
            except asyncio.TimeoutError:
                pass
            for day in due_days(self.cfg, self.wall()):
                if shutdown.is_set():
                    return
                await self.reconcile_day(day)
                await asyncio.sleep(1.0)


class OpenAIReconcileClient:
    def __init__(self, model: str, api_key: Optional[str] = None) -> None:
        import openai

        self.model = model
        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()

    def reconcile(self, body: str) -> str:
        resp = self._client.responses.create(
            model=self.model, instructions=RECONCILE_PROMPT, input=body,
            text={"format": {"type": "json_schema", "name": "reconcile", "strict": True, "schema": RECONCILE_SCHEMA}},
            max_output_tokens=4000, reasoning={"effort": "low"}, store=False)
        text = (resp.output_text or "").strip()
        if not text:
            raise RuntimeError("empty reply")
        return text


@dataclass(frozen=True)
class RecordsConfig:
    enabled: bool = RECORDS_DEFAULT
    model: str = DEFAULT_MODEL


def configured(environ: Any = None) -> RecordsConfig:
    """``CC_BUDDY_RECORDS=1`` turns the layer on; ``CC_BUDDY_RECORDS_MODEL`` picks the reconciling model."""
    env = os.environ if environ is None else environ
    switch = (env.get("CC_BUDDY_RECORDS") or ("1" if RECORDS_DEFAULT else "0")).strip().lower()
    model = (env.get("CC_BUDDY_RECORDS_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return RecordsConfig(enabled=switch in ("1", "true", "yes", "on"), model=model)


def make_reconciler(config: RecordsConfig, cfg: RecallConfig, environ: Any = None) -> Optional[Reconciler]:
    """The real reconciler, or None (with one log line) when it cannot run."""
    env = os.environ if environ is None else environ
    if not config.enabled:
        return None
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        log.warning("records: OPENAI_API_KEY not set — records are read but never reconciled")
        return None
    try:
        client = OpenAIReconcileClient(config.model, api_key=key)
    except ImportError as e:
        log.warning("records: openai SDK not importable (%s) — records are read but never reconciled", e)
        return None
    cfg.store.mkdir(parents=True, exist_ok=True)
    records_dir(cfg).mkdir(parents=True, exist_ok=True)
    log.info("records: on — %s reconciles each day's notes into %s", config.model, records_dir(cfg))
    return Reconciler(cfg, client)
