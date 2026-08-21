"""Monitor-build daemon behaviour: the MONITOR_ONLY gate and session adoption.

Neither needs a live Daemon — MONITOR_ONLY is module-level env parsing, and
_ensure_session touches nothing but ``self.state`` — so these stay unit tests
instead of dragging in BLE/IPC/matcher stubs.
"""

from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

import pytest

from cc_buddy_bridge.state import State


def _reload_daemon_with(monkeypatch, value):
    """Re-import daemon.py with CC_BUDDY_MONITOR_ONLY set (or cleared)."""
    import cc_buddy_bridge.daemon as daemon_mod

    if value is None:
        monkeypatch.delenv("CC_BUDDY_MONITOR_ONLY", raising=False)
    else:
        monkeypatch.setenv("CC_BUDDY_MONITOR_ONLY", value)
    return importlib.reload(daemon_mod)


@pytest.fixture(autouse=True)
def _restore_daemon_module():
    """Leave the module in its unset state so reloads can't leak across tests."""
    yield
    import cc_buddy_bridge.daemon as daemon_mod

    os.environ.pop("CC_BUDDY_MONITOR_ONLY", None)
    importlib.reload(daemon_mod)


def test_monitor_only_defaults_off(monkeypatch):
    """The interactive builds must be unaffected by this variant existing."""
    assert _reload_daemon_with(monkeypatch, None).MONITOR_ONLY is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_monitor_only_truthy_spellings(monkeypatch, value):
    assert _reload_daemon_with(monkeypatch, value).MONITOR_ONLY is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_monitor_only_falsy_spellings(monkeypatch, value):
    assert _reload_daemon_with(monkeypatch, value).MONITOR_ONLY is False


# ---- session adoption -------------------------------------------------------

def _ensure(state: State, **req):
    from cc_buddy_bridge.daemon import Daemon

    Daemon._ensure_session(SimpleNamespace(state=state), req)


def test_tool_hook_adopts_an_unknown_session():
    """A daemon restart leaves running sessions unregistered; the monitor would
    show an empty agent list while agents are plainly working."""
    st = State()
    _ensure(st, session_id="s1", cwd="/tmp/my-repo")
    assert st.total == 1
    assert st.agent_rows()[0]["n"] == "my-repo"


def test_adoption_is_idempotent():
    st = State()
    for _ in range(3):
        _ensure(st, session_id="s1", cwd="/tmp/my-repo")
    assert st.total == 1


def test_cwd_is_backfilled_when_the_first_hook_carried_none():
    """posttooluse carries no cwd and session_start is create-if-absent, so
    without a backfill whichever hook wins the race names the row forever."""
    st = State()
    _ensure(st, session_id="s1")                      # posttooluse-shaped
    assert st.agent_rows()[0]["n"] == "s1"            # fallback label
    _ensure(st, session_id="s1", cwd="/tmp/my-repo")  # pretooluse-shaped
    assert st.agent_rows()[0]["n"] == "my-repo"


def test_backfill_never_overwrites_a_known_cwd():
    st = State()
    _ensure(st, session_id="s1", cwd="/tmp/real-repo")
    _ensure(st, session_id="s1", cwd="/tmp/somewhere-else")
    assert st.sessions["s1"].cwd == "/tmp/real-repo"


@pytest.mark.parametrize("req", [{}, {"session_id": ""}, {"session_id": None}, {"session_id": 42}])
def test_malformed_hook_payloads_create_nothing(req):
    st = State()
    _ensure(st, **req)
    assert st.total == 0
