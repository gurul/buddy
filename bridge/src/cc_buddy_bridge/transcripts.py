"""Every word, kept on this Mac: a per-day transcript store for voice and Telegram.

Until 2026-09-23 buddy kept no transcript. A conversation lived in RAM until it
closed, went to one model call to be distilled, and was dropped. One failed call,
one restart, one crash, and the words were gone for good; a Telegram chat could
not see the voice chat that happened an hour ago; and nothing could answer "what
exactly did I say about it on Tuesday". The owner reversed the 2026-09-11 rule
(owner, 2026-09-23): the raw words are now written down, locally, the moment
they are spoken or typed.

Memory is now three stores (owner, 2026-09-23): these transcripts, the records
and mem0. The transcripts are the source; the nightly dream reads a whole day
from here (``day_text``) and the other two are rebuilt from what it finds.

Two channels are the owner talking (``TALK``: voice and telegram). A third,
``bus``, holds a line some other program sent over the memory bus asking buddy
to keep it: it is in the day the dream reads and in search, labelled "sent",
but never in a prompt's today block, never in recent_conversation, and never
the "last talked" time — it is not the owner speaking.

### Where the words live, and why there

``~/.config/cc-buddy-bridge/memory/transcripts/<YYYY-MM-DD>.jsonl``, one JSON
object per line (``CC_BUDDY_MEMORY_DIR`` moves the whole memory folder). Meeting
notes buddy was asked to take sit beside them, under ``meetings/<date>/``. A day
starts at 04:00, not midnight, so a conversation at 01:00 belongs to the evening
it continued. The words must never reach a git object, a cloud folder or a
backup, so the guards below refuse, with one WARNING naming the rule, a root that
is:

- inside a git work tree (any parent holds ``.git``);
- in a synced or backed-up user folder (iCloud, CloudStorage, Documents, Desktop);
- a symlink, or not owned by this user.

The folder is 0700, each file 0600 (set with fchmod, whatever the umask says), and
an empty ``.metadata_never_index`` keeps Spotlight out. FileVault does the rest.
Nothing is pruned: the owner keeps every day (owner, 2026-09-23).

### Writes are one syscall; reads are cached

``append`` is synchronous and cheap: one ``os.write`` of one whole line on an
``O_APPEND`` descriptor, under a thread lock and an ``flock`` (so the forget CLI
in another process can rewrite a file safely). It is safe to call from the event
loop. Failure is silence: an unwritable disk counts ``stats.failed`` and logs once
an hour, and the conversation carries on.

Readers parse a file once per (mtime, size, inode) and skip torn or malformed
lines, so a crash mid-write costs one line, never the day.

### Logs never carry what was said

Counts, channels and seconds only. The words are on disk for the owner and for
buddy's own recall; they do not belong in a log file that gets pasted into bug
reports.

This module imports no network library. Nothing here leaves the machine.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, Union

log = logging.getLogger(__name__)

MEMORY_DEFAULT = False                         # owner, 2026-09-23: off in code, on in the owner's env
DEFAULT_MEMORY_DIR = "~/.config/cc-buddy-bridge/memory"
TRANSCRIPTS_SUBDIR = "transcripts"
MEETINGS_SUBDIR = "meetings"                   # <root>/meetings/<date>/*.md: notes buddy was asked to take
DAY_START_HOUR = 4                             # 04:00: the night belongs to the evening before
TG_CHARS = 32000                               # today, in the Telegram prompt
VOICE_CHARS = 4000                             # today, in the Live voice prompt (latency-bound)
BACKEND_CHARS = 16000                          # today, in the voice backend's prompt
DAY_TEXT_CHARS = 100_000                       # one whole day, for the nightly dream

TALK = ("voice", "telegram")                   # where the owner talks: the only channels a prompt block shows
BUS = "bus"                                    # a line sent over the memory bus: kept for the dream, never talk
CHANNELS = TALK + (BUS,)
WHO = ("owner", "buddy", "claude", "system")
KINDS = ("say", "tool", "command", "relay", "image", "close")
RENDER_KINDS = ("say", "tool", "image")        # what a prompt block shows by default
MEETING = "meeting"                            # the search label for meeting notes
NOTE = "note"                                  # the search label for the retired session notes and day files
FORGOTTEN = "(forgotten)"                      # what a redacted line's text becomes

MAX_TEXT_CHARS = 20000                         # one line on disk
LINE_CHARS = 800                               # one rendered line in a text-style block
TOOL_LINE_CHARS = 300
VOICE_LINE_CHARS = 300                         # voice blocks are small: shorter lines, more of them
VOICE_TOOL_LINE_CHARS = 150
HEADER_BUCKET = 50                             # the "N earlier lines" count moves in steps of this
KEEP_AFTER_CUT = 0.6                           # after an overflow, keep this share of the budget
SEARCH_DAYS_BACK = 30
SEARCH_LIMIT = 12
SEARCH_HIT_CHARS = 300
SEARCH_NEIGHBOUR_CHARS = 200
SEARCH_RESULT_CHARS = 3000
SEARCH_BUDGET_SECS = 1.5
READ_MAX_CHARS = 8000
FIND_CAP = 10000
FAIL_LOG_EVERY_SECS = 3600.0

LOCK_FILE = ".lock"
NEVER_INDEX = ".metadata_never_index"
# Folders a copy of the words must never sit in: they sync to a cloud or get backed up off the machine.
SYNCED_UNDER_HOME = ("Library/Mobile Documents", "Library/CloudStorage", "Documents", "Desktop")

_DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAY_NOTE = re.compile(r"^(\d{4}-\d{2}-\d{2})-.+\.md$")
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
_CONV = re.compile(r"^[a-z]-(\d{10,16})-[0-9a-f]{4}$")
_PHRASE = re.compile(r'"([^"]*)"')
_NOTE_TIME = re.compile(r"^(\d{2})(\d{2})-")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)

Stamp = Union[datetime, str]


# ---- config ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class TranscriptConfig:
    enabled: bool = MEMORY_DEFAULT
    root: Path = Path(DEFAULT_MEMORY_DIR).expanduser() / TRANSCRIPTS_SUBDIR


def _on(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def configured(environ: Any = None) -> TranscriptConfig:
    """``CC_BUDDY_MEMORY=1`` turns memory on (the older ``CC_BUDDY_RECORDS=1`` does too).
    ``CC_BUDDY_MEMORY_DIR`` moves the memory folder; the transcripts are its ``transcripts/``."""
    env = os.environ if environ is None else environ
    enabled = _on(env.get("CC_BUDDY_MEMORY")) or _on(env.get("CC_BUDDY_RECORDS")) or MEMORY_DEFAULT
    base = Path((env.get("CC_BUDDY_MEMORY_DIR") or "").strip() or DEFAULT_MEMORY_DIR).expanduser()
    return TranscriptConfig(enabled=enabled, root=base / TRANSCRIPTS_SUBDIR)


# ---- guards ----------------------------------------------------------------------------------

def refusal(root: Path) -> str:
    """Why `root` must not hold transcripts, or "" when it may. Checked before anything is created."""
    root = Path(os.path.abspath(root.expanduser()))
    if root.is_symlink():
        return "the folder is a symlink"
    try:
        real = root.resolve()
    except OSError:
        real = root
    home = Path.home()
    for rel in SYNCED_UNDER_HOME:
        for base in {home / rel, (home / rel).resolve() if (home / rel).exists() else home / rel}:
            if real == base or base in real.parents or root == base or base in root.parents:
                return f"the folder is under ~/{rel}, which syncs or backs up off this computer"
    for p in (real, *real.parents):
        try:
            in_git = (p / ".git").exists()
        except OSError:                       # an unreadable parent: it cannot be vouched for
            return "a parent folder cannot be checked for a git work tree"
        if in_git:
            return "the folder is inside a git work tree, and a word must never reach a git object"
    return ""


def _prepare(root: Path) -> str:
    """Create the folder private and Spotlight-proof. → the rule that failed, or ""."""
    try:
        why = refusal(root)
    except (OSError, RuntimeError) as e:
        return f"the folder could not be checked ({type(e).__name__})"
    if why:
        return why
    try:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            return "the folder is a symlink"
        st = os.lstat(root)
        if st.st_uid != os.getuid():
            return "the folder is not owned by this user"
        os.chmod(root, 0o700)
        marker = root / NEVER_INDEX
        if not marker.exists():
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.close(fd)
    except OSError as e:
        return f"the folder could not be prepared ({type(e).__name__})"
    return ""


# ---- matching (shared with forget.py) ---------------------------------------------------------

def match(query: str) -> Optional[Callable[[str], bool]]:
    """The one matching rule for search, find, redact and forget.py: case-insensitive; every term longer than
    one character must appear in the line; a "quoted phrase" must appear as written (spacing aside).
    → a predicate over a line's text, or None when the query has no usable term."""
    q = str(query or "")
    phrases = [" ".join(p.split()).casefold() for p in _PHRASE.findall(q)]
    rest = _PHRASE.sub(" ", q).replace('"', " ")
    terms = [t.strip(".,;:!?()[]{}'`*").casefold() for t in rest.split()]
    needles = [n for n in phrases + terms if len(n) > 1]
    if not needles:
        return None

    def predicate(text: str) -> bool:
        hay = " ".join(str(text or "").split()).casefold()
        return all(n in hay for n in needles)

    return predicate


