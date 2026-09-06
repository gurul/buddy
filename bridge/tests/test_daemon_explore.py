"""The daemon side of the manual explore: `cc-buddy-bridge explore` over IPC, a
touch on the board, the wake word, and "hey buddy, go explore" (the voice tool
asks; the explore starts once the conversation has closed). No board, no mic,
no network: the same SimpleNamespace + MethodType stub test_explore.py uses."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import MethodType, SimpleNamespace

from cc_buddy_bridge import daemon as daemon_mod
from cc_buddy_bridge.daemon import Daemon
from cc_buddy_bridge.explore import ExploreConfig, Explorer, Look, Mode

MODE_ON = {"cmd": "mode", "explore": True}
MODE_OFF = {"cmd": "mode", "explore": False}


class _Ble:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.sent: list[dict] = []

    async def send(self, obj: dict, codec=None) -> bool:
        self.sent.append(obj)
        return True


class _Conversation:
    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return self.cancelled

    def cancel(self) -> None:
        self.cancelled = True


def _cfg(enabled: bool = True) -> ExploreConfig:
    return ExploreConfig(enabled=enabled, after_secs=600.0, notes_per_hour=0.0, model="fake",
                         notes_dir=Path("/nonexistent"), cycle_wait_secs=900.0)


def _daemon(connected: bool = True, pending: int = 0, listen_sent=None, enabled: bool = True) -> SimpleNamespace:
    d = SimpleNamespace(
        ble=_Ble(connected),
        state=SimpleNamespace(running_count=0, waiting_count=0, pending_count=pending),
        _listen_sent=listen_sent,
        _listen_down=False,
        _explore_cfg=_cfg(enabled),
        _explorer=Explorer(_cfg(enabled), now=0.0, notes_enabled=False),
        _notes=None,
        _last_activity_at=0.0,
        _explore_raw_frame=None,
        _conversation=None,
        _active_agent=None,
        _agent_state="idle",
        _explore_after_conversation=None,
        _voice=SimpleNamespace(start=lambda: None, stop=lambda: None, tap=lambda name: None),
        _voice_cfg=None,
        _agent_cfg=SimpleNamespace(enabled=False),
        _last_diag=None,
    )
    for name in ("_note_activity", "_idle_secs", "_explore_step", "_run_explore_action", "_stop_explore",
                 "_request_explore", "_dismiss_explore", "_handle_ipc", "_handle_ble", "_on_wake",
                 "_converse", "_on_voice_explore", "_on_agent_state", "_resync_agent", "_agent_keepalive",
                 "_cancel_active_task", "_make_agent", "_wake_suppressed", "_on_caption"):
        setattr(d, name, MethodType(getattr(Daemon, name), d))
    return d


# ---- IPC: cc-buddy-bridge explore [start|stop|status] ---------------------------------------

def test_ipc_start_puts_the_board_in_explore_mode_without_counting_as_activity() -> None:
    async def go():
        d = _daemon()
        d._last_activity_at = 123.0
        resp = await d._handle_ipc({"evt": "explore", "action": "start"})
        assert resp["ok"] and resp["connected"]
        assert resp["explore"]["state"] == "exploring" and resp["explore"]["manual"] is True
        assert resp["explore"]["reason"] == "requested by cli"
        assert d.ble.sent == [MODE_ON, {"cmd": "look", "yaw": -45, "pitch": 40, "hold": 6000}]
        assert d._last_activity_at == 123.0            # the request itself is not "the human is back"
        # a tick with zero idle time (a session is running) keeps exploring
        d.state.running_count = 1
        await d._explore_step(7.0)
        assert d._explorer.active
        assert d.ble.sent[-1]["cmd"] == "look"
    asyncio.run(go())


def test_ipc_stop_ends_it_and_status_reports() -> None:
    async def go():
        d = _daemon()
        st = await d._handle_ipc({"evt": "explore", "action": "status"})
        assert st == {"ok": True, "connected": True, "explore": d._explorer.status()}
        assert st["explore"]["state"] == "off"
        await d._handle_ipc({"evt": "explore", "action": "start"})
        st = await d._handle_ipc({"evt": "explore", "action": "status"})
        assert st["explore"]["state"] == "exploring" and st["explore"]["waypoint"] == [-45, 40]
        resp = await d._handle_ipc({"evt": "explore", "action": "stop"})
        assert resp["ok"] and resp["explore"]["state"] == "off" and resp["explore"]["manual"] is False
        assert d.ble.sent[-1] == MODE_OFF
        assert d.ble.sent.count(MODE_OFF) == 1
        # stop when nothing is running is a harmless no-op on the wire
        n = len(d.ble.sent)
        resp = await d._handle_ipc({"evt": "explore", "action": "stop"})
        assert resp["ok"] and len(d.ble.sent) == n
    asyncio.run(go())


def test_ipc_start_is_refused_while_disconnected_or_a_card_waits_or_dictating() -> None:
    async def go():
        for d, why in ((_daemon(connected=False), "board disconnected"),
                       (_daemon(pending=1), "card pending"),
                       (_daemon(listen_sent=True), "listen key")):
            resp = await d._handle_ipc({"evt": "explore", "action": "start"})
            assert resp["ok"] is False and resp["error"] == why, why
            assert resp["explore"]["state"] == "off"
            assert d.ble.sent == []
        resp = await _daemon()._handle_ipc({"evt": "explore", "action": "dance"})
        assert resp["ok"] is False and "unknown explore action" in resp["error"]
    asyncio.run(go())


def test_ipc_start_works_when_the_idle_start_is_disabled() -> None:
    async def go():
        d = _daemon(enabled=False)
        await d._explore_step(5000.0)                   # never starts on its own
        assert d.ble.sent == []
        resp = await d._handle_ipc({"evt": "explore"})  # action defaults to start
        assert resp["ok"] and d.ble.sent[0] == MODE_ON
    asyncio.run(go())


# ---- the board and the mic --------------------------------------------------------------------

def test_a_touch_ends_a_manual_explore_at_once() -> None:
    async def go():
        d = _daemon()
        await d._handle_ipc({"evt": "explore", "action": "start"})
        await d._handle_ble({"cmd": "voice", "on": False})
        assert d._explorer.state == "off" and not d._explorer.manual
        assert d.ble.sent[-1] == MODE_OFF
        # the idle clock restarted: the next tick does not resume
        await d._explore_step(1.0)
        assert d._explorer.state == "off"
    asyncio.run(go())


def test_a_touch_during_a_conversation_hushes_and_ends_the_explore() -> None:
    async def go():
        d = _daemon()
        conv = _Conversation()
        d._conversation = conv
        await d._handle_ipc({"evt": "explore", "action": "start"})
        await d._handle_ble({"cmd": "voice", "on": True})
        assert conv.cancelled and d._explorer.state == "off"
        assert d.ble.sent[-1] == MODE_OFF
    asyncio.run(go())


def test_the_wake_word_ends_a_manual_explore_before_the_conversation(monkeypatch) -> None:
    async def go():
        d = _daemon()
        d._ears = None                                   # _converse returns at once without ears
        await d._handle_ipc({"evt": "explore", "action": "start"})
        d._on_wake("hey buddy")
        await asyncio.sleep(0)                           # let the dismiss task run
        await asyncio.sleep(0)
        assert d._explorer.state == "off" and not d._explorer.manual
        assert d.ble.sent[-1] == MODE_OFF
        await d._conversation
    asyncio.run(go())


def test_voice_go_explore_starts_once_the_conversation_has_closed(monkeypatch) -> None:
    order: list[str] = []

    async def fake_open_session(mic, on_state, agent_factory, **kw):
        on_state("listening")
        kw["on_explore"]()
        order.append("session closed")
        assert not any(m.get("cmd") == "mode" for m in mics_daemon.ble.sent)   # not yet

    monkeypatch.setattr(daemon_mod.voice_agent, "open_session", fake_open_session)

    class _Ears:
        def subscribe(self):
            return asyncio.Queue()

        def unsubscribe(self, q):
            order.append("unsubscribed")

    async def go():
        global mics_daemon
        mics_daemon = d = _daemon()
        d._ears = _Ears()
        await d._converse()
        await asyncio.sleep(0)                           # the agent-state send is a task of its own
        assert order == ["session closed", "unsubscribed"]
        assert d._explorer.active and d._explorer.manual
        assert d._explorer.started_reason == "requested by voice"
        assert d._explore_after_conversation is None
        assert MODE_ON in d.ble.sent
        assert {"cmd": "look", "yaw": -45, "pitch": 40, "hold": 6000} in d.ble.sent
        assert {"cmd": "agent", "state": "idle"} in d.ble.sent
        assert d.ble.sent.index(MODE_ON) > d.ble.sent.index({"cmd": "agent", "state": "listening"})
    asyncio.run(go())


def test_voice_go_explore_is_dropped_when_the_conversation_is_hushed(monkeypatch) -> None:
    async def fake_open_session(mic, on_state, agent_factory, **kw):
        kw["on_explore"]()
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon_mod.voice_agent, "open_session", fake_open_session)

    class _Ears:
        def subscribe(self):
            return asyncio.Queue()

        def unsubscribe(self, q):
            pass

    async def go():
        d = _daemon()
        d._ears = _Ears()
        try:
            await d._converse()
        except asyncio.CancelledError:
            pass
        assert d._explorer.state == "off" and d._explore_after_conversation is None
        assert not any(m == MODE_ON for m in d.ble.sent)
    asyncio.run(go())
