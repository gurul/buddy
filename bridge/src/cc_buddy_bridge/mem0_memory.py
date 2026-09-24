"""mem0, self-hosted: the meaning search over everything the owner has said to buddy.

records.py answers "what do you know about me" with a page and a keyword search. It misses a question
asked in other words than the record used ("where does my sister live" against "moved to the coast").
This is the layer that catches it: mem0's open-source library (mem0ai, Apache-2.0), run on this
computer, with its vector store (Qdrant, on disk) and its history database both under
``<memory>/mem0``. Only the two model calls leave the machine: fact extraction and embeddings, both
OpenAI (owner, 2026-09-23: "it can go to openai"), and the extraction call asks OpenAI not to keep it
(``store=False``).

It is an INDEX, not a source (owner, 2026-09-23: three stores — transcripts, records, mem0). The nightly
dream feeds it each finished conversation of the day, natively: what the owner said goes in as the user's
messages and what buddy said as the assistant's, dated, with its channel, so mem0 extracts the owner's
own facts rather than facts about a note. Tool calls, commands and relays never go in. It can be rebuilt
from the transcripts at any time (``memory reindex``), which is why a failure here is never a loss.

The brains never write it: they search it (``search``, dated hits, 3 s at most) through the Memory
facade. A forget reaches it (``find`` + ``forget``), including the rows mem0's own history database keeps.

Privacy, as code rather than hope:

- mem0 ships PostHog telemetry, on by default and read at import time. ``MEM0_TELEMETRY`` is forced off
  before the first import, and the import is refused if telemetry still reads on.
- mem0 writes ``~/.mem0/config.json`` at import. ``MEM0_DIR`` points it inside buddy's own folder.
- The folder is 0700 and its files 0600, set again every time it opens; nothing here is in git.

Failure is silence: no key, no network, no mem0 installed — search returns nothing, a conversation that
failed to go in is tried again next night, and nothing about a conversation breaks. A failure to open is
retried after ten minutes, so one bad moment (a key not yet set, a lock held) does not last until restart.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from . import spend
from . import transcripts as transcripts_mod
from .recall import RecallConfig

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-nano"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536                              # text-embedding-3-small
OWNER = "owner"                                # the one user id: this is one person's memory
INGESTED_CONVS = "ingested_convs"              # one conversation id per line: what mem0 has read
HISTORY_DB = "history.db"
MAX_HITS = 5
MIN_SCORE = 0.25                               # below this a hit is a guess, not a memory
SEARCH_TIMEOUT_SECS = 3.0                      # a hung embedding call must not stall a turn
MAX_SEARCHES_IN_FLIGHT = 2                     # abandoned searches still running: past this, answer [] at once
RETRY_AFTER_SECS = 600.0                       # a failed open is tried again after ten minutes
TIMEOUT_LOG_EVERY_SECS = 3600.0
CHUNK_MESSAGES = 40                            # messages per mem0.add call
MESSAGE_CHARS = 1500                           # one message, clipped
GET_ALL_CAP = 10000
STAR = "★"                                     # a candidate buddy proposed, never the owner's own fact

# mem0's extraction prompt addition (MemoryConfig.custom_instructions, verified in mem0ai 2.2.0).
CUSTOM_INSTRUCTIONS = (
    "The user is the owner of buddy, a small desk robot, and the assistant is buddy. Extract facts about "
    "the owner only: their life, work, people, plans, preferences and routines, as the owner said them. "
    "A request to operate the robot or its tools (move, look, play a sound, set a timer, send a message, run "
    "a command) is not a fact about the owner: extract nothing from it. Never extract what buddy said about "
    "itself, and never a guess buddy made about the owner that the owner did not confirm.")

_DAY = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Mem0Config:
    enabled: bool = False
    home: Path = Path("~/.config/cc-buddy-bridge/memory/mem0").expanduser()
    model: str = DEFAULT_MODEL
    embed_model: str = DEFAULT_EMBED_MODEL


def configured(recall_cfg: RecallConfig, environ: Any = None) -> Mem0Config:
    """On with memory (``CC_BUDDY_MEMORY=1``, or the older ``CC_BUDDY_RECORDS=1``); ``CC_BUDDY_MEM0=0``
    turns just this index off. Its home is always the memory root's ``mem0/``."""
    env = os.environ if environ is None else environ
    memory_on = transcripts_mod.configured(env).enabled
    index_off = str(env.get("CC_BUDDY_MEM0") or "").strip().lower() in ("0", "false", "no", "off")
    return Mem0Config(enabled=memory_on and not index_off, home=recall_cfg.mem0_dir)


