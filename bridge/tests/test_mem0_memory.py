"""mem0_memory.py: a local mem0 fed only buddy's session notes, searched beside the records."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from cc_buddy_bridge import mem0_memory, records
from cc_buddy_bridge.mem0_memory import Mem0Config, OwnerMemory, configured, mem0_config, note_body
from cc_buddy_bridge.recall import RecallConfig
from cc_buddy_bridge.records import RecordsReader

NOTE = """---
status: machine-draft
source: buddy-voice
ended: 2026-09-23 14:01
---

> **Unverified.** buddy wrote this to itself right after talking. It has not been checked
> against anything.

# Sister trip

## What was said

- Owner said their sister Ana lives in Lisbon
"""


class FakeMem0:
    def __init__(self, fail_adds: int = 0) -> None:
        self.added: list[tuple[list[dict[str, str]], dict[str, Any]]] = []
        self.fail_adds = fail_adds

    def add(self, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
        if self.fail_adds:
            self.fail_adds -= 1
            raise RuntimeError("network")
        self.added.append((messages, kw))
        return {"results": []}

    def search(self, query: str, **kw: Any) -> dict[str, Any]:
        assert kw["filters"] == {"user_id": "owner"}
        return {"results": [{"memory": "Sister Ana lives in Lisbon", "score": 0.6},
                            {"memory": "A guess", "score": 0.1}]}


def setup(tmp_path: Path, fake: Any = None) -> tuple[OwnerMemory, RecallConfig, FakeMem0]:
    store = tmp_path / "debrief"
    store.mkdir()
    rc = RecallConfig(store=store, notes=tmp_path / "notes")
    fake = fake or FakeMem0()
    cfg = Mem0Config(enabled=True, home=tmp_path / "mem0")
    return OwnerMemory(cfg, rc, factory=lambda _c: fake), rc, fake


def write_note(rc: RecallConfig, day: str, name: str, text: str = NOTE) -> None:
    (rc.sessions_dir / day).mkdir(parents=True, exist_ok=True)
    (rc.sessions_dir / day / name).write_text(text, encoding="utf-8")


def test_it_ships_off_and_stores_everything_under_its_own_folder(tmp_path: Path) -> None:
    assert configured({}).enabled is False
    assert configured({"CC_BUDDY_MEM0": "1"}).enabled is True
    home = tmp_path / "m"
    conf = mem0_config(Mem0Config(enabled=True, home=home))
    assert conf["vector_store"]["provider"] == "qdrant" and conf["vector_store"]["config"]["path"] == str(home / "qdrant")
    assert conf["history_db_path"] == str(home / "history.db")
    assert "host" not in conf["vector_store"]["config"] and "url" not in conf["vector_store"]["config"]
    assert mem0_memory.shared(RecallConfig(store=tmp_path, notes=tmp_path), {}) is None


def test_the_note_goes_in_without_frontmatter_or_banner_once(tmp_path: Path) -> None:
    mem, rc, fake = setup(tmp_path)
    write_note(rc, "2026-09-22", "0900-a.md")
    write_note(rc, "2026-09-23", "1401-b.md")
    assert mem.ingest_pending() == 2
    content = fake.added[0][0][0]["content"]
    assert content.startswith(mem0_memory.NOTE_PREFIX) and "sister Ana lives in Lisbon" in content
    assert "machine-draft" not in content and "Unverified" not in content
    assert fake.added[0][1] == {"user_id": "owner", "metadata": {"note": "2026-09-22/0900-a.md"}}
    assert mem.ingest_pending() == 0 and len(fake.added) == 2            # read once
    write_note(rc, "2026-09-23", "1500-c.md")
    assert mem.ingest_pending() == 1


def test_a_note_that_failed_is_tried_again(tmp_path: Path) -> None:
    mem, rc, fake = setup(tmp_path, FakeMem0(fail_adds=1))
    write_note(rc, "2026-09-23", "1401-b.md")
    assert mem.ingest_pending() == 0 and mem.pending_notes()
    assert mem.ingest_pending() == 1 and not mem.pending_notes()


def test_search_drops_guesses_and_memory_search_carries_both_layers(tmp_path: Path) -> None:
    mem, rc, _ = setup(tmp_path)
    assert mem.search("where does my sister live") == ["Sister Ana lives in Lisbon"]
    assert mem.search("   ") == []
    out = RecordsReader(rc, mem).search("sister")
    assert out["recalled"] == ["Sister Ana lives in Lisbon"] and "note" not in out
    assert RecordsReader(rc).search("sister") == {"ok": True, "hits": [], "note": "nothing matched"}
    assert "by meaning" in records.MEMORY_TOOLS[0]["description"]


def test_mem0_unavailable_is_silence(tmp_path: Path) -> None:
    store = tmp_path / "debrief"
    store.mkdir()
    rc = RecallConfig(store=store, notes=tmp_path / "n")

    def broken(_c: Mem0Config) -> Any:
        raise ImportError("no mem0")

    mem = OwnerMemory(Mem0Config(enabled=True, home=tmp_path / "m"), rc, factory=broken)
    write_note(rc, "2026-09-23", "x.md")
    assert mem.search("anything") == [] and mem.ingest_pending() == 0 and mem.pending_notes()


def test_the_loop_reads_at_once_and_stops_on_shutdown(tmp_path: Path) -> None:
    mem, rc, fake = setup(tmp_path)
    write_note(rc, "2026-09-23", "x.md")

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(mem.loop(stop, interval_secs=60))
        for _ in range(100):
            if fake.added:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(run())
    assert len(fake.added) == 1


def test_telemetry_is_off_and_mem0_home_is_ours_before_the_real_import(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    import os
    import sys

    if importlib.util.find_spec("mem0") is None:
        pytest.skip("mem0ai is not installed")
    for name in [m for m in sys.modules if m == "mem0" or m.startswith("mem0.")]:
        monkeypatch.delitem(sys.modules, name)                          # a fresh import reads the switch again
    monkeypatch.delenv("MEM0_TELEMETRY", raising=False)
    monkeypatch.delenv("MEM0_DIR", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    home = tmp_path / "mem0"
    mem0_memory.open_mem0(Mem0Config(enabled=True, home=home))
    from mem0.memory import telemetry

    assert telemetry.MEM0_TELEMETRY is False
    assert os.environ["MEM0_DIR"] == str(home / "home") and (home / "qdrant").is_dir()
    assert not (tmp_path / ".mem0").exists()


def test_note_body_keeps_what_was_said() -> None:
    assert note_body(NOTE).startswith("# Sister trip") and "Ana" in note_body(NOTE)
