"""chat_memory.py: buddy writing down what was said, and curating its own days.

No model is called. The client is a fake returning canned JSON, so every test is
about the part that can actually be wrong: what lands on disk, what is refused,
and what happens when the model returns rubbish.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from cc_buddy_bridge.chat_memory import (
    CANDIDATE_MARK,
    DRAFT_STATUS,
    SOURCE,
    ChatMemory,
    daily_exists,
    days_with_drafts,
    make_chat_client,
    note_path,
    render_daily,
    render_note,
)
from cc_buddy_bridge.recall import RecallConfig, opening_brief

WHEN = datetime(2026, 9, 11, 22, 14)

DISTILLED = {
    "title": "The right servo and the flash script",
    "said": ["the owner thinks the right servo sticks", "the owner finds reflashing slow"],
    "open": ["the owner wants to try a different servo horn"],
    "owes": ["an answer about why the right servo sticks"],
    "star": ["the owner reflashes several times an evening and hates waiting"],
    "nothing": False,
}

CURATED = {
    "slug": "servos-and-reflashing",
    "title": "Servos, and how slow reflashing is",
    "happened": ["talked twice about the right servo", "the owner reflashed four times"],
    "learned": ["the owner reflashes in bursts and resents the wait"],
    "owes": ["buddy owes an answer about why the right servo sticks"],
    "index": ["the right servo sticks and buddy has not explained why"],
    "star": ["the owner reflashes several times an evening and hates waiting"],
}


class FakeClient:
    def __init__(self, distilled=None, curated=None, raise_on=None) -> None:
        self.distilled = json.dumps(distilled if distilled is not None else DISTILLED)
        self.curated = json.dumps(curated if curated is not None else CURATED)
        self.raise_on = raise_on or ()
        self.distil_calls: list[str] = []
        self.curate_calls: list[str] = []

    def distil(self, conversation: str) -> str:
        self.distil_calls.append(conversation)
        if "distil" in self.raise_on:
            raise RuntimeError("the model refused")
        return self.distilled

    def curate(self, notes: str) -> str:
        self.curate_calls.append(notes)
        if "curate" in self.raise_on:
            raise RuntimeError("the model refused")
        return self.curated


def _cfg(tmp_path: Path) -> RecallConfig:
    return RecallConfig(store=tmp_path / "debrief", notes=tmp_path / "notes")


TURNS = [
    ("user", "hey buddy, why does the right servo stick"),
    ("assistant", "I am not sure yet."),
    ("user", "and reflashing takes forever"),
    ("assistant", "Noted."),
]


# ---- one conversation ------------------------------------------------------------------

def test_a_conversation_becomes_one_private_draft(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(), wall=lambda: WHEN)
    path = asyncio.run(mem.remember(TURNS, "abc12345-0000"))
    assert path is not None and path.exists()
    assert path == note_path(cfg, WHEN, "abc12345-0000")
    assert path.name == "2214-abc12345.md"

    text = path.read_text()
    assert f"status: {DRAFT_STATUS}" in text
    assert f"source: {SOURCE}" in text
    assert "# The right servo and the flash script" in text
    assert "- buddy owes an answer about why the right servo sticks" in text
    assert CANDIDATE_MARK in text, "a star is proposed, never promoted"
    # the store and every directory down to the note is private
    for d in (cfg.store, cfg.sessions_dir, path.parent):
        assert (d.stat().st_mode & 0o777) == 0o700, d


def test_the_words_themselves_are_never_written_down(tmp_path: Path) -> None:
    """The owner chose distilled memories only. The transcript goes to the model
    and is dropped; a verbatim line on disk would be a different promise."""
    cfg = _cfg(tmp_path)
    client = FakeClient()
    mem = ChatMemory(cfg, client, wall=lambda: WHEN)
    path = asyncio.run(mem.remember(TURNS, "s1"))
    on_disk = path.read_text()
    for _who, said in TURNS:
        assert said not in on_disk, said
    # ... and it really was sent, so the distillation is not a no-op
    assert "why does the right servo stick" in client.distil_calls[0]
    assert client.distil_calls[0].startswith("OWNER:")


def test_a_wake_word_and_a_goodbye_is_not_a_conversation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    client = FakeClient()
    mem = ChatMemory(cfg, client, wall=lambda: WHEN)
    assert asyncio.run(mem.remember([("assistant", "Yeah?")], "s")) is None
    assert asyncio.run(mem.remember([("user", "bye")], "s")) is None
    assert client.distil_calls == [], "nothing that short is worth a model call"


def test_a_conversation_about_nothing_leaves_no_note(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    empty = {"title": "Nothing", "said": [], "open": [], "owes": [], "star": [], "nothing": True}
    mem = ChatMemory(cfg, FakeClient(distilled=empty), wall=lambda: WHEN)
    assert asyncio.run(mem.remember(TURNS, "s")) is None
    assert not cfg.sessions_dir.exists()


def test_a_debt_survives_even_a_nothing_conversation(tmp_path: Path) -> None:
    """"Nothing happened, but I still owe you an answer" is worth keeping."""
    cfg = _cfg(tmp_path)
    d = {"title": "Nothing much", "said": [], "open": [], "nothing": True,
         "owes": ["an answer about the servo"], "star": []}
    mem = ChatMemory(cfg, FakeClient(distilled=d), wall=lambda: WHEN)
    path = asyncio.run(mem.remember(TURNS, "s"))
    assert path is not None
    assert "- buddy owes an answer about the servo" in path.read_text()


def test_a_refusing_or_babbling_model_loses_the_note_and_nothing_else(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(raise_on=("distil",)), wall=lambda: WHEN)
    assert asyncio.run(mem.remember(TURNS, "s")) is None
    assert mem.stats.failed == 1

    class Babble:
        def distil(self, c): return "not json at all"
        def curate(self, n): return "{}"

    mem2 = ChatMemory(cfg, Babble(), wall=lambda: WHEN)
    assert asyncio.run(mem2.remember(TURNS, "s")) is None
    assert mem2.stats.failed == 1


def test_no_client_means_no_memory_and_no_crash(tmp_path: Path) -> None:
    mem = ChatMemory(_cfg(tmp_path), None, wall=lambda: WHEN)
    assert asyncio.run(mem.remember(TURNS, "s")) is None
    assert make_chat_client(environ={}) is None


def test_owes_lines_are_normalised_so_recall_can_find_them(tmp_path: Path) -> None:
    """recall.py prefers a line starting with "buddy owes". A model that drops the
    prefix must not cost buddy its best opening line."""
    cfg = _cfg(tmp_path)
    d = dict(DISTILLED, owes=["an answer about the servo", "buddy owes a look at the mount"])
    mem = ChatMemory(cfg, FakeClient(distilled=d), wall=lambda: WHEN)
    text = asyncio.run(mem.remember(TURNS, "s")).read_text()
    assert "- buddy owes an answer about the servo" in text
    assert "- buddy owes a look at the mount" in text


def test_the_note_written_is_the_note_recall_reads_back(tmp_path: Path) -> None:
    """The two halves have to agree, or buddy writes memories it cannot read."""
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(), wall=lambda: WHEN)
    asyncio.run(mem.remember(TURNS, "s"))
    brief = opening_brief(cfg, datetime(2026, 9, 12, 9, 0))
    assert "owes an answer about why the right servo sticks" in brief


# ---- the day pass ----------------------------------------------------------------------

def test_a_finished_day_is_curated_and_indexed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    client = FakeClient()
    mem = ChatMemory(cfg, client, wall=lambda: WHEN)
    asyncio.run(mem.remember(TURNS, "s1"))

    day = "2026-09-11"
    assert days_with_drafts(cfg) == [day]
    assert not daily_exists(cfg, day)

    out = asyncio.run(mem.curate_day(day))
    assert out is not None and out.name == "2026-09-11-servos-and-reflashing.md"
    text = out.read_text()
    assert "status: curated" in text and "curated_by: buddy" in text
    assert "# Servos, and how slow reflashing is" in text
    assert "buddy owes an answer about why the right servo sticks" in text
    assert CANDIDATE_MARK in text
    assert "only the owner promotes" in text

    index = (cfg.store / "INDEX.md").read_text()
    assert "## buddy's conversations" in index
    assert "2026-09-11-servos-and-reflashing.md" in index
    assert "the right servo sticks" in index
    assert daily_exists(cfg, day)
    # the drafts are left alone: the episodic layer is immutable
    assert len(days_with_drafts(cfg)) == 1


def test_today_is_never_curated_because_the_day_is_not_over(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(), wall=lambda: WHEN)
    asyncio.run(mem.remember(TURNS, "s1"))
    assert mem.due_days(WHEN) == [], "curating at 22:14 writes a record the night contradicts"
    assert mem.due_days(datetime(2026, 9, 12, 8, 0)) == ["2026-09-11"]


def test_a_day_is_curated_once(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    client = FakeClient()
    mem = ChatMemory(cfg, client, wall=lambda: WHEN)
    asyncio.run(mem.remember(TURNS, "s1"))
    asyncio.run(mem.curate_day("2026-09-11"))
    assert mem.due_days(datetime(2026, 9, 12, 8, 0)) == []
    assert len(client.curate_calls) == 1


def test_a_missed_day_is_still_written_later(tmp_path: Path) -> None:
    """The daemon is not guaranteed to be awake at any particular hour, so a day
    that went by with the machine asleep must still be curated when it comes back."""
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(), wall=lambda: datetime(2026, 9, 8, 21, 0))
    asyncio.run(mem.remember(TURNS, "s1"))
    assert mem.due_days(datetime(2026, 9, 14, 10, 0)) == ["2026-09-08"]


def test_curation_failing_leaves_the_drafts_and_no_half_written_day(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(raise_on=("curate",)), wall=lambda: WHEN)
    asyncio.run(mem.remember(TURNS, "s1"))
    assert asyncio.run(mem.curate_day("2026-09-11")) is None
    assert mem.stats.failed == 1
    assert not daily_exists(cfg, "2026-09-11")
    assert days_with_drafts(cfg) == ["2026-09-11"], "the drafts are still there to try again"


def test_the_curate_loop_stops_when_the_daemon_does(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mem = ChatMemory(cfg, FakeClient(), wall=lambda: WHEN)

    async def go() -> None:
        shutdown = asyncio.Event()
        task = asyncio.create_task(mem.curate_loop(shutdown, interval_secs=0.05))
        await asyncio.sleep(0.02)
        shutdown.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(go())


# ---- rendering ------------------------------------------------------------------------

def test_rendering_survives_a_model_that_ignores_the_schema() -> None:
    for junk in ({}, {"title": None, "said": "not a list", "owes": 3},
                 {"said": [None, "", "ok but longer than three"], "owes": []}):
        text = render_note(junk, WHEN, "s")
        assert text.startswith("---") and "## Open threads" in text
        assert "## What was said" in text
    daily = render_daily({}, "2026-09-11")
    assert "# A day of talking" in daily and "(a quiet day)" in daily


def test_a_note_says_it_is_unverified() -> None:
    """The episodic layer records what buddy believed, not what is true."""
    assert "Unverified" in render_note(DISTILLED, WHEN, "s")


# ---- the starred layer: promoted by voice, never by buddy -------------------------------

# The first 30 lines of a real installed HIGHLIGHTS.md. It carries the template
# banner AND ★ entries of its own, which is how a stranger's outage reached the
# widget on the first live read.
INSTALLED_TEMPLATE = """# ★ Highlights — must-know, forever

