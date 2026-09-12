"""Env file loader — parsing, precedence, and the missing-file case."""

from __future__ import annotations

from pathlib import Path

from cc_buddy_bridge.envfile import load_env_file, parse_env_file


def test_parse_basic_lines() -> None:
    text = (
        "# a comment\n"
        "\n"
        "OPENAI_API_KEY=sk-test-123\n"
        "export CC_BUDDY_NOTES_MODEL=gpt-5-mini\n"
        "QUOTED=\"with spaces\"\n"
        "SINGLE='x=y'\n"
        "  SPACED =  value  \n"
    )
    assert parse_env_file(text) == {
        "OPENAI_API_KEY": "sk-test-123",
        "CC_BUDDY_NOTES_MODEL": "gpt-5-mini",
        "QUOTED": "with spaces",
        "SINGLE": "x=y",
        "SPACED": "value",
    }


def test_parse_skips_malformed_lines() -> None:
    assert parse_env_file("no equals\n=novalue\n1BAD=x\nGOOD=1\n") == {"GOOD": "1"}


def test_load_fills_gaps_but_never_overrides(tmp_path: Path) -> None:
    f = tmp_path / "env"
    f.write_text("A=from-file\nB=from-file\n")
    env = {"A": "from-shell"}
    assert load_env_file(f, env) == ["B"]
    assert env == {"A": "from-shell", "B": "from-file"}


def test_load_missing_file_is_noop(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    assert load_env_file(tmp_path / "absent", env) == []
    assert env == {}


def test_load_expands_user(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "env").write_text("K=v\n")
    env: dict[str, str] = {}
    assert load_env_file(Path("~/env"), env) == ["K"]
    assert env["K"] == "v"
