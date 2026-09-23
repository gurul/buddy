"""Composio bridge: the switch, the owner's session, the ask-first gate, and execute that never raises.

No network: a fake client stands in for the SDK. The SDK is never imported here.
"""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path
from typing import Any

import pytest

from cc_buddy_bridge.composio_tools import (
    COMPOSIO_DEFAULT,
    ComposioBridge,
    configured,
    consequential_slugs,
    describe_for_confirmation,
    is_read_only,
)

META_TOOLS = [
    {"type": "function", "name": "COMPOSIO_SEARCH_TOOLS", "strict": True,
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "COMPOSIO_MULTI_EXECUTE_TOOL", "strict": True,
     "parameters": {"type": "object", "properties": {}}},
    {"type": "web_search"},  # not a function tool: filtered out
    {"name": "no_type"},  # no type: filtered out
]


class FakeResult:
    """Pydantic-ish, like SessionExecuteResponse."""

    def __init__(self, data: dict[str, Any], error: str | None = None) -> None:
        self.data, self.error, self.log_id = data, error, "log_123"

    def model_dump(self) -> dict[str, Any]:
        return {"data": self.data, "error": self.error, "log_id": self.log_id}


class FakeToolkit:
    def __init__(self, slug: str, active: bool) -> None:
        self.slug, self.name = slug, slug.title()
        self.connection = type("Conn", (), {"is_active": active})()


class FakeSession:
    def __init__(self, session_id: str, execute: Any = None) -> None:
        self.session_id = session_id
        self._execute = execute
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def tools(self) -> list[dict[str, Any]]:
        return list(META_TOOLS)

    def execute(self, slug: str, *, arguments: dict[str, Any] | None = None, account: str | None = None) -> Any:
        self.executed.append((slug, dict(arguments or {})))
        if isinstance(self._execute, Exception):
            raise self._execute
        return self._execute if self._execute is not None else FakeResult({"items": [1, 2]})

    def toolkits(self) -> Any:
        return type("Page", (), {"items": [FakeToolkit("gmail", True), FakeToolkit("googlecalendar", False)]})()

    def authorize(self, toolkit: str) -> Any:
        return type("Req", (), {"redirect_url": f"https://connect.example/{toolkit}",
                                "wait_for_connection": lambda self, timeout=None: None})()


class FakeSessions:
    def __init__(self, use_raises: bool = False, execute: Any = None) -> None:
        self.created: list[str] = []
        self.used: list[str] = []
        self.use_raises = use_raises
        self._execute = execute
        self.n = 0

    def create(self, *, user_id: str) -> FakeSession:
        self.created.append(user_id)
        self.n += 1
        return FakeSession(f"trs_new_{self.n}", self._execute)

    def use(self, session_id: str) -> FakeSession:
        self.used.append(session_id)
        if self.use_raises:
            raise RuntimeError("no such session")
        return FakeSession(session_id, self._execute)


class FakeClient:
    def __init__(self, **kw: Any) -> None:
        self.sessions = FakeSessions(**kw)


def _cfg(tmp_path: Path, owners: frozenset[int] = frozenset({7})) -> Any:
    env = {"CC_BUDDY_COMPOSIO": "1", "COMPOSIO_API_KEY": "ak_test", "CC_BUDDY_COMPOSIO_STATE": str(tmp_path / "composio.json")}
    return configured(env, owners)


def test_it_ships_off_and_needs_the_switch_and_the_key(tmp_path: Path, caplog: Any) -> None:
    owners = frozenset({42, 7})
    assert COMPOSIO_DEFAULT is False
    assert configured({}, owners).enabled is False
    assert configured({"COMPOSIO_API_KEY": "ak_x"}, owners).enabled is False  # key without the switch
    with caplog.at_level(logging.WARNING, logger="cc_buddy_bridge.composio_tools"):
        assert configured({"CC_BUDDY_COMPOSIO": "1"}, owners).enabled is False  # switch without the key
        assert configured({"CC_BUDDY_COMPOSIO": "yes", "COMPOSIO_API_KEY": "ak_x"}, frozenset()).enabled is False
    assert "COMPOSIO_API_KEY" in caplog.text and "owner" in caplog.text
    assert "ak_x" not in caplog.text
    cfg = configured({"CC_BUDDY_COMPOSIO": "on", "COMPOSIO_API_KEY": "ak_secret_value",
                      "CC_BUDDY_COMPOSIO_STATE": str(tmp_path / "s.json"), "CC_BUDDY_COMPOSIO_TIMEOUT_SECS": "12.5"},
                     owners)
    assert cfg.enabled is True
    assert cfg.user_id == "telegram-7-42"  # sorted, so the same owners always name the same session
    assert cfg.state_path == tmp_path / "s.json"
    assert cfg.timeout_secs == 12.5
    assert "ak_secret_value" not in repr(cfg)
    assert configured({"CC_BUDDY_COMPOSIO": "0", "COMPOSIO_API_KEY": "ak_x"}, owners).enabled is False
    # Defaults hold when the overrides are absent or junk.
    d = configured({"CC_BUDDY_COMPOSIO_TIMEOUT_SECS": "nope"}, owners)
    assert d.timeout_secs == 60.0 and d.state_path == Path("~/.config/cc-buddy-bridge/composio.json").expanduser()


