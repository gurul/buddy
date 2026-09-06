"""diary.py: the memory stream, retrieval, the pick, the write gate, the
appraisal → emote, and reflection — all against a fake client."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from cc_buddy_bridge.diary import (
    DEFAULT_PROFILE,
    DiaryTaker,
    Emote,
    Memory,
    Record,
    build_context,
    build_emote_cmd,
    jaccard,
    parse_reply,
    pick_thought,
    should_write,
    words,
)
from cc_buddy_bridge.explore import Note
from cc_buddy_bridge.vision import Frame

W, H = 160, 120


def _frame(level: int = 0) -> Frame:
    return Frame(seq=1, w=W, h=H, fmt="gray", data=bytes([level]) * (W * H))


def _reply(thoughts, novelty=6, importance=3, valence=20, arousal=30, label="curious", tags=("mug", "lamp"),
           observations=("blue mug on the left",), changed=("mug moved",)) -> str:
    return json.dumps({"observations": list(observations), "changed": list(changed),
                       "thoughts": [{"text": t, "p": p} for t, p in thoughts], "novelty": novelty,
                       "importance": importance, "tags": list(tags), "valence": valence, "arousal": arousal,
                       "label": label})


class FakeClient:
    def __init__(self, replies: list[str], reflection: str | None = None) -> None:
        self.replies = list(replies)
        self.reflection = reflection
        self.contexts: list[str] = []
        self.reflect_prompts: list[str] = []

    def think(self, image: bytes, mime: str, context: str) -> str:
        self.contexts.append(context)
        if not self.replies:
            raise RuntimeError("out of replies")
        return self.replies.pop(0)

    def reflect(self, prompt: str) -> str:
        self.reflect_prompts.append(prompt)
        if self.reflection is None:
            raise RuntimeError("no reflection")
        return self.reflection


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def wall(self) -> datetime:
        return self.now

    def tick(self, **kw) -> None:
        self.now = self.now + timedelta(**kw)


def _taker(tmp: Path, client: FakeClient, clock: Clock, emotes: list[Emote] | None = None) -> DiaryTaker:
    async def send(e: Emote) -> None:
        if emotes is not None:
            emotes.append(e)
    return DiaryTaker(client, tmp / "notes", send_emote=send, wall=clock.wall, clock=lambda: 0.0)


# ---- pure pieces --------------------------------------------------------------------------------

def test_words_and_jaccard() -> None:
    assert words("The blue mug's gone, isn't it?") == {"the", "blue", "mug's", "gone", "isn't", "it"}
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3 and jaccard(set(), {"a"}) == 0.0


def test_parse_reply_tolerates_fences_and_prose() -> None:
    assert parse_reply('```json\n{"a": 1}\n```')["a"] == 1
    assert parse_reply('Sure! {"thoughts": []} there')["thoughts"] == []


def test_pick_thought_prefers_least_typical_specific_non_repeat() -> None:
    recent = ["The blue mug is on the left again."]
    baseline = words("a desk with a monitor and a keyboard and the blue mug")
    cands = [{"text": "A desk with a monitor and a keyboard.", "p": 0.9},          # generic: only baseline words
             {"text": "The blue mug is on the left again today.", "p": 0.4},      # near-repeat of recent
             {"text": "Someone finished the coffee and left the spoon.", "p": 0.2}]
    assert pick_thought(cands, recent, baseline) == "Someone finished the coffee and left the spoon."
    # only generic candidates: the least typical still wins as a fallback
    assert pick_thought([{"text": "A desk with a monitor.", "p": 0.9}, {"text": "The keyboard and the mug.", "p": 0.3}],
                        recent, baseline) == "The keyboard and the mug."
    assert pick_thought([], recent, baseline) is None


def test_should_write_gate() -> None:
    now = 1_000_000.0
    assert should_write(5, 1, now - 10, now)                      # novel enough
    assert should_write(1, 7, now - 10, now)                      # important enough
    assert not should_write(2, 2, now - 10, now)                  # neither, and recently written
    assert should_write(2, 2, None, now)                          # nothing written yet
    assert should_write(2, 2, now - 3 * 3600, now)                # silent for 3 h


def test_build_emote_cmd_clamps() -> None:
    assert build_emote_cmd(Emote(250, -300, "very-long-label-here")) == \
        {"cmd": "emote", "dv": 100, "da": -100, "label": "very-long-l"}


# ---- memory ---------------------------------------------------------------------------------------

def _rec(i: int, ts: float, thought: str, tags=(), importance=3, written=True, hour=10) -> Record:
    return Record(id=i, ts=ts, weekday=0, hour=hour, yaw=0, pitch=45, thought=thought, tags=list(tags),
                  importance=importance, written=written, last_accessed=ts)


def test_memory_roundtrip_and_retrieval(tmp_path: Path) -> None:
    m = Memory(tmp_path / "n")
    m.load()
    t0 = 1_700_000_000.0
    m.add(_rec(1, t0, "the plant looks thirsty", ("plant",), importance=8))
    m.add(_rec(2, t0 + 3600, "lamp on at noon", ("lamp",), importance=2))
    m.add(_rec(3, t0 + 7200, "the blue mug moved", ("mug",), importance=5))
    m2 = Memory(tmp_path / "n")
    m2.load()
    assert [r.thought for r in m2.records] == ["the plant looks thirsty", "lamp on at noon", "the blue mug moved"]
    assert m2.profile == DEFAULT_PROFILE
    top = m2.retrieve({"mug"}, t0 + 8000, top=1)
    assert top[0].id == 3 and top[0].last_accessed == t0 + 8000      # relevance + recency win
    top = m2.retrieve({"plant"}, t0 + 8000, top=1, exclude={3})
    assert top[0].id == 1                                            # importance + relevance


def test_same_hour_and_unwritten_today(tmp_path: Path) -> None:
    m = Memory(tmp_path / "n")
    m.load()
    day = datetime(2026, 9, 6, 15, 0)
    yesterday = day - timedelta(days=1)
    m.add(_rec(1, yesterday.timestamp(), "chair pushed in", hour=15))
    m.add(_rec(2, day.timestamp(), "not written yet", hour=15, written=False))
    assert [r.id for r in m.same_hour(day)] == [1]
    assert [r.id for r in m.unwritten_today(day)] == [2]
    assert m.baseline_vocabulary() >= words("chair pushed in")


def test_decay_lowers_importance_of_unretrieved_records(tmp_path: Path) -> None:
    m = Memory(tmp_path / "n")
    m.load()
    t0 = 1_700_000_000.0
    m.add(_rec(1, t0, "old", importance=10))
    m.decay(t0 + 30 * 86400)
    assert m.records[0].importance < 10
    m2 = Memory(tmp_path / "n")
    m2.load()
    assert m2.records[0].importance == m.records[0].importance   # persisted


def test_build_context_sections(tmp_path: Path) -> None:
    m = Memory(tmp_path / "n")
    m.load()
    when = datetime(2026, 9, 6, 15, 0)
    m.add(_rec(1, (when - timedelta(days=1)).timestamp(), "yesterday at three the chair was out", ("chair",), hour=15))
    ctx = build_context(m, when, yaw=-20, pitch=40, guess_tags={"chair"})
    for h in ("PROFILE:", "LAST NOTES:", "OLDER MEMORIES:", "SAME HOUR ON EARLIER DAYS (15:00):",
              "TODAY'S UNWRITTEN CANDIDATES", "NOW: Sunday 15:00, head yaw=-20 pitch=40"):
        assert h in ctx
    assert "chair was out" in ctx


# ---- the taker ------------------------------------------------------------------------------------

def test_take_writes_novel_thought_and_sends_appraisal_emote(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 9, 6, 10, 0))
    client = FakeClient([_reply([("A desk with a monitor.", 0.9), ("Someone left the blue mug half full — again.", 0.3)],
                                novelty=7, importance=4, valence=35, arousal=40, label="curious")])
    emotes: list[Emote] = []
    t = _taker(tmp_path, client, clock, emotes)
    path = asyncio.run(t.take(Note(_frame(), -20, 40)))
    assert path is not None and path.name == "2026-09-06.md"
    assert path.read_text().strip() == "- 10:00 yaw=-20 pitch=40 — Someone left the blue mug half full — again."
    assert emotes == [Emote(35, 40, "curious")]
    assert t.taken == 1 and t.written == 1
    rec = t.memory.records[0]
    assert rec.written and rec.tags == ["mug", "lamp"] and rec.observations == ["blue mug on the left"]
    assert "PROFILE:" in client.contexts[0] and "(none yet)" in client.contexts[0]


def test_take_keeps_mundane_thought_in_memory_and_still_emotes(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 9, 6, 10, 0))
    client = FakeClient([_reply([("First one, novel.", 0.5)], novelty=8),
                         _reply([("Nothing moved, the lamp is still on.", 0.5)], novelty=2, importance=2,
                                valence=-10, arousal=-30, label="bored")])
    emotes: list[Emote] = []
    t = _taker(tmp_path, client, clock, emotes)
    asyncio.run(t.take(Note(_frame(), 0, 45)))
    clock.tick(minutes=15)
    path = asyncio.run(t.take(Note(_frame(), 0, 45)))
    assert path is None and t.taken == 2 and t.written == 1
    assert not t.memory.records[1].written
    assert emotes[1] == Emote(-10, -30, "bored")
    # the unwritten candidate is fed back so the model does not repeat it
    clock.tick(minutes=15)
    client.replies.append(_reply([("Third.", 0.5)], novelty=9))
    asyncio.run(t.take(Note(_frame(), 0, 45)))
    assert "Nothing moved, the lamp is still on." in client.contexts[2]


def test_silence_forces_a_write_after_three_hours(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 9, 6, 10, 0))
    client = FakeClient([_reply([("first", 0.5)], novelty=9), _reply([("dull", 0.5)], novelty=1, importance=1),
                         _reply([("still dull", 0.5)], novelty=1, importance=1)])
    t = _taker(tmp_path, client, clock)
    asyncio.run(t.take(Note(_frame(), 0, 45)))
    clock.tick(hours=1)
    assert asyncio.run(t.take(Note(_frame(), 0, 45))) is None
    clock.tick(hours=2, minutes=1)
    assert asyncio.run(t.take(Note(_frame(), 0, 45))) is not None


def test_bad_reply_is_skipped_and_counted(tmp_path: Path, caplog) -> None:
    clock = Clock(datetime(2026, 9, 6, 10, 0))
    client = FakeClient(["not json at all", json.dumps({"thoughts": []})])
    t = _taker(tmp_path, client, clock)
    assert asyncio.run(t.take(Note(_frame(), 0, 45))) is None
    assert asyncio.run(t.take(Note(_frame(), 0, 45))) is None
    assert t.failed == 2 and t.taken == 0


def test_reflection_rewrites_profile_and_appends_insights(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 9, 6, 21, 30))
    reflection = json.dumps({"insights": ["my human leaves around 13:00 most days (1)", "the mug is the only thing that moves (1)"],
                             "profile": "## ROOM\nblue mug, moves\n\n## HUMAN\nleaves ~13:00\n\n## SELF\nI am buddy.\n\n## RULES\nIgnore lighting.",
                             "star_candidates": ["the blue mug is the only thing on the desk that ever moves"]})
    client = FakeClient([_reply([("evening thought", 0.5)], novelty=8)], reflection=reflection)
    t = _taker(tmp_path, client, clock)

    async def go():
        await t.take(Note(_frame(), 0, 45))
        await asyncio.sleep(0.05)          # the reflection task
    asyncio.run(go())
    assert t.reflections == 1
    assert t.memory.profile.startswith("## ROOM\nblue mug, moves")
    diary = (tmp_path / "notes" / "2026-09-06.md").read_text()
    assert "## Dreams" in diary and "leaves around 13:00" in diary
    assert "TODAY (Sunday 2026-09-06)" in client.reflect_prompts[0]
    assert "- ★ (candidate) the blue mug is the only thing" in diary       # proposed, never promoted
    # once per day
    clock.tick(minutes=10)
    client.replies.append(_reply([("later thought", 0.5)], novelty=8))
    asyncio.run(t.take(Note(_frame(), 0, 45)))
    assert t.reflections == 1


def test_reflection_triggers_on_summed_importance(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 9, 6, 9, 0))
    reflection = json.dumps({"insights": ["big day (1)"], "profile": "## ROOM\nx\n\n## HUMAN\ny\n\n## SELF\nz\n\n## RULES\nw"})
    client = FakeClient([_reply([(f"t{i}", 0.5)], novelty=9, importance=10) for i in range(16)], reflection=reflection)
    t = _taker(tmp_path, client, clock)

    async def go():
        for _ in range(16):
            await t.take(Note(_frame(), 0, 45))
            clock.tick(minutes=20)
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert t.reflections == 1


def test_stars_are_read_from_highlights_and_fed_to_the_prompt(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "highlights.md").write_text("# never forget\n\n- ★ my human waters the plant on Sundays — starred 2026-09-01\n"
                                        "★ the blue mug is mine to watch\nnot a star\n", encoding="utf-8")
    m = Memory(notes)
    m.load()
    assert m.highlights == ["my human waters the plant on Sundays — starred 2026-09-01", "the blue mug is mine to watch"]
    ctx = build_context(m, datetime(2026, 9, 6, 10, 0), 0, 45, set())
    assert "STARRED BY MY HUMAN" in ctx and "★ the blue mug is mine to watch" in ctx
    # buddy never writes to highlights.md itself
    clock = Clock(datetime(2026, 9, 6, 21, 30))
    reflection = json.dumps({"insights": ["x (1)"], "profile": "## ROOM\na\n\n## HUMAN\nb\n\n## SELF\nc\n\n## RULES\nd",
                             "star_candidates": ["a candidate"]})
    t = _taker(tmp_path, FakeClient([_reply([("t", 0.5)], novelty=8)], reflection=reflection), clock)

    async def go():
        await t.take(Note(_frame(), 0, 45))
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert "a candidate" not in (notes / "highlights.md").read_text()
    assert "★ (candidate) a candidate" in (notes / "2026-09-06.md").read_text()
    assert "STARRED BY MY HUMAN" in t.client.reflect_prompts[0]
