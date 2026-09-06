"""The affect engine's rules, on the Python reference model (mood_model.py),
which mirrors firmware mood.cpp line for line (parity is checked separately)."""

from __future__ import annotations

from cc_buddy_bridge.mood_model import (
    AFFECTION,
    BORED,
    CALM,
    CHIRP_CURIOUS,
    CHIRP_NONE,
    CHIRP_SIGH,
    CHIRP_STARTLE,
    CURIOUS,
    HAPPY,
    LONELY,
    STARTLED,
    SURPRISED,
    MoodEngine,
    MoodInput,
    hsv,
)


def _run(m: MoodEngine, inp: MoodInput, seconds: float, dt_ms: int = 100) -> int:
    k = m.kind
    for _ in range(int(seconds * 1000 / dt_ms)):
        k = m.step(inp, dt_ms)
    return k


def test_starts_calm_and_slightly_cheerful() -> None:
    m = MoodEngine()
    assert m.step(MoodInput(), 100) == CALM
    assert m.expr.word == "exploring..." and m.expr.chirp == CHIRP_NONE
    assert m.mood_v > 0            # personality: a positive baseline


def test_motion_makes_it_curious_then_it_relaxes() -> None:
    m = MoodEngine()
    k = _run(m, MoodInput(motion_conf=40), 3.0)
    assert k == CURIOUS and m.a > 0.2 and m.expr.curious and m.expr.word == "curious..."
    assert m.expr.pulse_hz >= 0.5 and m.expr.tempo > 1.0
    k = _run(m, MoodInput(), 240.0)           # 4 minutes of nothing: relax toward the mood
    assert m.a < 0.1 and k in (CALM, BORED)


def test_sudden_motion_while_calm_startles_with_cooldown() -> None:
    m = MoodEngine()
    _run(m, MoodInput(), 5.0)
    assert m.step(MoodInput(motion_conf=90), 100) == STARTLED
    assert m.expr.flicker and m.expr.word == "eek!" and m.expr.chirp == CHIRP_STARTLE
    assert m.expr.r > m.expr.g and m.expr.pitch_bias == 10        # red, head up
    # a second jolt inside the cool-down is just interest, not another startle
    _run(m, MoodInput(), 5.0)
    startles_before = m.since_startle_ms
    m.step(MoodInput(motion_conf=90), 100)
    assert m.since_startle_ms == startles_before + 100


def test_owner_face_brings_affection_once_per_visit() -> None:
    m = MoodEngine()
    m.step(MoodInput(face_seen=True, face_owner=True), 100)
    assert m.kind == AFFECTION and m.v > 0.3 and m.expr.word == "<3"
    assert (m.expr.r, m.expr.g, m.expr.b)[0] > 150            # pink-ish
    v1 = m.v
    m.step(MoodInput(face_seen=True, face_owner=True), 100)   # still here: no second jump
    assert m.v < v1 + 0.05


def test_touch_is_pleasant_and_feeds_the_social_drive() -> None:
    m = MoodEngine()
    m.social = 0.9
    m.step(MoodInput(touched=True), 100)
    assert m.v > 0.15 and m.social < 0.75


def test_alone_and_quiet_becomes_bored_then_lonely() -> None:
    m = MoodEngine()
    m.since_face_ms = m.since_touch_ms = m.since_motion_ms = 600000
    k = _run(m, MoodInput(), 240.0, dt_ms=500)       # 4 quiet minutes
    assert m.stimulation > 0.6 and k == BORED
    assert m.expr.tired and m.expr.pitch_bias == -10 and m.expr.pulse_hz == 0.25
    k = _run(m, MoodInput(), 900.0, dt_ms=500)       # a quarter hour more: nobody came
    assert m.social > 0.7 and k == LONELY and m.expr.word == "lonely..."


def test_a_new_view_while_keen_is_a_small_surprise() -> None:
    m = MoodEngine()
    _run(m, MoodInput(motion_conf=50), 4.0)
    assert m.a > 0.25
    assert m.step(MoodInput(new_view=True), 100) == SURPRISED
    assert m.expr.word == "!" and m.expr.openness > 60


def test_host_appraisal_is_clamped() -> None:
    m = MoodEngine()
    m.step(MoodInput(host_emote=True, dv=100, da=100), 100)
    assert 0.29 < m.v < 0.35 and 0.29 < m.a < 0.32
    m2 = MoodEngine()
    m2.step(MoodInput(host_emote=True, dv=-100, da=-100), 100)
    assert m2.v < -0.25 and m2.a < -0.25


def test_happy_after_good_news() -> None:
    m = MoodEngine()
    m.step(MoodInput(host_emote=True, dv=60, da=10), 100)
    m.step(MoodInput(host_emote=True, dv=60, da=10), 100)
    assert m.kind == HAPPY and m.expr.happy and m.expr.g > m.expr.r


def test_one_chirp_per_kind_change_with_cooldown() -> None:
    m = MoodEngine()
    _run(m, MoodInput(motion_conf=40), 3.0)
    chirps = [m.expr.chirp]
    k = m.kind
    # find the step where it turned curious: exactly one chirp was emitted
    m2 = MoodEngine()
    seen = []
    for _ in range(40):
        m2.step(MoodInput(motion_conf=40), 100)
        if m2.expr.chirp != CHIRP_NONE:
            seen.append(m2.expr.chirp)
    assert seen == [CHIRP_CURIOUS] and k == CURIOUS and chirps
    # boredom's sigh: quiet for a long time, still only one
    m3 = MoodEngine()
    m3.since_face_ms = m3.since_touch_ms = m3.since_motion_ms = 600000
    seen = []
    for _ in range(600):
        m3.step(MoodInput(), 500)
        if m3.expr.chirp != CHIRP_NONE:
            seen.append(m3.expr.chirp)
    assert seen[:1] == [CHIRP_SIGH] and len(seen) <= 2


def test_sleep_relaxes_arousal_and_never_startles() -> None:
    m = MoodEngine()
    _run(m, MoodInput(motion_conf=50), 3.0)
    _run(m, MoodInput(asleep=True), 30.0)
    assert m.a < 0
    assert m.step(MoodInput(asleep=True, motion_conf=95), 100) != STARTLED


def test_expression_tracks_valence_and_arousal_monotonically() -> None:
    m = MoodEngine()
    lo = MoodEngine()
    lo.a, lo.v = -0.8, 0.0
    lo._classify()
    lo._express()
    hi = MoodEngine()
    hi.a, hi.v = 0.8, 0.0
    hi._classify()
    hi._express()
    assert lo.expr.openness < m.expr.openness < hi.expr.openness
    assert lo.expr.blink_secs > hi.expr.blink_secs
    assert lo.expr.saccade_secs > hi.expr.saccade_secs
    assert lo.expr.tempo < hi.expr.tempo and lo.expr.amplitude < hi.expr.amplitude
    assert (lo.expr.r + lo.expr.g + lo.expr.b) < (hi.expr.r + hi.expr.g + hi.expr.b)   # brighter when aroused


def test_hsv_primaries() -> None:
    assert hsv(0, 1, 1) == (255, 0, 0)
    assert hsv(120, 1, 1) == (0, 255, 0)
    assert hsv(240, 1, 1) == (0, 0, 255)
    assert hsv(0, 0, 0.5) == (128, 128, 128)
