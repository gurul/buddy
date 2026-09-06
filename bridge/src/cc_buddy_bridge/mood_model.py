"""Reference model of the board's affect engine (firmware/.../mood.cpp).

A line-for-line port of MoodEngine so the rules can be unit-tested here and
the two implementations compared on a scripted trace (the parity check in
the bridge tests). Keep the two in step: change a constant in one, change it
in the other.

Model in one paragraph: valence V and arousal A (fast emotion, tau 60 s)
relax toward a slow mood baseline (tau 1 h) that drifts toward them; two
drives — social and stimulation — rise when unsatisfied and, past their
regime thresholds, drag V down (lonely) or A down (bored); stimuli bump V/A
by fixed deltas (motion, faces, the owner, touch, arriving at a new view, a
host appraisal clamped to ±0.3); a sudden motion while calm is a startle.
The discrete kind is read off (V, A) and the recent events; expression
parameters are read off V, A and the kind.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

CALM, CURIOUS, HAPPY, SURPRISED, STARTLED, BORED, LONELY, AFFECTION = range(8)
KIND_NAMES = ["calm", "curious", "happy", "surprised", "startled", "bored", "lonely", "affection"]
WORDS = {CURIOUS: "curious...", HAPPY: "happy", SURPRISED: "!", STARTLED: "eek!", BORED: "bored...",
         LONELY: "lonely...", AFFECTION: "<3", CALM: "exploring..."}

CHIRP_NONE, CHIRP_CURIOUS, CHIRP_SURPRISE, CHIRP_SIGH, CHIRP_WARBLE, CHIRP_STARTLE = range(6)

TAU_EMOTION_S = 60.0
TAU_MOOD_S = 3600.0
SOCIAL_RISE_PER_S = 0.0004
STIM_RISE_PER_S = 0.001
HOST_EMOTE_CLAMP = 0.3
STARTLE_COOLDOWN_MS = 20000
CHIRP_COOLDOWN_MS = 8000


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def decay_toward(x: float, target: float, dt_s: float, tau_s: float) -> float:
    return target + (x - target) * math.exp(-dt_s / tau_s)


def hsv(h: float, s: float, val: float) -> tuple[int, int, int]:
    c = val * s
    x = c * (1.0 - abs(math.fmod(h / 60.0, 2.0) - 1.0))
    m = val - c
    rr = gg = bb = 0.0
    if h < 60:
        rr, gg = c, x
    elif h < 120:
        rr, gg = x, c
    elif h < 180:
        gg, bb = c, x
    elif h < 240:
        gg, bb = x, c
    elif h < 300:
        rr, bb = x, c
    else:
        rr, bb = c, x
    return (int((rr + m) * 255.0 + 0.5), int((gg + m) * 255.0 + 0.5), int((bb + m) * 255.0 + 0.5))


@dataclass
class MoodInput:
    exploring: bool = True
    asleep: bool = False
    motion_conf: int = 0
    face_seen: bool = False
    face_owner: bool = False
    touched: bool = False
    new_view: bool = False
    host_emote: bool = False
    dv: int = 0
    da: int = 0


@dataclass
class MoodExpr:
    v: float = 0.0
    a: float = 0.0
    kind: int = CALM
    openness: int = 55
    happy: bool = False
    tired: bool = False
    curious: bool = False
    flicker: bool = False
    blink_secs: int = 2
    saccade_secs: int = 2
    r: int = 0
    g: int = 0
    b: int = 0
    pulse_hz: float = 0.5
    tempo: float = 1.0
    pitch_bias: int = 0
    amplitude: int = 30
    chirp: int = CHIRP_NONE
    word: str = "exploring..."


@dataclass
class MoodEngine:
    v: float = 0.0
    a: float = 0.0
    mood_v: float = 0.05
    mood_a: float = 0.0
    social: float = 0.5
    stimulation: float = 0.5
    since_face_ms: int = 600000
    since_motion_ms: int = 600000
    since_touch_ms: int = 600000
    since_owner_ms: int = 600000
    since_surprise_ms: int = 600000
    since_startle_ms: int = 600000
    since_chirp_ms: int = 600000
    kind: int = CALM
    last_kind: int = CALM
    expr: MoodExpr = field(default_factory=MoodExpr)

    def step(self, inp: MoodInput, dt_ms: int) -> int:
        dt_ms = min(int(dt_ms), 5000)
        dt = dt_ms / 1000.0
        self.since_face_ms += dt_ms
        self.since_motion_ms += dt_ms
        self.since_touch_ms += dt_ms
        self.since_owner_ms += dt_ms
        self.since_surprise_ms += dt_ms
        self.since_startle_ms += dt_ms
        self.since_chirp_ms += dt_ms

        if inp.motion_conf > 0:
            m = inp.motion_conf / 100.0
            sudden = inp.motion_conf >= 60 and self.since_motion_ms > 3000 and self.a < 0.3
            if sudden and not inp.asleep and self.since_startle_ms > STARTLE_COOLDOWN_MS:
                self.a += 0.7
                self.v -= 0.35
                self.since_startle_ms = 0
            else:
                self.a += 0.2 * m * dt * 4.0
            self.stimulation -= 0.3 * m * dt
            self.since_motion_ms = 0
        if inp.face_seen:
            self.since_face_ms = 0
            self.social -= 0.4 * dt * 2.0
            if inp.face_owner:
                if self.since_owner_ms > 20000:
                    self.v += 0.4
                    self.a += 0.2
                self.since_owner_ms = 0
            elif self.since_face_ms == 0 and self.since_motion_ms > 500:
                self.a += 0.15 * dt * 4.0
                self.v += 0.05 * dt * 4.0
        if inp.touched:
            self.v += 0.2
            self.a += 0.1
            self.social -= 0.2
            self.since_touch_ms = 0
        if inp.new_view:
            self.a += 0.1
            self.stimulation -= 0.15
            if self.a > 0.25:
                self.since_surprise_ms = 0
        if inp.host_emote:
            self.v += clamp(inp.dv / 100.0, -HOST_EMOTE_CLAMP, HOST_EMOTE_CLAMP)
            self.a += clamp(inp.da / 100.0, -HOST_EMOTE_CLAMP, HOST_EMOTE_CLAMP)

        if self.since_face_ms > 30000 and self.since_touch_ms > 30000:
            self.social += SOCIAL_RISE_PER_S * dt
        if self.since_motion_ms > 15000 and not inp.new_view:
            self.stimulation += STIM_RISE_PER_S * dt
        self.social = clamp(self.social, 0.0, 1.0)
        self.stimulation = clamp(self.stimulation, 0.0, 1.0)
        if self.stimulation > 0.6:
            k = clamp((self.stimulation - 0.6) / 0.1, 0.0, 1.0)
            self.a = decay_toward(self.a, -0.6 * k, dt, 20.0)
        if self.social > 0.7:
            k = clamp((self.social - 0.7) / 0.1, 0.0, 1.0)
            self.v = decay_toward(self.v, -0.7 * k, dt, 20.0)
        if inp.asleep:
            self.a = decay_toward(self.a, -0.6, dt, 20.0)

        self.v = decay_toward(self.v, self.mood_v, dt, TAU_EMOTION_S)
        self.a = decay_toward(self.a, self.mood_a, dt, TAU_EMOTION_S)
        self.mood_v = decay_toward(self.mood_v, self.v, dt, TAU_MOOD_S)
        self.mood_a = decay_toward(self.mood_a, self.a, dt, TAU_MOOD_S)
        self.v = clamp(self.v, -1.0, 1.0)
        self.a = clamp(self.a, -1.0, 1.0)
        self.mood_v = clamp(self.mood_v, -0.5, 0.5)
        self.mood_a = clamp(self.mood_a, -0.5, 0.5)

        self._classify()
        self._express()
        return self.kind

    def _classify(self) -> None:
        v, a = self.v, self.a
        if self.since_startle_ms < 1500:
            k = STARTLED
        elif a > 0.6 and v < -0.2:
            k = STARTLED
        elif self.since_surprise_ms < 2500 and a > 0.3:
            k = SURPRISED
        elif self.since_owner_ms < 20000 and v > 0.2:
            k = AFFECTION
        elif v > 0.4 and a > -0.2:
            k = HAPPY
        elif a > 0.2 and v >= -0.2:
            k = CURIOUS
        elif v < -0.4 and a < 0.2:
            k = LONELY
        elif a < -0.35 and v >= -0.4:
            k = BORED
        else:
            k = CALM
        self.kind = k

    def _express(self) -> None:
        v, a, kind = self.v, self.a, self.kind
        e = MoodExpr(v=v, a=a, kind=kind)
        e.openness = int(clamp((0.55 + 0.4 * a) * 100.0, 25.0, 100.0))
        e.happy = v > 0.3
        e.tired = (a < -0.4) or kind == LONELY
        e.curious = kind in (CURIOUS, SURPRISED, AFFECTION)
        e.flicker = kind == STARTLED
        blink = 1.5 / (1.0 + clamp(a, -0.5, 1.0)) + 1.0
        e.blink_secs = int(clamp(blink, 1.0, 6.0))
        e.saccade_secs = int(clamp(2.0 / (1.0 + 1.5 * clamp(a, -0.5, 1.0)) + 0.5, 1.0, 6.0))
        hue = (170.0 if a < 0 else 120.0) if v >= 0 else 20.0
        if kind == AFFECTION:
            hue = 320.0
        if kind == STARTLED:
            hue = 0.0
        sat = clamp(0.4 + 0.5 * abs(v), 0.0, 1.0)
        bri = clamp(0.3 + 0.6 * (a + 1.0) / 2.0, 0.1, 1.0)
        e.r, e.g, e.b = hsv(hue, sat, bri)
        e.pulse_hz = 2.5 if a > 0.4 else 0.25 if a < -0.3 else 0.5
        e.tempo = clamp(1.0 + 0.6 * a, 0.5, 1.6)
        e.amplitude = int(clamp(30.0 + 15.0 * a, 15.0, 45.0))
        e.pitch_bias = 10 if kind in (SURPRISED, STARTLED) else -10 if kind in (LONELY, BORED) else 0
        e.chirp = CHIRP_NONE
        if kind != self.last_kind and self.since_chirp_ms > CHIRP_COOLDOWN_MS:
            e.chirp = {CURIOUS: CHIRP_CURIOUS, SURPRISED: CHIRP_SURPRISE, STARTLED: CHIRP_STARTLE,
                       BORED: CHIRP_SIGH, LONELY: CHIRP_SIGH, AFFECTION: CHIRP_WARBLE,
                       HAPPY: CHIRP_WARBLE}.get(kind, CHIRP_NONE)
            if e.chirp != CHIRP_NONE:
                self.since_chirp_ms = 0
        self.last_kind = kind
        e.word = WORDS[kind]
        self.expr = e


def trace_line(inp: MoodInput, dt_ms: int) -> str:
    """The host harness's input line format (mood_ref.cpp)."""
    return (f"{dt_ms} {int(inp.exploring)} {int(inp.asleep)} {inp.motion_conf} {int(inp.face_seen)} "
            f"{int(inp.face_owner)} {int(inp.touched)} {int(inp.new_view)} {int(inp.host_emote)} {inp.dv} {inp.da}")
