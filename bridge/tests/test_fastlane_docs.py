"""The fast-lane docs name every knob, every result status and the helper, and the documented
defaults match the shipped constants (fast_lane.FAST_LANE_DEFAULT, fast_lane.DEFAULT_STYLE)."""

from __future__ import annotations

import re
from pathlib import Path

from cc_buddy_bridge.fast_lane import DEFAULT_STYLE, FAST_LANE_DEFAULT, STATUSES

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "docs" / "stackchan" / "voice.md"
KNOBS = ("CC_BUDDY_FAST_LANE", "CC_BUDDY_FAST_LANE_STYLE", "CC_BUDDY_LAYA_MODEL", "CC_BUDDY_LOCAL_VERIFY")


def _knob_row(text: str, knob: str) -> str:
    match = re.search(rf"^\| `{knob}` \| `([^`]*)` \|", text, re.M)
    assert match, f"no Knobs row for {knob}"
    return match.group(1)


def test_voice_doc_names_every_knob_status_and_the_helper() -> None:
    text = VOICE.read_text(encoding="utf-8")
    for knob in KNOBS:
        assert knob in text, knob
        _knob_row(text, knob)
    section = text[text.index("## The fast lane"):text.index("## Safety")]
    for status in STATUSES:
        assert re.search(rf"^{status}:", section, re.M), status
    assert "delegate(objective" in section and "`delegate`" in text
    for word in ("confirm", "escalate", "approve", "done_when", "shadow", "timing", "settle"):
        assert word in section, word


def test_documented_defaults_match_the_shipped_constants() -> None:
    text = VOICE.read_text(encoding="utf-8")
    assert _knob_row(text, "CC_BUDDY_FAST_LANE") == ("1" if FAST_LANE_DEFAULT else "0")
    assert _knob_row(text, "CC_BUDDY_FAST_LANE_STYLE") == DEFAULT_STYLE
    assert _knob_row(text, "CC_BUDDY_LOCAL_VERIFY") == "shadow"
    assert "laya-multilingual-mlx" in _knob_row(text, "CC_BUDDY_LAYA_MODEL")


def test_readmes_point_at_the_lane() -> None:
    root = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "fast_lane.py" in root and "decider.py" in root and "ax_candidates.py" in root and "[fast]" in root
    bridge = (ROOT / "bridge" / "README.md").read_text(encoding="utf-8")
    assert '".[fast]"' in bridge and "CC_BUDDY_FAST_LANE" in bridge and "voice.md#the-fast-lane" in bridge