# ---- small helpers ---------------------------------------------------------------------------

@dataclass
class TranscriptStats:
    appended: int = 0
    failed: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class ConvInfo:
    conv: str
    ch: str
    started: datetime
    ended: datetime
    closed: bool
    n_owner: int


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.astimezone()


def _parse_ts(ts: Stamp) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return _aware(ts)
    try:
        return _aware(datetime.fromisoformat(str(ts)))
    except (TypeError, ValueError):
        return None


def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: max(0, cap - 1)].rstrip() + "…"


def _conv_epoch(conv: str) -> Optional[datetime]:
    m = _CONV.match(conv or "")
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group(1)) / 1000.0).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _next_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def speaker(line: dict[str, Any]) -> str:
    who = line.get("who")
    base = {"owner": "Owner", "buddy": "buddy", "claude": "Claude"}.get(who, "system")
    if line.get("kind") == "tool":
        tool = str(line.get("tool") or "a tool").replace("_", " ")
        return f"{base} ({tool})"
    if line.get("kind") == "image":
        return f"{base} (photo)"
    if line.get("kind") == "relay":
        return f"{base} (to Claude)"
    return base


def _mode(ch: Any) -> str:
    """How a line reached buddy: spoken (voice), texted (Telegram) or sent (the memory bus, not the owner)."""
    return {"voice": "spoken", BUS: "sent"}.get(ch, "texted")


