"""records.py: typed, git-tracked records the agent reads and only the nightly reconcile writes."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from cc_buddy_bridge import records, telegram
from cc_buddy_bridge.recall import RecallConfig
from cc_buddy_bridge.records import (
    MEMORY_TOOLS,
    Reconciler,
    Record,
    RecordsReader,
    apply,
    configured,
    due_days,
    load_records,
    parse_record,
    render_record,
    search,
)

DAY = "2026-09-20"
DINING = Record(id="dining", type="preference", aliases=["food", "lunch", "restaurants", "takeout"],
                facts=["Loves pasta (said 2026-09-15).", "Usual lunch spot is [[cafe-nero]] (said 2026-09-18)."],
                updated="2026-09-18")
CAFE = Record(id="cafe-nero", type="place", aliases=["cafe", "coffee place", "nero"],
              facts=["The coffee place on Castro Street (said 2026-09-18)."])


def cfg_at(tmp_path: Path) -> RecallConfig:
    store = tmp_path / "debrief"
    store.mkdir()
    return RecallConfig(store=store, notes=tmp_path / "notes")


def seed(cfg: RecallConfig, *recs: Record) -> None:
    folder = records.records_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    for rec in recs:
        (folder / f"{rec.id}.md").write_text(render_record(rec), encoding="utf-8")


def day_files(cfg: RecallConfig, day: str = DAY, text: str = "- Owner said they now prefer ramen for lunch") -> None:
    (cfg.store / f"{day}-a-day.md").write_text(f"---\nday: {day}\n---\n\n# A day\n\n## What I know now\n\n{text}\n")
    (cfg.sessions_dir / day).mkdir(parents=True, exist_ok=True)
    (cfg.sessions_dir / day / "1200-abc.md").write_text("# Lunch\n\n- said: ramen now, pasta is over\n")


class FakeReconcile:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.bodies: list[str] = []

    def reconcile(self, body: str) -> str:
        self.bodies.append(body)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return json.dumps(result)


def result(**over: Any) -> dict[str, Any]:
    base = {"records": [{"id": "dining", "type": "preference", "aliases": ["food", "lunch", "ramen"],
                         "facts": ["Now prefers ramen for lunch (changed 2026-09-20; was pasta).",
                                   "Usual lunch spot is [[cafe-nero]] (said 2026-09-18)."]}],
            "forget": [],
            "profile": {"life_context": ["Works on a desk robot called buddy."],
                        "acting": ["Order lunch without asking; ask before anything over $50."],
                        "talking": ["Short texts, no lists."]}}
    base.update(over)
    return base


# ---- ships off --------------------------------------------------------------------------------

def test_it_ships_off() -> None:
    assert records.RECORDS_DEFAULT is False
    assert configured({}).enabled is False
    assert configured({"CC_BUDDY_RECORDS": "1"}).enabled is True                 # the control
    assert records.make_reconciler(configured({}), RecallConfig(Path("/nowhere"), Path("/nowhere"))) is None
    assert records.make_reconciler(configured({"CC_BUDDY_RECORDS": "1"}),
                                   RecallConfig(Path("/nowhere"), Path("/nowhere")), environ={}) is None   # no key


# ---- the file format --------------------------------------------------------------------------

def test_a_record_round_trips_and_a_hand_edited_one_still_parses() -> None:
    assert parse_record(render_record(DINING)) == DINING
    assert DINING.links == ["cafe-nero"]
    hand = "---\nid: dining\nType: Preference\naliases: [ food , 'lunch' ]\n---\n\nsome prose the owner typed\n\n* Loves pasta.\n- Hates kale\n"
    rec = parse_record(hand)
    assert rec is not None and rec.aliases == ["food", "lunch"] and rec.facts == ["Loves pasta.", "Hates kale"]
    assert rec.type == "preference"
    for bad in ("", "no frontmatter", "---\nid: Has Spaces\n---\n- x", "---\ntype: person\n---\n- no id"):
        assert parse_record(bad) is None


def test_load_skips_the_profile_and_a_file_whose_id_does_not_match_its_name(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    seed(cfg, DINING, CAFE)
    (records.records_dir(cfg) / "profile.md").write_text("---\nid: profile\n---\n# hi\n")
    (records.records_dir(cfg) / "imposter.md").write_text(render_record(DINING))
    assert sorted(load_records(cfg)) == ["cafe-nero", "dining"]
    assert load_records(RecallConfig(tmp_path / "none", tmp_path / "none")) == {}


# ---- what the agent may do ------------------------------------------------------------------------

def test_search_is_keyword_over_aliases_and_lines_and_aliases_count_more() -> None:
    recs = {"dining": DINING, "cafe-nero": CAFE}
    hits = search(recs, "where do I get lunch?")
    assert hits[0]["id"] == "dining" and "lunch spot" in hits[0]["line"]   # alias "lunch" + the word in the line
    assert search(recs, "coffee")[0]["id"] == "cafe-nero"                # alias only, no fact mentions coffee
    assert search(recs, "castro street")[0]["line"].startswith("The coffee place")
    assert search(recs, "") == [] and search(recs, "quantum") == []
    assert len(search(recs, "the", limit=1)) <= 1


def test_the_reader_re_reads_disk_so_an_owner_edit_counts_at_once(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    seed(cfg, DINING)
    reader = RecordsReader(cfg)
    assert reader.profile() == ""
    assert reader.search("pasta")["hits"][0]["id"] == "dining"
    (records.records_dir(cfg) / "dining.md").write_text(render_record(Record("dining", "preference", ["food"],
                                                                             ["Went off pasta (said 2026-09-21)."])))
    assert reader.search("pasta")["hits"][0]["line"].startswith("Went off")
    assert reader.get("dining")["record"].startswith("---\nid: dining")
    assert reader.get("nope")["ok"] is False
    (records.records_dir(cfg) / "profile.md").write_text("# The owner\n" + "x" * 10000)
    assert 0 < len(reader.profile()) <= records.MAX_PROFILE_CHARS + 2


def test_the_text_brain_gets_the_profile_and_the_two_tools_only_with_records() -> None:
    cfg = telegram.TelegramConfig(enabled=True, token="t", owner_ids=frozenset({1}))
    plain = telegram.request(cfg, [], profile="")
    assert plain["tools"] == telegram.TOOLS + [{"type": "web_search"}] and "memory_search" not in json.dumps(plain["tools"])
    with_profile = telegram.request(cfg, [], profile="# The owner\n- Loves pasta")
    assert with_profile["tools"] == telegram.TOOLS + MEMORY_TOOLS + [{"type": "web_search"}]
    assert with_profile["instructions"].endswith("- Loves pasta") and "read it before answering" in with_profile["instructions"]
    assert all(name in telegram.TOOL_NAMES for name in ("memory_search", "memory_get"))
    assert not any(t.get("name", "").startswith("memory_") and "write" in t["name"] for t in MEMORY_TOOLS)


# ---- the reconcile: the only writer ----------------------------------------------------------------

def test_a_day_is_reconciled_into_records_a_profile_and_one_commit(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    seed(cfg, DINING, CAFE)
    day_files(cfg)
    client = FakeReconcile(result())
    rec = Reconciler(cfg, client, wall=lambda: datetime(2026, 9, 21, 9))
    assert due_days(cfg, datetime(2026, 9, 21, 9)) == [DAY]
    assert due_days(cfg, datetime(2026, 9, 20, 9)) == []            # never today
    sha = asyncio.run(rec.reconcile_day(DAY))
    assert sha and len(sha) >= 7
    body = client.bodies[0]
    assert "## Records now" in body and "Loves pasta" in body and "ramen now" in body and "a-day.md" in body
    dining = load_records(cfg)["dining"]
    assert dining.facts[0].startswith("Now prefers ramen") and dining.updated == DAY and "ramen" in dining.aliases
    assert load_records(cfg)["cafe-nero"] == CAFE                     # untouched: not in the result
    prof = (records.records_dir(cfg) / "profile.md").read_text()
    assert "## Life context" in prof and "Order lunch without asking" in prof and "Short texts" in prof
    assert "- dining (preference): food, lunch, ramen" in prof and "- cafe-nero (place)" in prof
    assert due_days(cfg, datetime(2026, 9, 21, 9)) == []            # read once
    log = subprocess.run(["git", "-C", str(cfg.store), "log", "--oneline"], capture_output=True, text=True).stdout
    assert log.count("\n") == 2 and "reconcile 2026-09-20 (1 changed, 0 forgotten)" in log.splitlines()[0]
    assert "as found before reconciling" in log.splitlines()[1]
    # history keeps what the working tree lost: the pasta fact is one commit back
    old = subprocess.run(["git", "-C", str(cfg.store), "show", "HEAD~1:records/dining.md"],
                         capture_output=True, text=True).stdout
    assert "Loves pasta" in old and "Loves pasta" not in (records.records_dir(cfg) / "dining.md").read_text()


def test_forgetting_removes_the_file_and_git_still_has_it(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    seed(cfg, DINING, CAFE)
    day_files(cfg, text="- Owner said: forget the cafe, never mention it")
    rec = Reconciler(cfg, FakeReconcile(result(records=[], forget=["cafe-nero", "profile", "../etc"])))
    assert asyncio.run(rec.reconcile_day(DAY))
    assert sorted(load_records(cfg)) == ["dining"]
    assert (records.records_dir(cfg) / "profile.md").exists()
    shown = subprocess.run(["git", "-C", str(cfg.store), "log", "--all", "--oneline", "--", "records/cafe-nero.md"],
                           capture_output=True, text=True).stdout
    assert "reconcile" in shown


def test_a_bad_result_writes_nothing_and_the_day_stays_due(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    seed(cfg, DINING)
    day_files(cfg)
    for bad in (RuntimeError("503"), "not json", ["a", "list"]):
        client = FakeReconcile(bad) if isinstance(bad, Exception) else FakeReconcile()
        if not isinstance(bad, Exception):
            client.results = [bad]
            client.reconcile = lambda body, b=bad: b if isinstance(b, str) else json.dumps(b)
        assert asyncio.run(Reconciler(cfg, client).reconcile_day(DAY)) is None
    assert load_records(cfg)["dining"] == DINING
    assert due_days(cfg, datetime(2026, 9, 21)) == [DAY]
    assert not (records.records_dir(cfg) / "profile.md").exists()


def test_apply_skips_malformed_entries_and_never_escapes_the_folder(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    written, forgotten = apply(cfg, {"records": [
        {"id": "../escape", "type": "person", "aliases": [], "facts": ["x"]},
        {"id": "Profile", "type": "person", "aliases": [], "facts": ["x"]},
        {"id": "empty", "type": "person", "aliases": [], "facts": []},
        {"id": "sam", "type": "not-a-type", "aliases": ["  ", "colleague"], "facts": [" Works at Acme. ", ""]},
        "junk"], "forget": ["../../etc/passwd"], "profile": {"life_context": []}}, DAY)
    assert (written, forgotten) == (1, 0)
    sam = load_records(cfg)["sam"]
    assert sam.type == "preference" and sam.aliases == ["colleague"] and sam.facts == ["Works at Acme."]
    assert sorted(p.name for p in records.records_dir(cfg).iterdir()) == [".reconciled", "profile.md", "sam.md"]
    assert not (tmp_path / "escape.md").exists() and not (tmp_path / "debrief" / "escape.md").exists()
    assert "(nothing yet)" in (records.records_dir(cfg) / "profile.md").read_text()


def test_a_day_without_notes_is_nothing_and_the_loop_stops_on_shutdown(tmp_path: Path) -> None:
    cfg = cfg_at(tmp_path)
    rec = Reconciler(cfg, FakeReconcile())
    assert asyncio.run(rec.reconcile_day("2026-01-01")) is None

    async def go() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(rec.loop(stop, interval_secs=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(go())


def test_without_git_records_are_still_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = cfg_at(tmp_path)
    seed(cfg, DINING)
    day_files(cfg)
    monkeypatch.setattr(records.shutil, "which", lambda name: None)
    rec = Reconciler(cfg, FakeReconcile(result()))
    assert rec.git is False
    assert asyncio.run(rec.reconcile_day(DAY)) == ""
    assert load_records(cfg)["dining"].facts[0].startswith("Now prefers ramen")
    assert not (cfg.store / ".git").exists()
