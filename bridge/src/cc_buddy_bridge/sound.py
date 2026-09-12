"""Sound: the owner can mute buddy. Muted means silent, not still — the head
and the LEDs keep moving; only sound stops.

Every sound buddy makes comes from the board's chirp synth, and the firmware
already gates all of it on its persisted ``sound`` setting (NVS key
``s_snd``). The host sets that setting with ``{"cmd":"sound","on":bool}`` and
re-sends it on every connect, so a reflashed or replaced board comes back
with the owner's choice. The choice itself is kept here, in
``~/.config/cc-buddy-bridge/sound.json``, so it survives a daemon restart.

Two host-side belts on top of the firmware switch: caption pages go out with
``"chirp": false`` while muted (older firmware without the command stays
quiet during a conversation), and the voice never plays audio on the Mac
speaker in ``CC_BUDDY_VOICE_OUTPUT=audio`` mode.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def default_path(environ: Any = None) -> Path:
    env = os.environ if environ is None else environ
    raw = (env.get("CC_BUDDY_SOUND_FILE") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "cc-buddy-bridge" / "sound.json"


def build_sound_cmd(on: bool) -> dict[str, Any]:
    return {"cmd": "sound", "on": bool(on)}


def quiet_caption(msg: dict[str, Any], muted: bool) -> dict[str, Any]:
    """A caption page without its talk chirp while muted; unchanged otherwise."""
    if muted and msg.get("chirp"):
        return {**msg, "chirp": False}
    return msg


class SoundSetting:
    """On or off, persisted. A missing or unreadable file means on."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or default_path()
        self.on = True

    @property
    def muted(self) -> bool:
        return not self.on

    def load(self) -> bool:
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            self.on = not bool(obj.get("muted", False))
        except FileNotFoundError:
            self.on = True
        except (OSError, ValueError) as e:
            log.warning("sound: cannot read %s (%s) — sound stays on", self.path, e)
            self.on = True
        return self.on

    def set(self, on: bool) -> bool:
        """Change and persist. Returns True when the value changed."""
        changed = bool(on) != self.on
        self.on = bool(on)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"muted": not self.on}) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("sound: cannot save %s (%s) — the choice lasts until the daemon restarts", self.path, e)
        return changed
