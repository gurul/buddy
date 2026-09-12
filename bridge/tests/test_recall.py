"""recall.py: what buddy carries from one conversation into the next.

Everything here is a file read against a temporary store, so the tests are the
same speed as the code. The cases that matter are the refusals: a fresh store
ships a worked example about somebody else's payments bug, and buddy quoting that
back as a memory of its owner is the single worst failure this module can have.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from cc_buddy_bridge.recall import (
    EXAMPLE_MARK,
    MAX_BRIEF_CHARS,
    TEMPLATE_BANNER,
    RecallConfig,
    configured,
    note_conversation_time,
    opening_brief,
)
from cc_buddy_bridge.voice_agent import VoiceConfig, memory_block, session_config

NOW = datetime(2026, 9, 11, 21, 30)


def _cfg(tmp_path: Path) -> RecallConfig:
    return RecallConfig(store=tmp_path / "debrief", notes=tmp_path / "notes")


def _note(cfg: RecallConfig, name: str, body: str, day: str = "2026-09-10") -> Path:
    d = cfg.sessions_dir / day
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


BUDDY_NOTE = """---
status: machine-draft
source: buddy-voice
ended: 2026-09-10 22:10
---

# Talking about the flash script

## What was said

The owner asked twice about the right servo and I did not answer either time.

## Open threads

- buddy owes an answer about the right servo sticking
- the flash script is the slow part of a reflash
"""


# ---- nothing yet ---------------------------------------------------------------------

def test_an_empty_store_says_nothing(tmp_path: Path) -> None:
    """The first ever conversation must sound exactly like today. A robot that
    announces it has no memories is narrating its own plumbing."""
    assert opening_brief(_cfg(tmp_path), NOW) == ""


def test_a_missing_store_says_nothing_rather_than_raising(tmp_path: Path) -> None:
    cfg = RecallConfig(store=tmp_path / "nope" / "debrief", notes=tmp_path / "nope" / "notes")
    assert opening_brief(cfg, NOW) == ""


# ---- the gap -------------------------------------------------------------------------

def test_the_gap_is_named_the_way_a_person_would(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    for delta, expected in (
        (timedelta(hours=4), "earlier today"),
        (timedelta(days=1), "yesterday"),
        (timedelta(days=3), "3 days ago"),
        (timedelta(days=9), "last week"),
        (timedelta(days=40), "a while ago"),
    ):
        note_conversation_time(cfg, NOW - delta)
        assert opening_brief(cfg, NOW) == f"You last talked {expected}.", expected


def test_a_conversation_minutes_ago_is_not_worth_mentioning(tmp_path: Path) -> None:
    """Back-to-back conversations are one conversation to a person. Saying "you
    last talked earlier today" ninety seconds later is a robot with a stopwatch."""
    cfg = _cfg(tmp_path)
    note_conversation_time(cfg, NOW - timedelta(minutes=2))
    assert opening_brief(cfg, NOW) == ""


def test_a_broken_or_future_stamp_is_silence_not_an_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.notes.mkdir(parents=True)
    for junk in ("", "   ", "not a number", "9999999999999999999999"):
        cfg.stamp_path.write_text(junk, encoding="utf-8")
        assert opening_brief(cfg, NOW) == "", junk
    # a clock that went backwards, or a restored backup
    note_conversation_time(cfg, NOW + timedelta(days=2))
    assert opening_brief(cfg, NOW) == ""