def render_line(line: dict[str, Any], style: str = "text") -> str:
    """One transcript line as a prompt line: 'HH:MM (spoken|texted|sent) Owner: …'. Style 'voice' clips shorter."""
    ts = _parse_ts(line.get("ts", ""))
    clock = f"{ts:%H:%M} " if ts else ""
    mode = _mode(line.get("ch"))
    tool = line.get("kind") == "tool"
    if style == "voice":
        cap = VOICE_TOOL_LINE_CHARS if tool else VOICE_LINE_CHARS
    else:
        cap = TOOL_LINE_CHARS if tool else LINE_CHARS
    return f"{clock}({mode}) {speaker(line)}: {_clip(_flat(line.get('text')), cap)}"


def render(lines: Iterable[dict[str, Any]], style: str = "text") -> str:
    """Lines rendered one per row, oldest first as given. '' for none."""
    return "\n".join(render_line(ln, style) for ln in lines)


def _parse_line(raw: str) -> Optional[dict[str, Any]]:
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    if not all(isinstance(obj.get(k), str) for k in ("ts", "ch", "conv", "who", "kind", "text")):
        return None
    if _parse_ts(obj["ts"]) is None:
        return None
    return obj


# ---- the store -------------------------------------------------------------------------------

class Transcripts:
    """The transcript store. One instance per daemon, shared by both channels and every reader.

    Search also reads the meeting notes under ``<root>/meetings/<date>/`` and, when ``archive`` is given (the
    retired debrief store), its session notes ``sessions/<date>/*.md`` and day files ``<date>-*.md``.
    ``wall`` is the clock (aware datetimes); tests inject one."""

    def __init__(self, config: TranscriptConfig, archive: Optional[Path] = None, *,
                 wall: Optional[Callable[[], datetime]] = None) -> None:
        self.config = config
        self.root = Path(config.root).expanduser()
        self.archive = Path(archive).expanduser() if archive is not None else None
        self._wall = wall or (lambda: datetime.now().astimezone())
        self.stats = TranscriptStats()
        self._lock = threading.Lock()            # writers: append, close, redact
        self._cache_lock = threading.Lock()      # readers' parsed-file cache
        self._cache: dict[Path, tuple[tuple[int, int, int], list[Any]]] = {}
        self._lock_fd: Optional[int] = None
        self._last: Optional[datetime] = None
        self._last_scanned = False
        self._minted: set[str] = set()
        self._last_fail_log = -FAIL_LOG_EVERY_SECS
        self.enabled = False
        if not config.enabled:
            return
        why = _prepare(self.root)
        if why:
            log.warning("transcripts: off — %s: %s", why, self.root)
            return
        try:
            self._lock_fd = os.open(self.root / LOCK_FILE,
                                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError as e:
            log.warning("transcripts: no lock file (%s); another process could race a forget", type(e).__name__)
        self.enabled = True
        log.info("transcripts: on — %s (a day starts at %02d:00)", self.root, DAY_START_HOUR)

    # -- time --------------------------------------------------------------------------------

    def now(self) -> datetime:
        return _aware(self._wall())

    def day_of(self, ts: Stamp) -> str:
        """The transcript day a moment belongs to: the local date, with the day starting at DAY_START_HOUR."""
        dt = _parse_ts(ts)
        if dt is None:
            dt = self.now()
        return (dt - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")

    def path_of(self, day: str) -> Path:
        return self.root / f"{day}.jsonl"

    def days(self) -> list[str]:
        """Every day with a transcript file, oldest first."""
        if not self.enabled:
            return []
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        return sorted(m.group(1) for m in (_DAY_FILE.match(n) for n in names) if m)

    # -- writing -----------------------------------------------------------------------------

    def new_conv(self, ch: str) -> str:
        """A conversation id nobody else has: '<v|t>-<epoch ms>-<4 hex>'."""
        prefix = (ch or "x")[0].lower()
        while True:
            conv = f"{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(2)}"
            with self._lock:
                if conv not in self._minted:
                    self._minted.add(conv)
                    if len(self._minted) > 4096:
                        self._minted = {conv}
                    return conv

    def _flock(self, op: int) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, op)
            except OSError:
                pass

    def _fail(self, what: str, e: BaseException) -> None:
        self.stats.failed += 1
        now = time.monotonic()
        if now - self._last_fail_log >= FAIL_LOG_EVERY_SECS:
            self._last_fail_log = now
            log.warning("transcripts: %s failed (%s; %d failures so far) — the conversation goes on",
                        what, type(e).__name__, self.stats.failed)

    def append(self, ch: str, conv: str, who: str, kind: str, text: str, tool: str = "",
               now: Optional[datetime] = None, *, fsync: bool = False) -> bool:
        """Write one line. Synchronous (one write), never raises. → whether the line reached the disk."""
        if not self.enabled:
            return False
        if ch not in CHANNELS or who not in WHO or kind not in KINDS or not conv:
            self.stats.rejected += 1
            return False
        at = _aware(now) if now is not None else self.now()
        rec: dict[str, Any] = {"ts": at.isoformat(timespec="seconds"), "ch": ch, "conv": conv, "who": who,
                               "kind": kind, "text": str(text or "")[:MAX_TEXT_CHARS]}
        if tool:
            rec["tool"] = str(tool)[:80]
        data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        path = self.path_of(self.day_of(at))
        with self._lock:
            self._flock(fcntl.LOCK_EX)
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    size = os.fstat(fd).st_size
                    # A crash mid-write left a torn last line: start on a fresh one, so only that line is lost.
                    if size and os.pread(fd, 1, size - 1) != b"\n":
                        data = b"\n" + data
                    os.write(fd, data)
                    if fsync:
                        os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as e:
                self._fail("append", e)
                return False
            finally:
                self._flock(fcntl.LOCK_UN)
            self.stats.appended += 1
            if kind != "close" and ch in TALK and (self._last is None or at > self._last):
                self._last = at
        return True

    def close(self, ch: str, conv: str, now: Optional[datetime] = None) -> bool:
        """The conversation is over: a close marker, flushed to the platter."""
        return self.append(ch, conv, "system", "close", "", now=now, fsync=True)

    # -- reading -----------------------------------------------------------------------------

    def _cached(self, path: Path, parse: Callable[[str], list[Any]]) -> list[Any]:
        try:
            st = os.stat(path)
        except OSError:
            with self._cache_lock:
                self._cache.pop(path, None)
            return []
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        with self._cache_lock:
            hit = self._cache.get(path)
            if hit is not None and hit[0] == key:
                return hit[1]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                parsed = parse(f.read())
        except OSError:
            return []
        with self._cache_lock:
            self._cache[path] = (key, parsed)
        return parsed

    def _invalidate(self, path: Path) -> None:
        with self._cache_lock:
            self._cache.pop(path, None)

    def lines(self, day: str) -> list[dict[str, Any]]:
        """Every well-formed line of one day, in the order written. Torn or malformed lines are skipped.
        The dicts are shared with the cache: read them, never change them."""
        if not self.enabled or not _DAY.match(day or ""):
            return []

        def parse(raw: str) -> list[Any]:
            return [obj for obj in (_parse_line(r) for r in raw.splitlines() if r.strip()) if obj is not None]

        return list(self._cached(self.path_of(day), parse))

    def _conv_day(self, conv: str, first_ts: str) -> str:
        born = _conv_epoch(conv)
        return self.day_of(born if born is not None else first_ts)

    def conv_lines(self, conv: str) -> list[dict[str, Any]]:
        """Every line of one conversation, even one that ran past the day boundary."""
        born = _conv_epoch(conv)
        days = [self.day_of(born)] if born is not None else self.days()
        if born is not None:
            days.append(_next_day(days[0]))
        return [ln for d in days for ln in self.lines(d) if ln["conv"] == conv]

    def conversations(self, day: str) -> list[ConvInfo]:
        """The conversations that began on `day` (by their id's birth time), oldest first. A conversation
        that ran past the day boundary is read whole, so `closed` and `ended` are true."""
        if not _DAY.match(day or ""):
            return []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for ln in self.lines(day) + self.lines(_next_day(day)):
            grouped.setdefault(ln["conv"], []).append(ln)
        out: list[ConvInfo] = []
        for conv, rows in grouped.items():
            if self._conv_day(conv, rows[0]["ts"]) != day:
                continue
            talk = [_parse_ts(r["ts"]) for r in rows if r["kind"] != "close"] or [_parse_ts(r["ts"]) for r in rows]
            out.append(ConvInfo(
                conv=conv, ch=rows[0]["ch"], started=min(talk), ended=max(talk),
                closed=any(r["kind"] == "close" for r in rows),
                n_owner=sum(1 for r in rows if r["who"] == "owner" and r["kind"] == "say")))
        out.sort(key=lambda c: c.started)
        return out

    def last_turn_at(self) -> Optional[datetime]:
        """When anything was last said or done on either channel (close markers and bus lines do not count)."""
        with self._lock:
            if self._last_scanned:
                return self._last
        newest: Optional[datetime] = None
        for day in reversed(self.days()[-2:]):
            for ln in self.lines(day):
                if ln["kind"] == "close" or ln["ch"] not in TALK:
                    continue
                ts = _parse_ts(ln["ts"])
                if ts is not None and (newest is None or ts > newest):
                    newest = ts
            if newest is not None:
                break
        with self._lock:
            if newest is not None and (self._last is None or newest > self._last):
                self._last = newest
            self._last_scanned = True
            return self._last

    def lines_since(self, ts: Stamp, exclude_ch: str = "",
                    kinds: Sequence[str] = RENDER_KINDS) -> list[dict[str, Any]]:
        """Lines strictly newer than `ts`, from the talk channels other than `exclude_ch`, oldest first."""
        since = _parse_ts(ts)
        if since is None:
            return []
        first, last = self.day_of(since), self.day_of(self.now())
        out: list[dict[str, Any]] = []
        for day in self.days():
            if first <= day <= last:
                for ln in self.lines(day):
                    at = _parse_ts(ln["ts"])
                    if (at is not None and at > since and ln["ch"] != exclude_ch and ln["ch"] in TALK
                            and ln["kind"] in kinds):
                        out.append(ln)
        return out

    def today_block(self, max_chars: int, exclude_conv: Optional[str] = None, exclude_tail: int = 0,
                    kinds: Sequence[str] = RENDER_KINDS, style: str = "text",
                    now: Optional[datetime] = None) -> str:
        """Today, both talk channels (never a bus line), oldest first, within `max_chars`. '' for an empty day.

        `exclude_conv` drops a conversation the caller already shows: all of it when `exclude_tail` is 0,
        or only its newest `exclude_tail` say/image lines (the turns a chat history keeps).

        Over budget, the NEWEST whole lines are kept under a fixed '(about N earlier lines today…)' header.
        The cut moves only when the budget overflows, and then back to KEEP_AFTER_CUT of it, so the block
        stays a stable prefix across many appends: prompt caching keeps working."""
        if not self.enabled or max_chars <= 0:
            return ""
        rows = [ln for ln in self.lines(self.day_of(now or self.now())) if ln["kind"] in kinds and ln["ch"] in TALK]
        if exclude_conv:
            if exclude_tail <= 0:
                rows = [ln for ln in rows if ln["conv"] != exclude_conv]
            else:
                turn_ids = [i for i, ln in enumerate(rows)
                            if ln["conv"] == exclude_conv and ln["kind"] in ("say", "image")]
                drop = set(turn_ids[-exclude_tail:])
                rows = [ln for i, ln in enumerate(rows) if i not in drop]
        rendered = [render_line(ln, style) for ln in rows]
        if not rendered:
            return ""
        total = sum(len(r) + 1 for r in rendered) - 1
        if total <= max_chars:
            return "\n".join(rendered)
        header_room = len(self._header(10 ** 6)) + 1
        room = max(0, max_chars - header_room)
        keep_room = int(room * KEEP_AFTER_CUT)
        start, size = 0, 0                       # size = len("\n".join(rendered[start:i + 1]))
        for i, r in enumerate(rendered):
            size += len(r) + (1 if i > start else 0)
            if size > room:
                # Overflow: cut from the front until the tail fits in keep_room, so the next many appends
                # extend this block instead of shifting it (the hysteresis that keeps the cache warm).
                while start < i and size > keep_room:
                    size -= len(rendered[start]) + 1
                    start += 1
                if size > room:                   # one line alone is over the whole budget
                    start, size = i + 1, 0
        kept = rendered[start:]
        if not kept:
            return ""
        block = self._header(start) + "\n" + "\n".join(kept)
        return block if len(block) <= max_chars else ""

    @staticmethod
    def _header(dropped: int) -> str:
        bucket = max(HEADER_BUCKET, -(-dropped // HEADER_BUCKET) * HEADER_BUCKET)
        return f"(about {bucket} earlier lines today: memory_search finds them)"

    def day_text(self, day: str, max_chars: int = DAY_TEXT_CHARS) -> str:
        """One whole day for the nightly dream: 'YYYY-MM-DD HH:MM spoken|texted|sent Owner/buddy[ (tool)]: text', say,
        tool and image lines, oldest first, each whole. The date is the line's own calendar date: a line at 00:08
        stays in the day before (the day runs 04:00 to 04:00) but says the date it was said, so the dream's
        journal never files it under the wrong date (days/2026-09-23.md did, for 2026-09-24 00:08). Over budget, the NEWEST lines are kept under a
        '(N earlier lines omitted)' header. '' for a day with nothing said."""
        rows = []
        for ln in self.lines(day):
            if ln["kind"] not in RENDER_KINDS or not ln["text"]:
                continue
            at = _parse_ts(ln["ts"])
            mode = _mode(ln["ch"])
            rows.append(f"{at:%Y-%m-%d %H:%M} {mode} {speaker(ln)}: {_flat(ln['text'])}")
        if not rows or max_chars <= 0:
            return ""
        size = sum(len(r) + 1 for r in rows) - 1
        if size <= max_chars:
            return "\n".join(rows)
        room = max_chars - len(f"({len(rows)} earlier lines omitted)") - 1
        if room < 0:
            return ""
        kept: list[str] = []
        used = 0
        for r in reversed(rows):
            if used + len(r) + (1 if kept else 0) > room:
                break
            kept.append(r)
            used += len(r) + (1 if len(kept) > 1 else 0)
        kept.reverse()
        return f"({len(rows) - len(kept)} earlier lines omitted)" + ("\n" + "\n".join(kept) if kept else "")

    def read(self, day: str, start: str = "", end: str = "", max_chars: int = READ_MAX_CHARS) -> str:
        """One day between two HH:MM times (inclusive; times after midnight count as late), oldest first."""
        if not _DAY.match(day or ""):
            return ""
        lo, hi = self._minute(start, 0), self._minute(end, 24 * 60 - 1)
        out: list[str] = []
        size = 0
        for ln in self.lines(day):
            if ln["kind"] == "close":
                continue
            at = _parse_ts(ln["ts"])
            if at is None:
                continue
            m = self._minute(f"{at:%H:%M}", 0)
            if not lo <= m <= hi:
                continue
            row = render_line(ln)
            if size + len(row) + 1 > max_chars - 60:
                out.append(f"(more after this: read again from {at:%H:%M})")
                break
            out.append(row)
            size += len(row) + 1
        return "\n".join(out)

    def _minute(self, hhmm: str, default: int) -> int:
        """Minutes since the day's start hour, so 01:00 sorts after 23:00."""
        m = _HHMM.match((hhmm or "").strip())
        if not m:
            return default
        h, mi = int(m.group(1)) % 24, min(59, int(m.group(2)))
        return ((h - DAY_START_HOUR) % 24) * 60 + mi

    # -- search ------------------------------------------------------------------------------

    def _doc_days(self, channels: Sequence[str]) -> set[str]:
        """Days that have markdown to search: meeting notes, and the archive's session notes and day files."""
        days: set[str] = set()

        def listing(folder: Path) -> list[str]:
            try:
                return os.listdir(folder)
            except OSError:
                return []

        if MEETING in channels:
            days.update(n for n in listing(self.root / MEETINGS_SUBDIR) if _DAY.match(n))
        if NOTE in channels and self.archive is not None:
            days.update(n for n in listing(self.archive / "sessions") if _DAY.match(n))
            days.update(m.group(1) for m in (_DAY_NOTE.match(n) for n in listing(self.archive)) if m)
        return days

    @staticmethod
    def _parse_doc(raw: str) -> list[Any]:
        """A markdown note's content lines: no frontmatter, comments, quotes or blank lines; bullets bare."""
        rows = []
        for r in _FRONTMATTER.sub("", raw, count=1).splitlines():
            r = r.strip()
            if not r or r.startswith("<!--") or r.startswith(">") or r == "---":
                continue
            rows.append(_flat(re.sub(r"^(#{1,6}\s+|[-*]\s+)", "", r)))
        return rows

    def _docs(self, day: str, channels: Sequence[str]) -> list[tuple[str, str, list[str]]]:
        """(channel, HH:MM or "", content lines) for each markdown note of one calendar day."""
        files: list[tuple[str, Path]] = []

        def md(folder: Path) -> list[Path]:
            try:
                return [folder / n for n in sorted(os.listdir(folder)) if n.endswith(".md")]
            except OSError:
                return []

        if MEETING in channels:
            files += [(MEETING, f) for f in md(self.root / MEETINGS_SUBDIR / day)]
        if NOTE in channels and self.archive is not None:
            files += [(NOTE, f) for f in md(self.archive / "sessions" / day)]
            files += [(NOTE, f) for f in md(self.archive) if f.name.startswith(f"{day}-")]
        out = []
        for ch, path in files:
            m = None if _DAY_NOTE.match(path.name) else _NOTE_TIME.match(path.name)   # a day file has no time
            out.append((ch, f"{m.group(1)}:{m.group(2)}" if m else "", self._cached(path, self._parse_doc)))
        return out

    def search(self, query: str, days_back: int = SEARCH_DAYS_BACK, limit: int = SEARCH_LIMIT,
               channel: str = "") -> dict[str, Any]:
        """Dated hits, newest day first: {"ok", "hits": [{day, time, ch, who, text, before, after}],
        "truncated"}. Every term must match; a quoted phrase matches as written. Three sources: the
        transcripts (ch voice|telegram|bus), meeting notes (ch 'meeting') and the archive's notes (ch 'note').
        The JSON result stays within 3000 chars and the search within 1.5 s; either cut sets truncated."""
        pred = match(query)
        if pred is None:
            return {"ok": False, "error": "The query has no word to search for.", "hits": [], "truncated": False}
        if not self.enabled:
            return {"ok": True, "hits": [], "truncated": False}
        try:
            days_back = int(days_back)
        except (TypeError, ValueError):
            days_back = SEARCH_DAYS_BACK
        days_back = SEARCH_DAYS_BACK if days_back < 1 else min(days_back, 3650)
        limit = max(1, min(int(limit or SEARCH_LIMIT), 50))
        docs = (MEETING, NOTE)
        channels = (channel,) if channel in CHANNELS + docs else CHANNELS + docs
        today = self.day_of(self.now())
        cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
        t_days = set(self.days()) if any(c in CHANNELS for c in channels) else set()
        d_days = self._doc_days(channels)
        deadline = time.monotonic() + SEARCH_BUDGET_SECS
        hits: list[dict[str, Any]] = []
        truncated = False
        for day in sorted((d for d in t_days | d_days if cutoff <= d <= today), reverse=True):
            if time.monotonic() > deadline:
                truncated = True
                break
            found: list[tuple[int, dict[str, Any]]] = []
            if day in t_days:
                rows = self.lines(day)
                for i in range(len(rows) - 1, -1, -1):
                    ln = rows[i]
                    if ln["kind"] == "close" or ln["ch"] not in channels or not pred(ln["text"]):
                        continue
                    at = _parse_ts(ln["ts"])
                    found.append((self._minute(f"{at:%H:%M}", 0) if at else 0, {
                        "day": day, "time": f"{at:%H:%M}" if at else "", "ch": ln["ch"], "who": ln["who"],
                        "text": _clip(_flat(ln["text"]), SEARCH_HIT_CHARS),
                        "before": self._neighbour(rows, i, -1), "after": self._neighbour(rows, i, 1)}))
            if day in d_days:
                for ch, hhmm, doc in self._docs(day, channels):
                    for i, text in enumerate(doc):
                        if pred(text):
                            found.append((self._minute(hhmm, -1), {
                                "day": day, "time": hhmm, "ch": ch, "who": "notes",
                                "text": _clip(text, SEARCH_HIT_CHARS),
                                "before": _clip(doc[i - 1], SEARCH_NEIGHBOUR_CHARS) if i else "",
                                "after": _clip(doc[i + 1], SEARCH_NEIGHBOUR_CHARS) if i + 1 < len(doc) else ""}))
            found.sort(key=lambda f: f[0], reverse=True)
            hits.extend(h for _, h in found)
            if len(hits) >= limit:
                truncated = truncated or len(hits) > limit
                hits = hits[:limit]
                break
        result = {"ok": True, "hits": hits, "truncated": truncated}
        while hits and len(json.dumps(result, ensure_ascii=False)) > SEARCH_RESULT_CHARS:
            hits.pop()
            result["truncated"] = True
        return result

    @staticmethod
    def _neighbour(rows: list[dict[str, Any]], i: int, step: int) -> str:
        conv = rows[i]["conv"]
        j = i + step
        while 0 <= j < len(rows):
            ln = rows[j]
            if ln["conv"] == conv and ln["kind"] != "close" and ln["text"]:
                return _clip(f"{speaker(ln)}: {_flat(ln['text'])}", SEARCH_NEIGHBOUR_CHARS)
            j += step
        return ""

    # -- forget (W11) ------------------------------------------------------------------------

    def find(self, query: str) -> list[dict[str, Any]]:
        """Every line on any day that `query` matches (the search rule), oldest first, as the line plus its
        "day". Lines already forgotten are not matched again. [] for a query with no usable term."""
        pred = match(query)
        if pred is None:
            return []
        out: list[dict[str, Any]] = []
        for day in self.days():
            for ln in self.lines(day):
                if ln["kind"] != "close" and ln["text"] != FORGOTTEN and pred(ln["text"]):
                    out.append({**ln, "day": day})
                    if len(out) >= FIND_CAP:
                        return out
        return out

    def redact(self, query: str) -> int:
        """Forget: every matching line keeps its envelope (time, channel, conversation, speaker) and its text
        becomes "(forgotten)". A torn line that matches is dropped. Each changed file is rewritten atomically
        (a 0600 temp file in the same folder, fsync, rename) under the writers' lock, so a concurrent append
        is never lost. → lines forgotten."""
        pred = match(query)
        if pred is None or not self.enabled:
            return 0
        total = 0
        for day in self.days():
            path = self.path_of(day)
            with self._lock:
                self._flock(fcntl.LOCK_EX)
                try:
                    total += self._redact_file(path, pred)
                except OSError as e:
                    self._fail("forget", e)
                finally:
                    self._flock(fcntl.LOCK_UN)
                    self._invalidate(path)
        if total:
            log.info("transcripts: forgot %d lines", total)
        return total

    def _redact_file(self, path: Path, pred: Callable[[str], bool]) -> int:
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return 0
        changed = 0
        out: list[bytes] = []
        for chunk in raw.split(b"\n"):
            if not chunk.strip():
                continue
            text = chunk.decode("utf-8", errors="replace")
            obj = _parse_line(text)
            if obj is None:
                if pred(text):              # a torn line holding the words: it goes entirely
                    changed += 1
                    continue
                out.append(chunk)
                continue
            if obj["kind"] != "close" and obj["text"] != FORGOTTEN and pred(obj["text"]):
                obj["text"] = FORGOTTEN
                out.append(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
                changed += 1
            else:
                out.append(chunk)
        if not changed:
            return 0
        self._atomic_write(path, b"".join(c + b"\n" for c in out))
        return changed

    def _atomic_write(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            dfd = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