> **Template.** This is the starred layer: the handful of things you would want
> tattooed on the inside of your eyelids. Replace this blockquote and the example
> entries with your own.

## The two rules

1. **A human stars it. Always.** No hook, no drafter, and no agent may add an
   entry on its own initiative.

## Platform

- ★ Cloud Armor CRS 932140 matches the Windows batch `IF x==y` signature, so any
  request body containing it is blocked before it reaches the app (2026-03-04)
- ★ The 3am outage was a missing idempotency key, not the retry loop *(example)*
"""


def test_starring_is_how_the_owner_promotes_by_voice(tmp_path: Path) -> None:
    from cc_buddy_bridge.chat_memory import star, stars

    cfg = _cfg(tmp_path)
    assert star(cfg, "the owner hates waiting for a reflash", WHEN) == \
        "The owner hates waiting for a reflash"
    kept = stars(cfg)
    assert len(kept) == 1
    assert kept[0].startswith("The owner hates waiting for a reflash")
    assert "(2026-09-11)" in kept[0], "a highlight carries the day it was starred"
    text = (cfg.store / "HIGHLIGHTS.md").read_text()
    assert "## From talking" in text


def test_a_strangers_starred_example_is_never_read_back(tmp_path: Path) -> None:
    """The bug this test exists for: on the first live read, buddy presented a
    Cloud Armor rule from the installed template as its memory of its owner."""
    from cc_buddy_bridge.chat_memory import star, stars

    cfg = _cfg(tmp_path)
    cfg.store.mkdir(parents=True)
    (cfg.store / "HIGHLIGHTS.md").write_text(INSTALLED_TEMPLATE, encoding="utf-8")

    assert stars(cfg) == [], "nothing in the installed file is buddy's memory"

    star(cfg, "the owner keeps the guitar behind the desk", WHEN)
    kept = stars(cfg)
    assert len(kept) == 1 and kept[0].startswith("The owner keeps the guitar")
    assert not any("Cloud Armor" in k or "idempotency" in k for k in kept)
    # the owner's own file is not rewritten, only appended to
    assert "Cloud Armor" in (cfg.store / "HIGHLIGHTS.md").read_text()


def test_saying_it_twice_is_not_two_facts(tmp_path: Path) -> None:
    from cc_buddy_bridge.chat_memory import star, stars

    cfg = _cfg(tmp_path)
    star(cfg, "the owner hates waiting for a reflash", WHEN)
    star(cfg, "The owner hates waiting for a reflash.", WHEN)
    star(cfg, "  the OWNER hates waiting for a reflash  ", WHEN)
    assert len(stars(cfg)) == 1


def test_nothing_worth_starring_is_refused(tmp_path: Path) -> None:
    from cc_buddy_bridge.chat_memory import star, stars

    cfg = _cfg(tmp_path)
    for junk in ("", "   ", "ok", "..."):
        assert star(cfg, junk, WHEN) is None, junk
    assert stars(cfg) == []


def test_both_memory_calls_ask_for_a_schema_not_a_bare_json_object() -> None:
    """Same defect as the notes summary: `json_object` requires the word "json"
    in the input, and the input is what the owner said. Live probe, 2026-09-11:
    400 on both calls until they became schemas."""
    import sys
    import types

    calls: list[dict] = []

    class _Resp:
        output_text = "{}"

    class _Client:
        def __init__(self, **kw: object) -> None:
            self.responses = types.SimpleNamespace(create=self._create)

        def _create(self, **kw: object) -> _Resp:
            calls.append(kw)
            return _Resp()

    stub = types.ModuleType("openai")
    stub.OpenAI = _Client  # type: ignore[attr-defined]
    real = sys.modules.get("openai")
    sys.modules["openai"] = stub
    try:
        from cc_buddy_bridge.chat_memory import (
            CURATE_SCHEMA,
            DEFAULT_MODEL,
            DISTIL_SCHEMA,
            OpenAIChatClient,
        )

        client = OpenAIChatClient(model=DEFAULT_MODEL, api_key="k")
        client.distil("owner: hello\n")
        client.curate("# a note\n")
    finally:
        if real is None:
            del sys.modules["openai"]
        else:
            sys.modules["openai"] = real

    assert len(calls) == 2
    for call, schema in zip(calls, (DISTIL_SCHEMA, CURATE_SCHEMA), strict=True):
        fmt = call["text"]["format"]
        assert fmt["type"] == "json_schema" and fmt["strict"] is True
        assert fmt["schema"] is schema
    # a strict schema refuses a response that drops a field, so every key the
    # writer reads has to be required
    assert "owes" in DISTIL_SCHEMA["required"] and "star" in DISTIL_SCHEMA["required"]
    assert "slug" in CURATE_SCHEMA["required"] and "index" in CURATE_SCHEMA["required"]