def test_the_stamp_is_written_private_and_parseable(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    note_conversation_time(cfg, NOW)
    assert int(cfg.stamp_path.read_text().strip()) == int(NOW.timestamp())
    assert (cfg.notes.stat().st_mode & 0o777) == 0o700


# ---- what is carried over ------------------------------------------------------------

def test_a_debt_of_buddys_own_is_preferred_over_any_other_thread(tmp_path: Path) -> None:
    """The strongest thing a robot can open with is something it owes you."""
    cfg = _cfg(tmp_path)
    note_conversation_time(cfg, NOW - timedelta(days=1))
    _note(cfg, "2210-abc.md", BUDDY_NOTE)
    brief = opening_brief(cfg, NOW)
    assert brief.startswith("You last talked yesterday.")
    assert "owes an answer about the right servo" in brief
    assert "flash script" not in brief, "only one carried thing, and the debt wins"


def test_the_newest_conversation_wins(tmp_path: Path) -> None:
    import os
    import time

    cfg = _cfg(tmp_path)
    old = _note(cfg, "1000-old.md", BUDDY_NOTE.replace(
        "buddy owes an answer about the right servo sticking", "the old thing nobody cares about"))
    new = _note(cfg, "2300-new.md", BUDDY_NOTE.replace(
        "buddy owes an answer about the right servo sticking", "buddy owes a look at the camera mount"))
    now = time.time()
    os.utime(old, (now - 9000, now - 9000))
    os.utime(new, (now, now))
    assert "camera mount" in opening_brief(cfg, NOW)


def test_a_note_that_is_not_buddys_is_ignored(tmp_path: Path) -> None:
    """The store is buddy's own, for spoken conversations. A note that did not
    come from a conversation is not buddy's memory of its owner."""
    cfg = _cfg(tmp_path)
    _note(cfg, "2210-other.md", BUDDY_NOTE.replace("source: buddy-voice", "source: claude-code"))
    assert opening_brief(cfg, NOW) == ""


def test_the_installed_template_is_never_quoted_back(tmp_path: Path) -> None:
    """A fresh store ships a worked example about a payments retry loop. buddy
    repeating that as a memory of its owner is the worst thing in this module."""
    cfg = _cfg(tmp_path)
    _note(cfg, "2210-tmpl.md", TEMPLATE_BANNER + "\n" + BUDDY_NOTE)
    assert opening_brief(cfg, NOW) == ""

    cfg.store.mkdir(parents=True, exist_ok=True)
    (cfg.store / "2026-01-05-payments-retry-loop.md").write_text(
        f"# Payments\n\n## Open threads\n\n- add idempotency keys before retries {EXAMPLE_MARK}\n",
        encoding="utf-8")
    assert "idempotency" not in opening_brief(cfg, NOW)


def test_a_promoted_day_is_used_when_there_is_no_fresh_conversation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.store.mkdir(parents=True, exist_ok=True)
    (cfg.store / "2026-09-09-desk-and-the-guitar.md").write_text(
        "# The desk and the guitar\n\n## Open threads\n\n- the owner wants the guitar photographed properly\n",
        encoding="utf-8")
    assert "guitar photographed" in opening_brief(cfg, NOW)


def test_a_brief_is_never_a_question_and_never_a_bare_identifier(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _note(cfg, "2210-q.md", """---
source: buddy-voice
---
## Open threads

- did the owner ever say which servo?
- body.cpp:558
- the owner asked for the head to stop buzzing
""")
    brief = opening_brief(cfg, NOW)
    assert "?" not in brief
    assert "body.cpp" not in brief
    assert "stop buzzing" in brief


def test_a_long_brief_is_cut_to_length_on_a_word(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    note_conversation_time(cfg, NOW - timedelta(days=1))
    _note(cfg, "2210-long.md", "---\nsource: buddy-voice\n---\n## Open threads\n\n- buddy owes "
          + "a very long explanation about the servos " * 12 + "\n")
    brief = opening_brief(cfg, NOW)
    assert len(brief) <= MAX_BRIEF_CHARS
    assert not brief.endswith(" ")
    assert brief.endswith(".")


# ---- how it reaches the model --------------------------------------------------------

def test_no_memory_leaves_the_prompt_byte_identical(tmp_path: Path) -> None:
    """The safety property of the whole phase: an empty store cannot change how
    buddy behaves, so shipping this can only be felt once there is something to
    remember."""
    plain = session_config(VoiceConfig())
    assert session_config(VoiceConfig(), "") == plain
    assert session_config(VoiceConfig(), "   ") == plain
    assert memory_block("") == "" and memory_block("  ") == ""


def test_a_brief_reaches_the_prompt_with_the_rules_that_keep_it_short() -> None:
    cfg = session_config(VoiceConfig(), "You last talked yesterday. Still open from then: buddy owes an answer")
    text = cfg["instructions"]
    assert "You last talked yesterday" in text
    assert "buddy owes an answer" in text
    # the rules that stop it becoming a recital
    for rule in ("one clause", "never as a separate announcement", "what they said",
                 "never ask them to confirm"):
        assert rule in text, rule


def test_captions_mode_still_gets_its_own_instructions_after_the_memory() -> None:
    """Order matters: the caption rules are last, so they are the final thing the
    model reads about how to speak."""
    text = session_config(VoiceConfig(output="captions"), "You last talked yesterday.")["instructions"]
    assert text.index("You last talked yesterday") < text.index("17 characters")


def test_configured_reads_the_env_and_expands_the_paths() -> None:
    cfg = configured({"CC_BUDDY_DEBRIEF_DIR": "~/x/debrief", "CC_BUDDY_NOTES_DIR": "~/x/notes"})
    assert cfg.store.is_absolute() and cfg.store.name == "debrief"
    assert cfg.notes.is_absolute() and cfg.stamp_path.name == "last_conversation"
    assert "~" not in str(cfg.store)
    default = configured({})
    assert default.store.name == "debrief" and default.notes.name == "notes"
