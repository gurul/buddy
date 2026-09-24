"""mem0, self-hosted: a meaning search over everything buddy has written down about talking with its owner.

records.py answers "what do you know about me" with a page and a keyword search. It misses a question
asked in other words than the note used ("where does my sister live" against "Ana moved to Lisbon").
This is the layer that catches it: mem0's open-source library (mem0ai, Apache-2.0), run on this
computer, with its vector store (Qdrant, on disk) and its history database both under
``~/.config/cc-buddy-bridge/mem0``. Only the two model calls leave the machine: fact extraction and
embeddings, both OpenAI (owner, 2026-09-23: "it can go to openai").

It sits beside records, never instead of it (owner, 2026-09-23), and keeps records' one rule: **the
text brain never writes memory.** mem0 is fed only the distilled session notes chat_memory.py already
writes, one note at a time, off the conversation's path. The first run feeds every note already on disk.

Privacy, as code rather than hope:

- mem0 ships PostHog telemetry, on by default and read at import time. ``MEM0_TELEMETRY`` is forced off
  before the first import, and the import is refused if telemetry still reads on.
- mem0 writes ``~/.mem0/config.json`` at import. ``MEM0_DIR`` points it inside buddy's own folder.
- Nothing here is in a git repository, and the folder is outside the project.

Failure is silence, as in chat_memory.py: no key, no network, no mem0 installed — search returns nothing,
a note that failed to go in is tried again next pass, and nothing about a conversation breaks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .recall import RecallConfig

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-nano"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536                              # text-embedding-3-small
DEFAULT_HOME = Path.home() / ".config" / "cc-buddy-bridge" / "mem0"
OWNER = "owner"                                # the one user id: this is one person's memory
INGESTED_FILE = "ingested"                     # one session note path per line: what mem0 has read
INGEST_INTERVAL_SECS = 300.0                   # a closed conversation is searchable within five minutes
MAX_HITS = 5
MIN_SCORE = 0.25                               # below this a hit is a guess, not a memory
MAX_NOTE_CHARS = 4000

# The notes speak of "the owner" and "you" (buddy). mem0 extracts facts about the user from user messages,
# so the note goes in as the owner's message, said what it is.
NOTE_PREFIX = ("These are notes buddy, my desk robot, wrote about a conversation with me. \"Owner\" is me; "
               "\"you\" is buddy. Keep what they say about me: ")


@dataclass(frozen=True)
class Mem0Config:
    enabled: bool = False
    home: Path = DEFAULT_HOME
    model: str = DEFAULT_MODEL
    embed_model: str = DEFAULT_EMBED_MODEL


def configured(environ: Any = None) -> Mem0Config:
    """``CC_BUDDY_MEM0=1`` turns it on; ``CC_BUDDY_MEM0_MODEL`` picks the extracting model."""
    env = os.environ if environ is None else environ
    on = (env.get("CC_BUDDY_MEM0") or "0").strip().lower() in ("1", "true", "yes", "on")
    model = (env.get("CC_BUDDY_MEM0_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    home = Path(env.get("CC_BUDDY_MEM0_HOME") or DEFAULT_HOME).expanduser()
    return Mem0Config(enabled=on, home=home, model=model)


def mem0_config(cfg: Mem0Config) -> dict[str, Any]:
    """The Memory.from_config dict: OpenAI for the two model calls, everything stored under ``cfg.home``."""
    return {
        "llm": {"provider": "openai", "config": {"model": cfg.model}},
        "embedder": {"provider": "openai", "config": {"model": cfg.embed_model}},
        "vector_store": {"provider": "qdrant", "config": {
            "collection_name": "buddy", "path": str(cfg.home / "qdrant"), "on_disk": True,
            "embedding_model_dims": EMBED_DIMS}},
        "history_db_path": str(cfg.home / "history.db"),
    }


def open_mem0(cfg: Mem0Config) -> Any:
    """The real mem0 Memory, with telemetry off and its home inside ours. Raises when that cannot be had."""
    cfg.home.mkdir(parents=True, exist_ok=True)
    os.environ["MEM0_TELEMETRY"] = "False"          # read once, at import: must be set before it
    os.environ["MEM0_DIR"] = str(cfg.home / "home")  # where mem0 keeps its config.json, instead of ~/.mem0
    from mem0 import Memory
    from mem0.memory import telemetry

    if telemetry.MEM0_TELEMETRY:
        raise RuntimeError("mem0 telemetry is on (mem0 was imported before buddy could turn it off)")
    return Memory.from_config(mem0_config(cfg))


def note_body(text: str) -> str:
    """A session note without its frontmatter and the unverified banner: what was said, bounded."""
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(">")).strip()
    return body[:MAX_NOTE_CHARS]


class OwnerMemory:
    """mem0 for one owner. ``factory`` makes the Memory (open_mem0 by default; tests pass a fake).

    Every call is blocking and serialised: the daemon runs them on a worker thread, and a local Qdrant
    store is one writer at a time."""

    def __init__(self, cfg: Mem0Config, recall_cfg: RecallConfig,
                 factory: Optional[Callable[[Mem0Config], Any]] = None) -> None:
        self.cfg = cfg
        self.recall_cfg = recall_cfg
        self._factory = factory or open_mem0
        self._mem: Any = None
        self._failed = False
        self._lock = threading.Lock()

    def _memory(self) -> Any:
        if self._mem is None and not self._failed:
            try:
                self._mem = self._factory(self.cfg)
                log.info("mem0: on — %s extracts, %s embeds, stored in %s", self.cfg.model,
                         self.cfg.embed_model, self.cfg.home)
            except Exception as e:  # noqa: BLE001 - not installed, no key, telemetry refused: all are "off"
                self._failed = True
                log.warning("mem0: unavailable (%s: %s) — search stays records-only", type(e).__name__, e)
        return self._mem

    def search(self, query: str, limit: int = MAX_HITS) -> list[str]:
        """The memories that mean what `query` asks, best first. [] when there are none or mem0 is off."""
        query = " ".join(query.split())
        if not query:
            return []
        with self._lock:
            mem = self._memory()
            if mem is None:
                return []
            try:
                found = mem.search(query, filters={"user_id": OWNER}, top_k=limit)
            except Exception as e:  # noqa: BLE001
                log.warning("mem0: search failed (%s: %s)", type(e).__name__, e)
                return []
        rows = found.get("results", []) if isinstance(found, dict) else (found or [])
        return [str(r.get("memory")) for r in rows
                if isinstance(r, dict) and r.get("memory") and float(r.get("score") or 0) >= MIN_SCORE]

    def _ingested(self) -> set[str]:
        try:
            return set((self.cfg.home / INGESTED_FILE).read_text(encoding="utf-8").split("\n")) - {""}
        except OSError:
            return set()

    def pending_notes(self) -> list[Path]:
        """Session notes on disk that mem0 has not read yet, oldest first."""
        done = self._ingested()
        sessions = self.recall_cfg.sessions_dir
        if not sessions.is_dir():
            return []
        return [p for p in sorted(sessions.glob("*/*.md")) if str(p.relative_to(sessions)) not in done]

    def ingest_pending(self) -> int:
        """Feed every unread session note to mem0. → how many went in. A failed note stays pending."""
        added = 0
        for path in self.pending_notes():
            rel = str(path.relative_to(self.recall_cfg.sessions_dir))
            try:
                body = note_body(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            with self._lock:
                mem = self._memory()
                if mem is None:
                    return added
                if body:
                    try:
                        mem.add([{"role": "user", "content": NOTE_PREFIX + body}], user_id=OWNER,
                                metadata={"note": rel})
                    except Exception as e:  # noqa: BLE001
                        log.warning("mem0: could not read %s (%s: %s); will retry", rel, type(e).__name__, e)
                        continue
                try:
                    self.cfg.home.mkdir(parents=True, exist_ok=True)
                    with (self.cfg.home / INGESTED_FILE).open("a", encoding="utf-8") as fh:
                        fh.write(rel + "\n")
                except OSError as e:
                    log.warning("mem0: could not mark %s read: %s", rel, e)
                    return added
            added += 1
        if added:
            log.info("mem0: read %d session note(s)", added)
        return added

    async def loop(self, shutdown: asyncio.Event, interval_secs: float = INGEST_INTERVAL_SECS) -> None:
        """Read new notes now (the backlog, on the first start), then every few minutes."""
        while not shutdown.is_set():
            await asyncio.to_thread(self.ingest_pending)
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval_secs)
                return
            except asyncio.TimeoutError:
                pass


_shared: dict[str, Optional[OwnerMemory]] = {}


def shared(recall_cfg: RecallConfig, environ: Any = None) -> Optional[OwnerMemory]:
    """The daemon's one OwnerMemory (a local Qdrant store takes one opener), or None when switched off."""
    cfg = configured(environ)
    if not cfg.enabled:
        return None
    key = str(cfg.home)
    if key not in _shared:
        _shared[key] = OwnerMemory(cfg, recall_cfg)
    return _shared[key]