def available() -> bool:
    """Whether mem0ai is installed. Checked without importing it (importing starts its telemetry)."""
    try:
        return importlib.util.find_spec("mem0") is not None
    except (ImportError, ValueError):
        return False


def mem0_config(cfg: Mem0Config) -> dict[str, Any]:
    """The Memory.from_config dict: OpenAI for the two model calls, everything stored under ``cfg.home``."""
    return {
        # store=False: mem0's extraction call is a Chat Completions call, and its text is not to be kept on
        # OpenAI's side (owner, 2026-09-23: "nothing should leak out ever"). mem0ai 2.2.0 sends it only when
        # set. The embeddings call has no such flag.
        "llm": {"provider": "openai", "config": {"model": cfg.model, "store": False}},
        "embedder": {"provider": "openai", "config": {"model": cfg.embed_model}},
        "vector_store": {"provider": "qdrant", "config": {
            "collection_name": "buddy", "path": str(cfg.home / "qdrant"), "on_disk": True,
            "embedding_model_dims": EMBED_DIMS}},
        "history_db_path": str(cfg.home / HISTORY_DB),
        "custom_instructions": CUSTOM_INSTRUCTIONS,
    }


def make_private(home: Path) -> None:
    """``home`` and every folder under it 0700, every file 0600. Symlinks are never followed."""
    with contextlib.suppress(OSError):
        home.mkdir(parents=True, exist_ok=True)
    if home.is_symlink():
        return
    with contextlib.suppress(OSError):
        os.chmod(home, 0o700)
    for root, dirs, files in os.walk(home):
        for name in dirs:
            p = os.path.join(root, name)
            if not os.path.islink(p):
                with contextlib.suppress(OSError):
                    os.chmod(p, 0o700)
        for name in files:
            p = os.path.join(root, name)
            if not os.path.islink(p):
                with contextlib.suppress(OSError):
                    os.chmod(p, 0o600)


def open_mem0(cfg: Mem0Config) -> Any:
    """The real mem0 Memory, with telemetry off and its home inside ours. Raises when that cannot be had."""
    cfg.home.mkdir(parents=True, exist_ok=True)
    os.environ["MEM0_TELEMETRY"] = "False"          # read once, at import: must be set before it
    os.environ["MEM0_DIR"] = str(cfg.home / "home")  # where mem0 keeps its config.json, instead of ~/.mem0
    from mem0 import Memory
    from mem0.memory import telemetry

    if telemetry.MEM0_TELEMETRY:
        raise RuntimeError("mem0 telemetry is on (mem0 was imported before buddy could turn it off)")
    return meter(Memory.from_config(mem0_config(cfg)), cfg)


def meter(memory: Any, cfg: Mem0Config) -> Any:
    """Route mem0's two OpenAI calls through the daily spend meter (spend.py). mem0 owns its clients, so the
    ``create`` of its fact extraction (Chat Completions, ``memory.llm.client``) and of its embeddings
    (``memory.embedding_model.client``) is wrapped on those instances: each reply is recorded, then returned
    unchanged. A client shaped otherwise (another mem0 version) is left as it is, with one log line: the index
    must never break for want of a meter."""
    def wrap(owner: Any, record: Any, what: str) -> None:
        original = getattr(owner, "create", None)
        if not callable(original) or getattr(original, "_buddy_metered", False):
            log.warning("mem0: %s calls are not metered (no create to wrap)", what)
            return

        def create(*args: Any, **kwargs: Any) -> Any:
            reply = original(*args, **kwargs)
            record(reply, str(kwargs.get("model") or ""))
            return reply

        create._buddy_metered = True  # type: ignore[attr-defined]
        owner.create = create

    try:
        wrap(memory.llm.client.chat.completions,
             lambda r, m: spend.record_chat_completion(spend.MEMORY, r, model=m or cfg.model, provider="openai"),
             "extraction")
        wrap(memory.embedding_model.client.embeddings,
             lambda r, m: spend.record_embedding(spend.MEMORY, m or cfg.embed_model, r), "embedding")
    except AttributeError:
        log.warning("mem0: its model calls are not metered (this mem0 has no client where buddy looks)")
    return memory


