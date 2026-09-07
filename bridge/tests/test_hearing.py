"""hearing.py: the host's memory of what the room sounded like. Pure — a
hand-rolled clock, no board."""

from __future__ import annotations

from cc_buddy_bridge.hearing import Hearing, Sound, parse

QUIET = {"rms": 20, "peak": 24, "quiet": 18}
SMALL = {"rms": 32, "peak": 40, "quiet": 18}
BANG = {"rms": 55, "peak": 88, "quiet": 18}


def test_parse_reads_a_reading_and_refuses_anything_else() -> None:
    s = parse(QUIET, 5.0)
    assert s == Sound(rms=20, peak=24, quiet=18, at=5.0)
    assert parse({"rms": 1}, 0.0) is None          # no peak
    assert parse({"rms": "loud", "peak": 1}, 0.0) is None
    assert parse(None, 0.0) is None and parse([], 0.0) is None
    # quiet is optional; a board that has not learned a floor yet says nothing
    assert parse({"rms": 5, "peak": 6}, 0.0).quiet == 0


def test_readings_are_clamped_to_the_scale() -> None:
    s = parse({"rms": 999, "peak": -4, "quiet": 200}, 0.0)
    assert (s.rms, s.peak, s.quiet) == (100, 0, 100)


def test_above_floor_is_loudness_for_this_room() -> None:
    assert parse(QUIET, 0.0).above_floor == 2
    assert parse(BANG, 0.0).above_floor == 37
    # A room whose floor is already high is not startled by the same number.
    assert parse({"rms": 55, "peak": 60, "quiet": 54}, 0.0).above_floor == 1


def test_a_quiet_room_reads_as_quiet_and_a_bang_as_something_loud() -> None:
    e = Hearing()
    e.hear(parse(QUIET, 0.0))
    assert e.describe(1.0) == "the room is as quiet as it usually is"
    assert not e.is_notable(1.0)
    e.hear(parse(SMALL, 2.0))
    assert "something small" in e.describe(3.0)
    assert e.is_notable(3.0)
    e.hear(parse(BANG, 4.0))
    assert "loud" in e.describe(5.0)


def test_nothing_is_said_without_a_recent_reading() -> None:
    e = Hearing()
    assert e.describe(0.0) is None                 # never heard anything
    assert not e.is_notable(0.0)
    e.hear(parse(QUIET, 0.0))
    assert e.describe(e.stale_secs + 1) is None    # the board went quiet on us
    assert e.fresh(e.stale_secs + 1) is None


def test_it_reports_the_loudest_moment_at_this_waypoint_not_the_latest() -> None:
    """A bang while the head was settling is the news, even if the room went
    quiet again by the time the frame was taken."""
    e = Hearing()
    e.hear(parse(QUIET, 0.0))
    e.hear(parse(BANG, 2.0))
    e.hear(parse(QUIET, 4.0))
    assert "loud" in (e.describe(5.0, since=1.0) or "")
    # ... but a bang from before this waypoint is not this waypoint's news.
    assert e.describe(5.0, since=3.0) == "the room is as quiet as it usually is"


def test_the_loudest_since_a_moment() -> None:
    e = Hearing()
    for at, obj in ((0.0, QUIET), (1.0, BANG), (2.0, SMALL)):
        e.hear(parse(obj, at))
    assert e.loudest_since(0.0).peak == 88
    assert e.loudest_since(1.5).peak == 40
    assert e.loudest_since(99.0) is None


def test_history_is_bounded() -> None:
    e = Hearing(history=5)
    for i in range(50):
        e.hear(parse(QUIET, float(i)))
    assert len(e.readings) == 5


def test_the_phrase_never_guesses_what_made_the_sound() -> None:
    """buddy has a loudness number, not a classifier."""
    e = Hearing()
    e.hear(parse(BANG, 0.0))
    said = e.describe(1.0) or ""
    for invented in ("door", "voice", "music", "someone", "phone"):
        assert invented not in said


def test_an_all_zero_reading_is_not_evidence_of_a_silent_room() -> None:
    """A live mic in a real room never returns exactly nothing; that pattern
    means the codec is dead, and buddy must not write about a hush it cannot
    hear (bench 2026-09-06: the ES7210 reported itself enabled and handed back
    digital silence)."""
    assert parse({"rms": 0, "peak": 0, "quiet": 0}, 0.0) is None
    assert parse({"rms": 0, "peak": 1, "quiet": 0}, 0.0) is not None
    e = Hearing()
    for _ in range(5):
        reading = parse({"rms": 0, "peak": 0}, 0.0)
        if reading is not None:
            e.hear(reading)
    assert e.describe(1.0) is None and not e.is_notable(1.0)