def test_the_session_is_the_owners_and_is_reused(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first = FakeClient()
    b1 = ComposioBridge(cfg, client_factory=lambda: first)
    b1.start()
    assert first.sessions.created == ["telegram-7"] and first.sessions.used == []
    assert b1.session_id == "trs_new_1"
    state = json.loads(cfg.state_path.read_text())
    assert state == {"user_id": "telegram-7", "session_id": "trs_new_1"}
    assert stat.S_IMODE(cfg.state_path.stat().st_mode) == 0o600
    # Only function tools with a name survive; names is derived from them.
    assert all(t["type"] == "function" and t["name"] for t in b1.tools())
    assert b1.names == frozenset({"COMPOSIO_SEARCH_TOOLS", "COMPOSIO_MULTI_EXECUTE_TOOL"})

    # Restart: the second bridge resumes the stored id instead of creating.
    second = FakeClient()
    b2 = ComposioBridge(cfg, client_factory=lambda: second)
    b2.start()
    assert second.sessions.used == ["trs_new_1"] and second.sessions.created == []
    assert b2.session_id == "trs_new_1"

    # A stored id for someone else is not ours: create, and overwrite the state.
    cfg.state_path.write_text(json.dumps({"user_id": "telegram-999", "session_id": "trs_theirs"}))
    third = FakeClient()
    b3 = ComposioBridge(cfg, client_factory=lambda: third)
    b3.start()
    assert third.sessions.used == [] and third.sessions.created == ["telegram-7"]
    assert json.loads(cfg.state_path.read_text())["session_id"] == "trs_new_1"

    # A stored id the server no longer knows: use raises, create takes over, the state heals.
    fourth = FakeClient(use_raises=True)
    b4 = ComposioBridge(cfg, client_factory=lambda: fourth)
    b4.start()
    assert fourth.sessions.used == ["trs_new_1"] and fourth.sessions.created == ["telegram-7"]
    assert b4.session_id == "trs_new_1" and json.loads(cfg.state_path.read_text())["session_id"] == "trs_new_1"

    # Toolkits and connect links pass through the session.
    assert b4.toolkits() == [("gmail", True), ("googlecalendar", False)]
    assert b4.connect_link("gmail") == "https://connect.example/gmail"
    assert b4.wait_for("gmail", timeout=1) is True


def test_read_only_slugs_run_and_consequential_slugs_ask_first() -> None:
    assert is_read_only("GMAIL_FETCH_EMAILS") and is_read_only("GITHUB_LIST_REPOSITORY_ISSUES")
    assert is_read_only("googlecalendar_find_event") and is_read_only("NOTION_SEARCH")
    assert is_read_only("GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS")      # the verb is not always the first word
    assert not is_read_only("GMAIL_SEND_EMAIL") and not is_read_only("GITHUB_CREATE_ISSUE")
    assert not is_read_only("GETTY_UPLOAD")  # a verb-like toolkit name does not make the call read-only
    assert not is_read_only("GMAIL_GET_AND_DELETE_MESSAGE")               # a read word beside a write word writes
    assert not is_read_only("SLACK_DELETE_MESSAGE") and not is_read_only("") and not is_read_only("GMAIL")


def test_gmail_is_read_only_and_the_calendar_may_write() -> None:
    from cc_buddy_bridge.composio_tools import Decision, decide, toolkit_policy

    def multi(*slugs: str) -> dict:
        return {"tools": [{"tool_slug": s, "arguments": {}} for s in slugs]}

    assert decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GMAIL_FETCH_EMAILS", "GOOGLECALENDAR_FIND_EVENT")).action == "run"
    d = decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GMAIL_SEND_EMAIL"))
    assert d.action == "refuse" and d.slugs == ["GMAIL_SEND_EMAIL"] and "gmail is read only here" in d.why
    d = decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR_FIND_EVENT"))
    assert d.action == "run" and d.why == "allowed to write: GOOGLECALENDAR_CREATE_EVENT"
    d = decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("SLACK_SEND_MESSAGE"))
    assert d == Decision("ask", ["SLACK_SEND_MESSAGE"])
    # mixed: refuse beats ask beats write
    d = decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GOOGLECALENDAR_CREATE_EVENT", "SLACK_SEND_MESSAGE", "GMAIL_SEND_EMAIL"))
    assert d.action == "refuse" and d.slugs == ["GMAIL_SEND_EMAIL"]
    assert decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GOOGLECALENDAR_CREATE_EVENT", "SLACK_SEND_MESSAGE")).action == "ask"
    assert decide("COMPOSIO_REMOTE_BASH_TOOL", {"command": "ls"}).action == "ask"
    assert decide("COMPOSIO_SEARCH_TOOLS", {}).action == "run"
    # the owner's env overrides and extends
    pol = toolkit_policy({"CC_BUDDY_COMPOSIO_POLICY": "gmail=ask, slack=write,notion=bogus,=read"})
    assert pol == {"gmail": "ask", "googlecalendar": "write", "googledrive": "ask", "slack": "write"}
    assert decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GMAIL_SEND_EMAIL"), pol).action == "ask"
    assert toolkit_policy({}) == {"gmail": "read", "googlecalendar": "write", "googledrive": "ask"}
    assert decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GOOGLEDRIVE_LIST_FILES")).action == "run"
    assert decide("COMPOSIO_MULTI_EXECUTE_TOOL", multi("GOOGLEDRIVE_UPLOAD_FILE")).action == "ask"

    mixed = {"tools": [
        {"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {"max_results": 5}},
        {"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {"to": "x@y.com", "subject": "Hello"}},
        {"tool_slug": "GITHUB_LIST_REPOSITORY_ISSUES", "arguments": {}},
        {"tool_slug": "SLACK_SEND_MESSAGE", "arguments": {"channel": "#general", "text": "hi"}},
    ], "thought": "do it", "sync_response_to_workbench": False, "current_step": "1"}
    assert consequential_slugs("COMPOSIO_MULTI_EXECUTE_TOOL", mixed) == ["GMAIL_SEND_EMAIL", "SLACK_SEND_MESSAGE"]
    reads = {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {}},
                       {"tool_slug": "GOOGLECALENDAR_FIND_EVENT", "arguments": {}}]}
    assert consequential_slugs("COMPOSIO_MULTI_EXECUTE_TOOL", reads) == []
    assert consequential_slugs("COMPOSIO_MULTI_EXECUTE_TOOL", {}) == []
    assert consequential_slugs("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": "junk"}) == []
    assert consequential_slugs("COMPOSIO_SEARCH_TOOLS", {"queries": [{"use_case": "send mail"}]}) == []
    assert consequential_slugs("COMPOSIO_GET_TOOL_SCHEMAS", {}) == []
    assert consequential_slugs("COMPOSIO_MANAGE_CONNECTIONS", {}) == []
    assert consequential_slugs("COMPOSIO_REMOTE_BASH_TOOL", {"command": "ls"}) == ["COMPOSIO_REMOTE_BASH_TOOL"]
    assert consequential_slugs("COMPOSIO_REMOTE_WORKBENCH", {}) == ["COMPOSIO_REMOTE_WORKBENCH"]

    line = describe_for_confirmation("COMPOSIO_MULTI_EXECUTE_TOOL", mixed)
    assert line == ("Run GMAIL_SEND_EMAIL with to: x@y.com, subject: Hello; "
                    "and SLACK_SEND_MESSAGE with channel: #general, text: hi?")
    assert "GMAIL_FETCH_EMAILS" not in line  # the reads are not what the owner is asked about
    assert describe_for_confirmation("COMPOSIO_REMOTE_BASH_TOOL", {"command": "ls -la"}) == (
        "Run COMPOSIO_REMOTE_BASH_TOOL with command: ls -la?")
    assert describe_for_confirmation("COMPOSIO_REMOTE_WORKBENCH", {}) == "Run COMPOSIO_REMOTE_WORKBENCH?"

    # Clipping: each value to 60 chars, the line to 300; newlines flattened; nested values serialised.
    long = {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {
        "to": "x@y.com", "body": "line one\nline two " + "z" * 500, "cc": ["a@b.c", "d@e.f"], "n": 3, "flag": True}}]}
    line = describe_for_confirmation("COMPOSIO_MULTI_EXECUTE_TOOL", long)
    assert line.startswith("Run GMAIL_SEND_EMAIL with to: x@y.com, body: line one line two zzz")
    assert line.endswith("?") and len(line) <= 300 and "\n" not in line
    body = line.split("body: ", 1)[1].split(", cc:", 1)[0]
    assert len(body) <= 60
    assert 'cc: ["a@b.c", "d@e.f"]' in line and "n: 3" in line and "flag: true" in line
    many = {"tools": [{"tool_slug": f"APP_CREATE_THING_{i}", "arguments": {"v": "w" * 60}} for i in range(12)]}
    line = describe_for_confirmation("COMPOSIO_MULTI_EXECUTE_TOOL", many)
    assert len(line) <= 300 and line.endswith("?")
    for text in (line, describe_for_confirmation("COMPOSIO_MULTI_EXECUTE_TOOL", mixed)):
        assert not any(ord(ch) > 0x2FFF for ch in text)  # no emoji (the only high code point allowed is the ellipsis)
        assert " - " not in text and " — " not in text


