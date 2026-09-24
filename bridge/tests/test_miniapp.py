"""miniapp: the "Ask Claude" Telegram Mini App — who may ask, what it costs, and the streamed answer."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from cc_buddy_bridge import miniapp
from cc_buddy_bridge.miniapp import (
    Answer,
    MiniApp,
    MiniAppConfig,
    MiniAppServer,
    SpendLedger,
    check_init_data,
    clean_history,
    cost_usd,
    sign_init_data,
)

TOKEN = "123456:TEST-token"
OWNER = 4242
OWNERS = frozenset({OWNER})


def signed(user_id: int = OWNER, *, auth_date: int | None = None, token: str = TOKEN) -> str:
    return sign_init_data({"auth_date": str(auth_date if auth_date is not None else int(time.time())),
                           "query_id": "AAH", "user": json.dumps({"id": user_id, "first_name": "O"})}, token)


# ---- who may ask ----------------------------------------------------------------------------------

def test_a_fresh_owner_signature_is_accepted() -> None:
    assert check_init_data(signed(), TOKEN, OWNERS) == OWNER


def test_a_tampered_field_breaks_the_signature() -> None:
    good = signed()
    assert check_init_data(good.replace("AAH", "AAX"), TOKEN, OWNERS) is None


def test_someone_else_signed_by_telegram_is_still_refused() -> None:
    assert check_init_data(signed(999), TOKEN, OWNERS) is None


def test_another_bots_signature_is_refused() -> None:
    assert check_init_data(signed(token="999:OTHER"), TOKEN, OWNERS) is None


@pytest.mark.parametrize("age", [miniapp.INIT_DATA_MAX_AGE_SECS + 60, -3600])
def test_a_stale_or_future_signature_is_refused(age: int) -> None:
    assert check_init_data(signed(auth_date=int(time.time()) - age), TOKEN, OWNERS) is None


@pytest.mark.parametrize("raw", ["", "user=%7B%7D", "hash=abc", "not a query string at all&&&"])
def test_garbage_is_refused(raw: str) -> None:
    assert check_init_data(raw, TOKEN, OWNERS) is None


# ---- what it costs ------------------------------------------------------------------------------

def test_cost_is_the_list_price_per_million_tokens() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 0}
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4.0 + 20.0 + 0.20)


def test_an_unknown_model_costs_at_the_worst_price() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert cost_usd("some-future-model", usage) >= cost_usd("claude-opus-5-5", usage)


def test_the_ledger_counts_today_and_resets_tomorrow(tmp_path: Path) -> None:
    day = {"d": "2026-09-24"}
    led = SpendLedger(tmp_path / "spend.json", 1.0, today=lambda: day["d"])
    assert led.left() == 1.0
    led.add(0.4)
    led.add(0.7)
    assert led.spent() == pytest.approx(1.1) and led.left() == 0.0
    assert SpendLedger(tmp_path / "spend.json", 1.0, today=lambda: day["d"]).spent() == pytest.approx(1.1)
    day["d"] = "2026-09-25"
    assert led.left() == 1.0


# ---- the conversation the page sends -------------------------------------------------------------

def test_history_is_cleaned_to_alternating_turns_ending_on_the_user() -> None:
    raw = [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "a"},
           {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}, {"role": "user", "content": "d"}]
    assert clean_history(raw) == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"},
                                  {"role": "user", "content": "d"}]


@pytest.mark.parametrize("raw", [None, [], [{"role": "assistant", "content": "x"}],
                                 [{"role": "system", "content": "x"}], [{"role": "user", "content": 3}]])
def test_bad_history_is_refused(raw) -> None:
    assert clean_history(raw) is None


# ---- the server ---------------------------------------------------------------------------------

def fake_stream(pieces=("Hello", " there"), fail: Exception | None = None, usage=None):
    calls: list[list[dict]] = []

    async def stream(history, answer: Answer):
        calls.append(history)
        for p in pieces:
            yield p
        if fail is not None:
            raise fail
        answer.usage = usage or {"input_tokens": 1000, "output_tokens": 500}
        answer.stop_reason = "end_turn"

    return stream, calls


def events(body: str) -> list[dict]:
    return [json.loads(chunk[6:]) for chunk in body.split("\n\n") if chunk.startswith("data: ")]


def run_server(tmp_path: Path, stream, cap: float = 5.0):
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=OWNERS, daily_usd=cap,
                        ledger_path=tmp_path / "spend.json")
    return MiniAppServer(cfg, stream, SpendLedger(cfg.ledger_path, cap), page=b"<html>PAGE</html>")


async def post(port: int, path: str, body: dict) -> httpx.Response:
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        return await c.post(path, json=body)


def test_the_page_is_served_and_unknown_paths_are_not(tmp_path: Path) -> None:
    async def go():
        srv = run_server(tmp_path, fake_stream()[0])
        port = await srv.start()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
                page, missing = await c.get("/"), await c.get("/etc/passwd")
            return page, missing
        finally:
            await srv.close()

    page, missing = asyncio.run(go())
    assert page.status_code == 200 and b"PAGE" in page.content and missing.status_code == 404


def test_an_owner_question_streams_the_answer_and_is_billed(tmp_path: Path) -> None:
    stream, calls = fake_stream()

    async def go():
        srv = run_server(tmp_path, stream)
        port = await srv.start()
        try:
            r = await post(port, "/api/chat", {"initData": signed(),
                                               "messages": [{"role": "user", "content": "hi"}]})
            return r, srv.ledger.spent()
        finally:
            await srv.close()

    r, spent = asyncio.run(go())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    assert "".join(e.get("t", "") for e in evs) == "Hello there" and evs[-1]["done"] is True
    assert calls == [[{"role": "user", "content": "hi"}]]
    assert spent == pytest.approx(cost_usd("claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 500}))


def test_anyone_else_gets_nothing_and_claude_is_never_called(tmp_path: Path) -> None:
    stream, calls = fake_stream()

    async def go():
        srv = run_server(tmp_path, stream)
        port = await srv.start()
        try:
            msgs = [{"role": "user", "content": "hi"}]
            return [await post(port, "/api/chat", {"initData": d, "messages": msgs})
                    for d in ("", signed(999), signed().replace("AAH", "AAB"))]
        finally:
            await srv.close()

    assert [r.status_code for r in asyncio.run(go())] == [403, 403, 403] and calls == []


def test_at_the_cap_claude_is_not_called(tmp_path: Path) -> None:
    stream, calls = fake_stream()

    async def go():
        srv = run_server(tmp_path, stream, cap=0.01)
        srv.ledger.add(0.02)
        port = await srv.start()
        try:
            return await post(port, "/api/chat", {"initData": signed(),
                                                  "messages": [{"role": "user", "content": "hi"}]})
        finally:
            await srv.close()

    r = asyncio.run(go())
    assert r.status_code == 429 and "budget" in r.json()["error"] and calls == []


def test_a_claude_failure_becomes_a_readable_line(tmp_path: Path) -> None:
    stream, _ = fake_stream(pieces=("Part",), fail=RuntimeError("boom"))

    async def go():
        srv = run_server(tmp_path, stream)
        port = await srv.start()
        try:
            return await post(port, "/api/chat", {"initData": signed(),
                                                  "messages": [{"role": "user", "content": "hi"}]})
        finally:
            await srv.close()

    evs = events(asyncio.run(go()).text)
    assert evs[0] == {"t": "Part"} and evs[-1]["error"] and not any(e.get("done") for e in evs)


def test_me_reports_the_budget_left(tmp_path: Path) -> None:
    async def go():
        srv = run_server(tmp_path, fake_stream()[0], cap=2.0)
        srv.ledger.add(0.5)
        port = await srv.start()
        try:
            return await post(port, "/api/me", {"initData": signed()})
        finally:
            await srv.close()

    r = asyncio.run(go())
    assert r.status_code == 200 and r.json()["left"] == pytest.approx(1.5)


# ---- the tunnel and the menu button ---------------------------------------------------------------

class FakeTunnel:
    def __init__(self) -> None:
        self.stopped = False
        self._exit = asyncio.Event()

    async def start(self, port: int) -> str:
        self.port = port
        return "https://quiet-owl.trycloudflare.com"

    async def wait(self) -> int:
        await self._exit.wait()
        return 0

    async def stop(self) -> None:
        self.stopped = True


class FakeBot:
    """The Bot API as the pins see it: records calls, hands out message ids, can refuse an edit."""

    def __init__(self, refuse_edit: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.refuse_edit = refuse_edit

    async def __call__(self, method: str, data: dict):
        self.calls.append((method, data))
        if method == "editMessageText" and self.refuse_edit:
            return {"ok": False, "description": "Bad Request: message to edit not found"}
        return {"ok": True, "result": {"message_id": 900 + len(self.calls)}}


def run_app(tmp_path: Path, bot: FakeBot):
    tunnels: list[FakeTunnel] = []

    def factory() -> FakeTunnel:
        tunnels.append(FakeTunnel())
        return tunnels[-1]

    async def go():
        cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=OWNERS, ledger_path=tmp_path / "s.json")
        app = MiniApp(cfg, stream_fn=fake_stream()[0], tunnel_factory=factory, bot=bot,
                      pins_path=tmp_path / "pins.json", apps_root=tmp_path / "apps")
        task = asyncio.create_task(app.run())
        for _ in range(200):
            if any(m == "pinChatMessage" or m == "editMessageText" for m, _ in bot.calls):
                break
            await asyncio.sleep(0.01)
        url = app.url
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return url

    return asyncio.run(go()), tunnels


def test_the_menu_stays_the_commands_and_a_pinned_button_opens_the_app(tmp_path: Path) -> None:
    bot = FakeBot()
    url, tunnels = run_app(tmp_path, bot)
    assert url == "https://quiet-owl.trycloudflare.com" and tunnels[0].stopped
    methods = [m for m, _ in bot.calls]
    assert methods[:3] == ["setChatMenuButton", "sendMessage", "pinChatMessage"]
    assert bot.calls[0][1]["menu_button"] == {"type": "commands"}                       # the / menu, kept
    button = bot.calls[1][1]["reply_markup"]["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == url + "/"
    # on stop the pinned message says offline instead of leading nowhere
    assert methods[-1] == "editMessageText" and "reply_markup" not in bot.calls[-1][1]
    assert json.loads((tmp_path / "pins.json").read_text()) == {str(OWNER): 902}


def test_the_next_start_edits_the_same_pinned_message(tmp_path: Path) -> None:
    (tmp_path / "pins.json").write_text(json.dumps({str(OWNER): 555}))
    bot = FakeBot()
    run_app(tmp_path, bot)
    edit = next(d for m, d in bot.calls if m == "editMessageText")
    assert edit["message_id"] == 555 and "reply_markup" in edit
    assert "sendMessage" not in [m for m, _ in bot.calls] and "pinChatMessage" not in [m for m, _ in bot.calls]


def test_a_deleted_pinned_message_is_sent_and_pinned_again(tmp_path: Path) -> None:
    (tmp_path / "pins.json").write_text(json.dumps({str(OWNER): 555}))
    bot = FakeBot(refuse_edit=True)
    run_app(tmp_path, bot)
    methods = [m for m, _ in bot.calls]
    assert "sendMessage" in methods and "pinChatMessage" in methods
    assert json.loads((tmp_path / "pins.json").read_text())[str(OWNER)] != 555


def test_the_tunnel_url_pattern_matches_cloudflared_output() -> None:
    line = "2026-09-24T07:30:00Z INF |  https://brave-cat-lamp-owl.trycloudflare.com                    |"
    assert miniapp.TUNNEL_URL_RE.search(line).group(0) == "https://brave-cat-lamp-owl.trycloudflare.com"


def test_off_unless_asked_and_fully_configured() -> None:
    base = {"CC_BUDDY_TELEGRAM": "1", "CC_BUDDY_TELEGRAM_TOKEN": TOKEN, "CC_BUDDY_TELEGRAM_OWNER": str(OWNER)}
    assert miniapp.configured(base).enabled is False
    assert miniapp.configured({**base, "CC_BUDDY_MINIAPP": "1"}).enabled is False          # no API key
    cfg = miniapp.configured({**base, "CC_BUDDY_MINIAPP": "1", "ANTHROPIC_API_KEY": "k"})
    assert cfg.enabled and cfg.model == "claude-opus-5-5" and cfg.effort == "medium" and cfg.owner_ids == OWNERS
    assert TOKEN not in repr(cfg)
