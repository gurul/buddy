"""notes_widget — the pure half: file discovery, parsing, ordering, rendering,
position persistence, and the widget's launchd unit.

No AppKit here. ``run()`` is exercised by hand (``cc-buddy-bridge notes-widget
--once``); importing the module must stay safe on Linux/Windows CI.
"""

from __future__ import annotations

import plistlib
import sys
from datetime import date
from pathlib import Path

import pytest

from cc_buddy_bridge import _service_launchd
from cc_buddy_bridge import notes_widget as nw

TODAY = date(2026, 9, 5)
YESTERDAY = date(2026, 9, 4)


def _write(directory: Path, day: date, lines: list[str]) -> Path:
    path = directory / f"{day.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- discovery -------------------------------------------------------------


def test_note_files_only_today_and_yesterday_newest_first(tmp_path):
    for day in (date(2026, 9, 1), YESTERDAY, TODAY):
        _write(tmp_path, day, ["- 09:00 yaw=+0 pitch=0 — old"])
    (tmp_path / "notes.txt").write_text("- 09:00 not a note file")
    files = nw.note_files(tmp_path, TODAY)
    assert [p.name for p in files] == ["2026-09-05.md", "2026-09-04.md"]


def test_note_files_skips_missing_days(tmp_path):
    _write(tmp_path, YESTERDAY, ["- 09:00 yaw=+0 pitch=0 — only yesterday"])
    assert [p.name for p in nw.note_files(tmp_path, TODAY)] == ["2026-09-04.md"]
    assert nw.note_files(tmp_path / "missing", TODAY) == []


def test_notes_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv(nw.NOTES_DIR_ENV, raising=False)
    assert nw.notes_dir() == nw.DEFAULT_NOTES_DIR
    monkeypatch.setenv(nw.NOTES_DIR_ENV, str(tmp_path))
    assert nw.notes_dir() == tmp_path


def test_day_of_parses_only_dated_filenames():
    assert nw.day_of(Path("2026-09-05.md")) == TODAY
    assert nw.day_of(Path("2026-13-05.md")) is None
    assert nw.day_of(Path("README.md")) is None


# --- parsing ---------------------------------------------------------------


def test_parse_notes_keeps_note_lines_in_file_order():
    text = (
        "# 2026-09-05\n"
        "\n"
        "- 10:02 yaw=+20 pitch=40 — a mug, still steaming\n"
        "not a note\n"
        "- 10:15 yaw=-10 pitch=30 — the window is open  \n"
    )
    notes = nw.parse_notes(text, TODAY)
    assert notes == [
        nw.Note(TODAY, "10:02", "yaw=+20 pitch=40 — a mug, still steaming"),
        nw.Note(TODAY, "10:15", "yaw=-10 pitch=30 — the window is open"),
    ]


def test_collect_notes_newest_first_across_days(tmp_path):
    _write(tmp_path, YESTERDAY, ["- 22:10 yaw=+0 pitch=0 — y1", "- 23:40 yaw=+0 pitch=0 — y2"])
    _write(tmp_path, TODAY, ["- 08:00 yaw=+0 pitch=0 — t1", "- 09:30 yaw=+0 pitch=0 — t2"])
    got = [(n.day, n.time) for n in nw.collect_notes(tmp_path, TODAY)]
    assert got == [(TODAY, "09:30"), (TODAY, "08:00"), (YESTERDAY, "23:40"), (YESTERDAY, "22:10")]


def test_collect_notes_applies_limit_after_ordering(tmp_path):
    _write(tmp_path, YESTERDAY, [f"- 0{i}:00 yaw=+0 pitch=0 — y{i}" for i in range(5)])
    _write(tmp_path, TODAY, [f"- 1{i}:00 yaw=+0 pitch=0 — t{i}" for i in range(5)])
    got = nw.collect_notes(tmp_path, TODAY, limit=3)
    assert [n.text for n in got] == ["yaw=+0 pitch=0 — t4", "yaw=+0 pitch=0 — t3", "yaw=+0 pitch=0 — t2"]
    assert len(nw.collect_notes(tmp_path, TODAY, limit=nw.MAX_NOTES)) == 10