def test_execute_never_raises(tmp_path: Path, caplog: Any) -> None:
    cfg = _cfg(tmp_path)
    # Not started: a reason, not an exception.
    idle = ComposioBridge(cfg, client_factory=FakeClient)
    assert idle.execute("COMPOSIO_SEARCH_TOOLS", {}) == {"ok": False, "reason": "composio is not started"}
    assert idle.tools() == [] and idle.names == frozenset() and idle.toolkits() == []

    # A good result is a plain dict the model can read, with ok and the log id.
    ok = ComposioBridge(cfg, client_factory=FakeClient)
    ok.start()
    out = ok.execute("COMPOSIO_SEARCH_TOOLS", {"queries": [{"use_case": "mail"}]})
    assert out == {"data": {"items": [1, 2]}, "error": None, "log_id": "log_123", "ok": True}
    json.dumps(out)
    assert ok._session.executed == [("COMPOSIO_SEARCH_TOOLS", {"queries": [{"use_case": "mail"}]})]

    # The server said error: ok is False, the payload is kept for the model to explain.
    said_no = ComposioBridge(cfg, client_factory=lambda: FakeClient(execute=FakeResult({}, error="not connected")))
    said_no.start()
    out = said_no.execute("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": []})
    assert out["ok"] is False and out["error"] == "not connected" and out["log_id"] == "log_123"

    # The SDK raised: the type and a short message, and the log names the type but never the arguments.
    boom = ComposioBridge(cfg, client_factory=lambda: FakeClient(execute=TimeoutError("upstream took " + "x" * 400)))
    boom.start()
    with caplog.at_level(logging.WARNING, logger="cc_buddy_bridge.composio_tools"):
        out = boom.execute("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL",
                                                                       "arguments": {"body": "SECRET BODY"}}]})
    assert out["ok"] is False and out["reason"].startswith("TimeoutError: upstream took x")
    assert len(out["reason"]) <= len("TimeoutError: ") + 200
    json.dumps(out)
    assert "TimeoutError" in caplog.text and "SECRET BODY" not in caplog.text and "GMAIL_SEND_EMAIL" not in caplog.text

    # A result that is not pydantic and not a mapping is still a dict; unserialisable bits become strings.
    odd = ComposioBridge(cfg, client_factory=lambda: FakeClient(execute={"data": {"when": Path("/x")}, "log_id": "l"}))
    odd.start()
    out = odd.execute("COMPOSIO_GET_TOOL_SCHEMAS", {})
    assert out == {"data": {"when": "/x"}, "log_id": "l", "ok": True}

    # wait_for and connect_link on a broken session do not raise either.
    class Broken(FakeSession):
        def authorize(self, toolkit: str) -> Any:
            raise ConnectionError("down")

    class BrokenSessions(FakeSessions):
        def create(self, *, user_id: str) -> FakeSession:
            return Broken("trs_broken")

    class BrokenClient:
        sessions = BrokenSessions()

    b = ComposioBridge(_cfg(tmp_path / "b"), client_factory=BrokenClient)
    b.start()
    assert b.wait_for("gmail", timeout=0.1) is False


@pytest.mark.parametrize("slug,read", [
    ("GMAIL_GET_LABEL", True),                  # the unread count lives on the INBOX label: a read (live, 2026-09-23)
    ("GMAIL_LIST_LABELS", True),
    ("GMAIL_FETCH_EMAILS", True),
    ("GMAIL_ADD_LABEL_TO_EMAIL", False),
    ("GMAIL_CREATE_LABEL", False),
    ("GMAIL_MODIFY_THREAD_LABELS", False),
    ("GMAIL_LABEL_EMAIL", False),               # LABEL in verb position is still a write
    ("GMAIL_SEND_EMAIL", False),
])
def test_label_is_a_write_only_as_the_verb(slug: str, read: bool) -> None:
    from cc_buddy_bridge.composio_tools import is_read_only

    assert is_read_only(slug) is read
