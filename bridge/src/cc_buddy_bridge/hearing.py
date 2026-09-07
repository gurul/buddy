"""What buddy hears while it looks around.

The camera says what the room looks like; this says whether it is quiet. A
desk that has been silent all afternoon and a desk where a door just banged
look identical in a 320x240 frame, and the difference is most of what makes a
moment worth remembering.

The board sends, at most every couple of seconds while explore mode is on
(``firmware/claude_pet_stackchan/src/hearing.cpp``):

    {"sound":{"rms":0..100,"peak":0..100,"quiet":0..100}}

``rms`` and ``peak`` are the last 48 ms window on a decibel scale; ``quiet``
is the floor the board has learned for this room. A reading of exactly zero on
both is discarded rather than believed: a live microphone in a real room never
returns nothing at all, so that pattern means the codec is not working, and
"buddy hears nothing" must not be reported as "the room is silent". All three are already
relative to the room, which matters: 40 is loud in a study and unremarkable in
a kitchen, so nothing here compares against an absolute number.

This module is the host's memory of those readings. It keeps a short history,
says how loud the room is *for this room*, and turns that into the one English
phrase the diary needs. Pure: no clock of its own, no I/O.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

HISTORY = 30                 # about a minute of readings
STALE_SECS = 30.0            # older than this and buddy has not heard anything lately
# How far above the room's own floor counts as a sound worth mentioning. The
# board's scale is 0..100 over 60 dB, so 10 points is 6 dB — about a doubling
# of loudness, the smallest step a person reliably calls "louder".
NOTICEABLE = 10
LOUD = 25


@dataclass(frozen=True)
class Sound:
    """One reading, as it came off the wire."""

    rms: int
    peak: int
    quiet: int
    at: float

    @property
    def above_floor(self) -> int:
        """How far this reading rises over what the room usually sounds like."""
        return max(0, self.rms - self.quiet)


def parse(obj: Any, at: float) -> Optional[Sound]:
    """Turn a ``sound`` object off the wire into a reading, or None if it is
    not one. A malformed line is never worth an exception on the read path."""
    if not isinstance(obj, dict):
        return None
    try:
        rms = int(obj["rms"])
        peak = int(obj["peak"])
        quiet = int(obj.get("quiet", 0))
    except (KeyError, TypeError, ValueError):
        return None
    clamp = lambda v: max(0, min(100, v))  # noqa: E731
    rms, peak = clamp(rms), clamp(peak)
    if rms == 0 and peak == 0:
        # Not a silent room: a real microphone in a real room never returns
        # exactly nothing. This is the shape a dead codec has (bench
        # 2026-09-06, where the ES7210 reported itself enabled and handed back
        # digital silence), and reading it as "quiet" would have buddy write
        # confident diary lines about a hush it cannot actually hear.
        return None
    return Sound(rms=rms, peak=peak, quiet=clamp(quiet), at=at)


@dataclass
class Hearing:
    """The host's short memory of what the room sounded like.

    Not to be confused with ``ears.Ears``, which is the Mac microphone and the
    wake word. This is the robot's own ear, and it only ever measures loudness.
    """

    history: int = HISTORY
    stale_secs: float = STALE_SECS
    noticeable: int = NOTICEABLE
    loud: int = LOUD
    readings: deque[Sound] = field(default_factory=lambda: deque(maxlen=HISTORY))

    def __post_init__(self) -> None:
        # The deque's own cap has to follow `history`, not the module default.
        if self.readings.maxlen != self.history:
            self.readings = deque(self.readings, maxlen=self.history)

    def hear(self, sound: Sound) -> None:
        self.readings.append(sound)

    @property
    def latest(self) -> Optional[Sound]:
        return self.readings[-1] if self.readings else None

    def fresh(self, now: float) -> Optional[Sound]:
        """The latest reading, if it is recent enough to mean anything."""
        last = self.latest
        if last is None or now - last.at > self.stale_secs:
            return None
        return last

    def loudest_since(self, since: float) -> Optional[Sound]:
        """The loudest reading taken since a moment — what buddy heard while
        it was settling on this waypoint."""
        candidates = [s for s in self.readings if s.at >= since]
        return max(candidates, key=lambda s: s.peak) if candidates else None

    def describe(self, now: float, since: Optional[float] = None) -> Optional[str]:
        """One phrase for the diary, or None when there is nothing to say.

        Deliberately vague about what the sound *was*: buddy has a loudness
        reading, not a classifier, and a diary that guesses "a door" from a
        number would be making things up.
        """
        heard = self.loudest_since(since) if since is not None else None
        last = self.fresh(now)
        if heard is None:
            heard = last
        if heard is None or now - heard.at > self.stale_secs:
            return None
        over = max(heard.above_floor, max(0, heard.peak - heard.quiet))
        if over >= self.loud:
            return "something loud happened while you were looking"
        if over >= self.noticeable:
            return "you heard something small over the room's usual hum"
        return "the room is as quiet as it usually is"

    def is_notable(self, now: float, since: Optional[float] = None) -> bool:
        """True when the sound is worth the diary knowing about at all."""
        heard = self.loudest_since(since) if since is not None else self.fresh(now)
        if heard is None:
            return False
        return max(heard.above_floor, max(0, heard.peak - heard.quiet)) >= self.noticeable