def test_collect_notes_missing_dir_is_empty(tmp_path):
    assert nw.collect_notes(tmp_path / "nope", TODAY) == []


# --- rendering -------------------------------------------------------------


def test_render_text_empty_state():
    assert nw.render_text([], TODAY) == nw.EMPTY_TEXT
    assert "idle for 10 min" in nw.EMPTY_TEXT


def test_render_text_sections_per_day_with_headers():
    notes = [
        nw.Note(TODAY, "09:30", "yaw=+5 pitch=10 — t2"),
        nw.Note(TODAY, "08:00", "yaw=+5 pitch=10 — t1"),
        nw.Note(YESTERDAY, "23:40", "yaw=+5 pitch=10 — y1"),
    ]
    assert nw.render_text(notes, TODAY).splitlines() == [
        "Today · 2026-09-05",
        "09:30  yaw=+5 pitch=10 — t2",
        "08:00  yaw=+5 pitch=10 — t1",
        "",
        "Yesterday · 2026-09-04",
        "23:40  yaw=+5 pitch=10 — y1",
    ]


def test_day_label_falls_back_to_iso_for_other_days():
    assert nw.day_label(date(2026, 9, 1), TODAY) == "2026-09-01"


# --- position persistence --------------------------------------------------


def test_position_round_trip(tmp_path):
    path = tmp_path / "cfg" / "widget.json"
    assert nw.load_position(path) is None
    nw.save_position(1234.5, 88.0, path)
    assert nw.load_position(path) == (1234.5, 88.0)
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("payload", ["", "{}", "[1,2]", '{"x": "far", "y": 1}', "not json"])
def test_load_position_tolerates_garbage(tmp_path, payload):
    path = tmp_path / "widget.json"
    path.write_text(payload)
    assert nw.load_position(path) is None


def test_default_position_is_top_right_with_margin():
    screen = (0.0, 0.0, 1920.0, 1055.0)   # visible frame: menu bar already removed
    assert nw.default_position(screen, size=(360.0, 420.0), margin=24.0) == (1536.0, 611.0)


def test_position_on_screens_rejects_offscreen_saved_origin():
    screens = [(0.0, 0.0, 1920.0, 1080.0)]
    assert nw.position_on_screens((1536.0, 611.0), screens) is True
    assert nw.position_on_screens((2500.0, 611.0), screens) is False   # unplugged display
    assert nw.position_on_screens(None, screens) is False


# --- launchd unit ----------------------------------------------------------


def test_widget_plist_shape():
    parsed = plistlib.loads(_service_launchd._build_widget_plist())
    assert parsed["Label"] == "com.github.cc-buddy-bridge.notes-widget"
    assert parsed["ProgramArguments"] == [sys.executable, "-m", "cc_buddy_bridge.cli", "notes-widget"]
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is False
    assert parsed["ProcessType"] == "Interactive"
    assert parsed["StandardOutPath"] == str(_service_launchd.WIDGET_LOG_PATH)
    assert parsed["StandardErrorPath"] == str(_service_launchd.WIDGET_LOG_PATH)
    assert set(parsed["EnvironmentVariables"]) == {"HOME", "PATH"}


def test_widget_plist_is_separate_from_daemon_plist():
    daemon = plistlib.loads(_service_launchd._build_plist())
    widget = plistlib.loads(_service_launchd._build_widget_plist())
    assert daemon["Label"] != widget["Label"]
    assert _service_launchd.WIDGET_PLIST_PATH != _service_launchd.PLIST_PATH
    assert daemon["ProgramArguments"][0] == widget["ProgramArguments"][0]
    # The daemon unit keeps its shape — the widget is additive.
    assert daemon["KeepAlive"] is True
    assert daemon["ProgramArguments"][-1] == "daemon"