def _rows(found: Any) -> list[dict[str, Any]]:
    rows = found.get("results", []) if isinstance(found, dict) else (found or [])
    return [r for r in rows if isinstance(r, dict)]


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).strip(" .,;:!").casefold()


def _day_of(row: dict[str, Any]) -> str:
    """The day a memory is from: its own `day`, else the date in a legacy note path, else when mem0 made it."""
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for raw in (meta.get("day"), meta.get("note"), row.get("created_at")):
        m = _DAY.search(str(raw or ""))
        if m:
            return m.group(1)
    return ""


class OwnerMemory:
    """mem0 for one owner. ``factory`` makes the Memory (open_mem0 by default; tests pass a fake);
    ``clock`` is monotonic seconds (tests inject one).

    Every call to mem0 is serialised (a local Qdrant store is one writer at a time). Search runs on a
    daemon thread of its own so it can be abandoned after SEARCH_TIMEOUT_SECS without holding up the
    process's exit; the rest is called from the dream's worker thread."""

    def __init__(self, cfg: Mem0Config, factory: Optional[Callable[[Mem0Config], Any]] = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.home = cfg.home
        self._factory = factory or open_mem0
        self._clock = clock
        self._mem: Any = None
        self._failed_at: Optional[float] = None
        self._lock = threading.Lock()
        self._in_flight = 0
        self._count_lock = threading.Lock()
        self._last_timeout_log = -TIMEOUT_LOG_EVERY_SECS

    # -- opening -------------------------------------------------------------------------------

    def _memory(self) -> Any:
        """The Memory, opened on first use. Call with self._lock held. None while off."""
        if self._mem is not None:
            return self._mem
        if self._failed_at is not None and self._clock() - self._failed_at < RETRY_AFTER_SECS:
            return None
        try:
            make_private(self.home)
            self._mem = self._factory(self.cfg)
            make_private(self.home)          # what opening just created, too
            self._failed_at = None
            log.info("mem0: on — %s extracts, %s embeds, stored in %s", self.cfg.model,
                     self.cfg.embed_model, self.home)
        except Exception as e:  # noqa: BLE001 - not installed, no key, telemetry refused: all are "off"
            self._failed_at = self._clock()
            log.warning("mem0: unavailable (%s) — tried again in %d minutes", type(e).__name__,
                        int(RETRY_AFTER_SECS // 60))
        return self._mem

    # -- reading -------------------------------------------------------------------------------

    def _search_now(self, query: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            mem = self._memory()
            if mem is None:
                return []
            found = mem.search(query, filters={"user_id": OWNER}, top_k=limit)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for r in _rows(found):
            text = " ".join(str(r.get("memory") or "").split())
            try:
                score = float(r.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            if not text or score < MIN_SCORE or STAR in text or _norm(text) in seen:
                continue
            seen.add(_norm(text))
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            out.append({"text": text, "day": _day_of(r), "channel": str(meta.get("channel") or "")})
        return out[:limit]

    def search(self, query: str, limit: int = MAX_HITS) -> list[dict[str, Any]]:
        """The memories that mean what `query` asks, best first: [{"text", "day", "channel"}], at most
        `limit`, each scoring MIN_SCORE or more. [] when there are none, mem0 is off, or it took longer than
        SEARCH_TIMEOUT_SECS (the call is abandoned, not waited for)."""
        query = " ".join(str(query or "").split())
        if not query:
            return []
        with self._count_lock:
            if self._in_flight >= MAX_SEARCHES_IN_FLIGHT:
                return []                            # earlier searches are still stuck: do not pile up threads
            self._in_flight += 1
        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["rows"] = self._search_now(query, max(1, min(limit, MAX_HITS)))
            except Exception as e:  # noqa: BLE001
                box["error"] = e
            finally:
                with self._count_lock:
                    self._in_flight -= 1
                done.set()

        threading.Thread(target=run, name="mem0-search", daemon=True).start()
        if not done.wait(SEARCH_TIMEOUT_SECS):
            now = self._clock()
            if now - self._last_timeout_log >= TIMEOUT_LOG_EVERY_SECS:
                self._last_timeout_log = now
                log.warning("mem0: search took over %.0f s — answered without it", SEARCH_TIMEOUT_SECS)
            return []
        if "error" in box:
            log.warning("mem0: search failed (%s)", type(box["error"]).__name__)
            return []
        return box.get("rows", [])

    def _all(self, mem: Any) -> list[dict[str, Any]]:
        return _rows(mem.get_all(filters={"user_id": OWNER}, top_k=GET_ALL_CAP))

    def find(self, match: Callable[[str], bool]) -> list[dict[str, str]]:
        """Every memory whose text `match` accepts: [{"id", "text"}]. [] when mem0 is off or failed."""
        with self._lock:
            mem = self._memory()
            if mem is None:
                return []
            try:
                rows = self._all(mem)
            except Exception as e:  # noqa: BLE001
                log.warning("mem0: listing failed (%s)", type(e).__name__)
                return []
        return [{"id": str(r["id"]), "text": str(r.get("memory") or "")}
                for r in rows if r.get("id") and match(str(r.get("memory") or ""))]

    # -- writing (the dream and a forget only) ----------------------------------------------------

    def _done(self) -> set[str]:
        try:
            return set((self.home / INGESTED_CONVS).read_text(encoding="utf-8").split("\n")) - {""}
        except OSError:
            return set()

    def _mark(self, conv: str) -> bool:
        try:
            fd = os.open(self.home / INGESTED_CONVS, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, (conv + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except OSError as e:
            log.warning("mem0: could not mark a conversation read (%s)", type(e).__name__)
            return False

    @staticmethod
    def messages(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
        """A conversation's lines as mem0 messages: what the owner said → user, what buddy said →
        assistant. Everything else (tools, commands, relays, photos, close markers, Claude) stays out."""
        roles = {"owner": "user", "buddy": "assistant"}
        out = []
        for ln in lines:
            role = roles.get(ln.get("who", ""))
            text = " ".join(str(ln.get("text") or "").split())
            if ln.get("kind") != "say" or role is None or not text:
                continue
            out.append({"role": role, "content": text[:MESSAGE_CHARS]})
        return out

    def ingest_day(self, transcripts: Any, day: str) -> int:
        """Feed each conversation of `day` that mem0 has not read to it. → how many went in.

        One ``add`` per CHUNK_MESSAGES messages, metadata {day, channel, conv}. A conversation is marked
        read only when every chunk went in; one that failed is tried again next time (mem0's own update
        step and ``tidy`` absorb a chunk read twice). A conversation in which the owner said nothing is
        marked read and not sent."""
        added = 0
        done = self._done()
        for info in transcripts.conversations(day):
            if info.conv in done:
                continue
            msgs = self.messages(transcripts.conv_lines(info.conv))
            chunks = [msgs[i:i + CHUNK_MESSAGES] for i in range(0, len(msgs), CHUNK_MESSAGES)]
            chunks = [c for c in chunks if any(m["role"] == "user" for m in c)]
            ok = True
            for chunk in chunks:
                with self._lock:
                    mem = self._memory()
                    if mem is None:
                        return added
                    try:
                        mem.add(chunk, user_id=OWNER, metadata={"day": day, "channel": info.ch, "conv": info.conv})
                    except Exception as e:  # noqa: BLE001
                        log.warning("mem0: a conversation did not go in (%s); tried again next time",
                                    type(e).__name__)
                        ok = False
                        break
            if not ok:
                continue
            with self._lock:
                make_private(self.home)
                if not self._mark(info.conv):
                    return added
            if chunks:
                added += 1
        if added:
            log.info("mem0: read %d conversation(s) of one day", added)
        return added

    def _delete(self, mem: Any, ids: list[str]) -> list[str]:
        gone = []
        for mid in ids:
            try:
                mem.delete(mid)
                gone.append(mid)
            except Exception as e:  # noqa: BLE001 - already gone, or mem0 failed: the rest still go
                log.warning("mem0: could not delete one memory (%s)", type(e).__name__)
        return gone

    def tidy(self) -> int:
        """Delete exact duplicates (same text, any case or spacing; the oldest stays) and anything carrying a
        ★ (a candidate buddy proposed, which only the owner may promote). → how many went."""
        with self._lock:
            mem = self._memory()
            if mem is None:
                return 0
            try:
                rows = sorted(self._all(mem), key=lambda r: str(r.get("created_at") or ""))
            except Exception as e:  # noqa: BLE001
                log.warning("mem0: listing failed (%s)", type(e).__name__)
                return 0
            seen: set[str] = set()
            doomed: list[str] = []
            for r in rows:
                text, mid = str(r.get("memory") or ""), r.get("id")
                if not mid:
                    continue
                key = _norm(text)
                if STAR in text or key in seen:
                    doomed.append(str(mid))
                else:
                    seen.add(key)
            gone = self._delete(mem, doomed)
        if gone:
            log.info("mem0: tidied %d memories", len(gone))
        return len(gone)

    def forget(self, ids: list[str]) -> int:
        """Delete these memories, then everything mem0's history database still holds of them. → how many
        memories were deleted.

        mem0's ``delete`` removes the vector but writes the old text into its history table (a DELETE row),
        and every earlier ADD/UPDATE row keeps it too; its messages table keeps the last raw messages it was
        fed. So after the deletes: every history row of these ids goes, the messages table is emptied (it is
        only mem0's extraction context, and a forgotten line may be in it), with secure_delete on, and the
        databases are vacuumed so the text does not linger in free pages."""
        ids = [str(i) for i in ids if str(i or "").strip()]
        if not ids:
            return 0
        with self._lock:
            mem = self._memory()
            if mem is None:
                return 0
            gone = self._delete(mem, ids)
            self._scrub(ids)
        log.info("mem0: forgot %d memories", len(gone))
        return len(gone)

    def _scrub(self, ids: list[str]) -> None:
        path = self.home / HISTORY_DB
        if path.exists():
            try:
                con = sqlite3.connect(str(path), timeout=10)
                try:
                    con.execute("PRAGMA secure_delete = ON")
                    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for table in ("history", "history_old"):
                        if table in tables:
                            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                            if "memory_id" in cols:
                                con.executemany(f"DELETE FROM {table} WHERE memory_id = ?", [(i,) for i in ids])
                    if "messages" in tables:
                        con.execute("DELETE FROM messages")
                    con.commit()
                finally:
                    con.close()
            except sqlite3.Error as e:
                log.warning("mem0: could not scrub the history database (%s)", type(e).__name__)
        for db in [path, *sorted((self.home / "qdrant").rglob("*.sqlite"))]:
            if db.exists() and not db.is_symlink():
                try:
                    con = sqlite3.connect(str(db), timeout=10, isolation_level=None)
                    try:
                        con.execute("VACUUM")
                    finally:
                        con.close()
                except sqlite3.Error as e:
                    log.warning("mem0: could not compact a database (%s)", type(e).__name__)


_shared: dict[str, OwnerMemory] = {}
_shared_lock = threading.Lock()


def shared(recall_cfg: RecallConfig, environ: Any = None) -> Optional[OwnerMemory]:
    """The daemon's one OwnerMemory (a local Qdrant store takes one opener), or None when memory or the
    index is switched off, or mem0ai is not installed."""
    cfg = configured(recall_cfg, environ)
    if not cfg.enabled:
        return None
    if not available():
        log.warning("mem0: mem0ai is not installed — memory search stays records and transcripts")
        return None
    key = str(cfg.home)
    with _shared_lock:
        if key not in _shared:
            _shared[key] = OwnerMemory(cfg)
        return _shared[key]
