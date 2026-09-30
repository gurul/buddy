"""stick_link.py: the Buddy Link app's door into a call, its token, its pairing page and the pinned Update button.

The real Mini App server on a loopback port and a real WebSocket client standing in for the app, as in
test_phone_call.py (whose fake chat and voice this reuses)."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from test_phone_call import OWNER, TOKEN, Brain, Voice, is_, press, signed, until
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from cc_buddy_bridge import miniapp, phone_call, stick_link
from cc_buddy_bridge.miniapp import MiniAppConfig, MiniAppServer, SpendLedger, check_init_data

# ---- the token ------------------------------------------------------------------------------------------

def test_no_token_until_pairing_and_nothing_is_accepted_then(tmp_path: Path) -> None:
    t = stick_link.LinkToken(tmp_path / "stick-link.json")
    assert t.current() == ""
    assert not t.check("") and not t.check("anything")


def test_the_token_is_private_checked_exactly_and_a_new_one_cuts_off_the_old(tmp_path: Path) -> None:
    t = stick_link.LinkToken(tmp_path / "stick-link.json")
    first = t.ensure()
    assert len(first) >= 40 and t.ensure() == first                      # ensure keeps it
    assert stat.S_IMODE(os.stat(t.path).st_mode) == 0o600
    assert t.check(first)                                                 # positive control
    assert not t.check(first[:-1]) and not t.check(first + "x") and not t.check("")
    second = t.rotate()
    assert second != first and t.check(second) and not t.check(first)


def test_a_damaged_token_file_counts_as_unpaired(tmp_path: Path) -> None:
    path = tmp_path / "stick-link.json"
    for text in ("not json", json.dumps({"token": 5}), json.dumps({"token": "short"}), json.dumps([1])):
        path.write_text(text)
        assert stick_link.LinkToken(path).current() == ""


def test_the_pairing_link_keeps_the_token_out_of_what_the_server_sees() -> None:
    url = stick_link.pair_url("https://a-b.trycloudflare.com/", "tok_en-123")
    parts = urlsplit(url)
    assert parts.path == stick_link.PAIR_PATH and parts.query == ""       # the request line carries neither
    assert parts.fragment == "t=tok_en-123"


# ---- the door -------------------------------------------------------------------------------------------

async def serve(tmp_path: Path, brain: Any, paired: bool = True) -> tuple[MiniAppServer, str, str, str]:
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=frozenset({OWNER}), ledger_path=tmp_path / "l.json")
    server = MiniAppServer(cfg, SpendLedger(cfg.ledger_path), page=b"home")
    server.calls = phone_call.PhoneCalls(lambda d: check_init_data(d, TOKEN, cfg.owner_ids), brain, Voice())
    server.stick = stick_link.LinkToken(tmp_path / "stick-link.json")
    token = server.stick.ensure() if paired else ""
    port = await server.start()
    return server, f"ws://127.0.0.1:{port}", f"http://127.0.0.1:{port}", token


def test_the_app_calls_with_its_token_and_a_press_is_heard(tmp_path: Path) -> None:
    brain = Brain()

    async def go() -> list[Any]:
        server, ws_base, _, token = await serve(tmp_path, brain)
        async with connect(ws_base + stick_link.CALL_PATH) as ws:        # no Origin, as the app
            await ws.send(json.dumps({"token": token}))
            await until(ws, is_("state", state="connected"))
            await press(ws, 1.0)
            got = await until(ws, is_("state", state="listening"))
        await server.close()
        return got

    got = asyncio.run(go())
    assert {"type": "heard", "text": "what's on my calendar"} in got
    assert brain.heard == ["what's on my calendar"]
    assert any(isinstance(m, bytes) for m in got)                         # the reply's audio came back


@pytest.mark.parametrize("hello", [{"token": "wrong"}, {"token": ""}, {}, {"initData": "x"}, "not json"])
def test_a_wrong_or_missing_token_ends_the_call_before_it_starts(tmp_path: Path, hello: Any) -> None:
    brain = Brain()

    async def go() -> None:
        server, ws_base, _, _ = await serve(tmp_path, brain)
        async with connect(ws_base + stick_link.CALL_PATH) as ws:
            await ws.send(hello if isinstance(hello, str) else json.dumps(hello))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        await server.close()

    asyncio.run(go())
    assert brain.say is None and brain.heard == []


def test_telegrams_initdata_does_not_open_the_apps_door_nor_the_token_the_mini_apps(tmp_path: Path) -> None:
    async def go() -> None:
        server, ws_base, http, token = await serve(tmp_path, Brain())
        async with connect(ws_base + stick_link.CALL_PATH) as ws:
            await ws.send(json.dumps({"initData": signed()}))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        async with connect(ws_base + "/api/call", origin=http) as ws:
            await ws.send(json.dumps({"token": token}))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        async with connect(ws_base + "/api/call", origin=http) as ws:  # control: the Mini App still gets in
            await ws.send(json.dumps({"initData": signed()}))
            await until(ws, is_("state", state="connected"))
        await server.close()

    asyncio.run(go())


def test_a_browser_cannot_use_the_apps_door_and_it_is_shut_until_paired(tmp_path: Path) -> None:
    async def go() -> None:
        server, ws_base, http, _ = await serve(tmp_path, Brain())
        for origin in (http, "https://evil.example", "null"):
            with pytest.raises(InvalidStatus) as e:
                async with connect(ws_base + stick_link.CALL_PATH, origin=origin):
                    pass
            assert e.value.response.status_code == 403
        await server.close()
        server, ws_base, _, _ = await serve(tmp_path / "unpaired", Brain(), paired=False)
        async with connect(ws_base + stick_link.CALL_PATH) as ws:        # open door, but no token to match
            await ws.send(json.dumps({"token": ""}))
            assert (await until(ws, is_("ended")))[-1]["reason"] == "Only buddy's owner can call."
        server.stick = None
        with pytest.raises(InvalidStatus) as e:
            async with connect(ws_base + stick_link.CALL_PATH):
                pass
        assert e.value.response.status_code == 404
        await server.close()

    asyncio.run(go())


def test_the_pairing_page_is_static_strict_and_hands_the_fragment_to_the_app(tmp_path: Path) -> None:
    def fetch(url: str) -> tuple[int, dict[str, str], str]:
        with urllib.request.urlopen(url) as r:                              # noqa: S310 - loopback
            return r.status, dict(r.headers), r.read().decode()

    async def go() -> tuple[int, dict[str, str], str]:
        server, _, http, token = await serve(tmp_path, Brain())
        out = await asyncio.to_thread(fetch, http + stick_link.PAIR_PATH)  # off the loop the server runs on
        await server.close()
        assert token not in out[2]
        return out

    status, headers, body = asyncio.run(go())
    assert status == 200
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer" and headers["Cache-Control"] == "no-store"
    assert "location.hash" in body and "buddylink://pair?u=" in body
    assert "history.replaceState" in body                                 # the token leaves the address bar


# ---- the pinned message and /stick ------------------------------------------------------------------------

def test_the_pinned_message_gets_the_update_button_only_once_paired() -> None:
    plain = miniapp.open_button("https://x.trycloudflare.com/")
    assert len(plain["inline_keyboard"]) == 1
    both = miniapp.open_button("https://x.trycloudflare.com/", "https://x.trycloudflare.com/stick#t=abc")
    assert both["inline_keyboard"][1] == [{"text": stick_link.UPDATE_TEXT,
                                           "url": "https://x.trycloudflare.com/stick#t=abc"}]


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method: str, data: dict) -> Any:
        self.calls.append((method, data))
        return {"ok": True, "result": {"message_id": 77}}


def test_stick_pairs_rotates_and_repoints_the_pin_at_the_live_address(tmp_path: Path) -> None:
    bot = FakeBot()
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=frozenset({OWNER}), ledger_path=tmp_path / "l.json")
    app = miniapp.MiniApp(cfg, bot=bot, tunnel_factory=lambda: None, pins_path=tmp_path / "pin.json")

    async def go() -> tuple[str, str, str]:
        down = await app.stick_link()                                      # no tunnel yet
        app.url = "https://live.trycloudflare.com"
        first = await app.stick_link()
        second = await app.stick_link(rotate=True)
        return down, first, second

    down, first, second = asyncio.run(go())
    assert down == "" and app.server.stick.current() == urlsplit(second).fragment[2:]
    assert first.startswith("https://live.trycloudflare.com/stick#t=") and first != second
    markups = [d["reply_markup"] for m, d in bot.calls if "reply_markup" in d]
    assert markups and markups[-1]["inline_keyboard"][1][0]["url"] == second


def test_pairing_without_a_tunnel_keeps_no_token(tmp_path: Path) -> None:
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=frozenset({OWNER}), ledger_path=tmp_path / "l.json")
    app = miniapp.MiniApp(cfg, bot=FakeBot(), tunnel_factory=lambda: None, pins_path=tmp_path / "pin.json")
    assert asyncio.run(app.stick_link()) == ""
    assert not (tmp_path / "stick-link.json").exists()
