"""miniapp: buddy's Telegram Mini App — who may ask, what it costs, the apps routes (build,
delete, rename, undo), the wiring to the app maker, and the home page itself."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from cc_buddy_bridge import miniapp
from cc_buddy_bridge.miniapp import (
    MiniApp,
    MiniAppConfig,
    MiniAppServer,
    SpendLedger,
    check_init_data,
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


def test_hour_long_cache_writes_cost_twice_the_input_price() -> None:
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 3_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 2_000_000}}
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(5.0 + 2 * 8.0)
    assert cost_usd("claude-opus-5-5", {"cache_creation_input_tokens": 1_000_000}) == pytest.approx(5.0)


def test_an_unknown_model_costs_at_the_worst_price() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert cost_usd("some-future-model", usage) >= cost_usd("claude-opus-5-5", usage)


def test_the_ledger_tracks_every_day_and_never_refuses_without_a_cap(tmp_path: Path) -> None:
    day = {"d": "2026-09-24"}
    led = SpendLedger(tmp_path / "spend.json", today=lambda: day["d"])
    assert led.cap is None and led.left() is None and not led.over()
    led.add(40.0)
    led.add(0.7)
    assert led.spent() == pytest.approx(40.7) and not led.over()        # owner: "no claude limit, just track spend"
    day["d"] = "2026-09-25"
    led.add(0.3)
    assert led.spent() == pytest.approx(0.3)
    assert SpendLedger(tmp_path / "spend.json").days() == {"2026-09-24": pytest.approx(40.7),
                                                            "2026-09-25": pytest.approx(0.3)}


@pytest.mark.parametrize("cap", [0, 0.0, -1.0, None])
def test_a_cap_of_zero_or_less_is_no_cap(tmp_path: Path, cap) -> None:
    led = SpendLedger(tmp_path / "spend.json", cap)
    led.add(1000.0)
    assert led.cap is None and not led.over()


def test_a_cap_set_by_the_owner_counts_today_and_resets_tomorrow(tmp_path: Path) -> None:
    day = {"d": "2026-09-24"}
    led = SpendLedger(tmp_path / "spend.json", 1.0, today=lambda: day["d"])
    assert led.left() == 1.0 and not led.over()
    led.add(0.4)
    led.add(0.7)
    assert led.spent() == pytest.approx(1.1) and led.left() == 0.0 and led.over()
    day["d"] = "2026-09-25"
    assert led.left() == 1.0 and not led.over()


def test_the_ledger_reads_the_capped_versions_file_and_keeps_a_bounded_history(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    path.write_text(json.dumps({"date": "2026-09-24", "usd": 2.5}))                   # the format before tracking
    led = SpendLedger(path, today=lambda: "2026-09-24")
    assert led.spent() == pytest.approx(2.5)
    led.add(0.5)
    assert json.loads(path.read_text()) == {"days": {"2026-09-24": 3.0}}
    path.write_text(json.dumps({"days": {f"2020-01-{d:02d}": 1.0 for d in range(1, 32)} | {"bad": "x"}}))
    assert "bad" not in SpendLedger(path).days()
    for broken in ("not json", "[1, 2]", '{"days": 3}'):
        path.write_text(broken)
        assert SpendLedger(path).days() == {} and SpendLedger(path).spent() == 0.0


def test_the_ledger_keeps_only_the_newest_days(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(miniapp, "LEDGER_DAYS", 3)
    path = tmp_path / "spend.json"
    for d in range(1, 6):
        SpendLedger(path, today=lambda d=d: f"2026-09-{d:02d}").add(1.0)
    assert sorted(SpendLedger(path).days()) == ["2026-09-03", "2026-09-04", "2026-09-05"]


# ---- the server ---------------------------------------------------------------------------------

def events(body: str) -> list[dict]:
    return [json.loads(chunk[6:]) for chunk in body.split("\n\n") if chunk.startswith("data: ")]


def run_server(tmp_path: Path, cap: float = 0.0):
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=OWNERS, daily_usd=cap,
                        ledger_path=tmp_path / "spend.json")
    return MiniAppServer(cfg, SpendLedger(cfg.ledger_path, cap), page=b"<html>PAGE</html>")


async def post(port: int, path: str, body: dict) -> httpx.Response:
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        return await c.post(path, json=body)


def test_the_page_is_served_and_unknown_paths_are_not(tmp_path: Path) -> None:
    async def go():
        srv = run_server(tmp_path)
        port = await srv.start()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
                page, missing = await c.get("/"), await c.get("/etc/passwd")
            return page, missing
        finally:
            await srv.close()

    page, missing = asyncio.run(go())
    assert page.status_code == 200 and b"PAGE" in page.content and missing.status_code == 404


def test_anyone_else_gets_nothing_and_the_chat_route_is_gone(tmp_path: Path) -> None:
    async def go():
        srv = run_server(tmp_path)
        port = await srv.start()
        try:
            refused = [await post(port, "/api/me", {"initData": d})
                       for d in ("", signed(999), signed().replace("AAH", "AAB"))]
            chat = await post(port, "/api/chat", {"initData": signed(), "messages": [{"role": "user", "content": "hi"}]})
            return refused, chat
        finally:
            await srv.close()

    refused, chat = asyncio.run(go())
    assert [r.status_code for r in refused] == [403, 403, 403]
    assert chat.status_code == 404                     # the Claude chat was removed (owner, 2026-09-24)


@pytest.mark.parametrize("cap, want", [(0.0, None), (2.0, 2.0)])
def test_me_reports_todays_spend_and_the_cap_if_any(tmp_path: Path, cap: float, want) -> None:
    async def go():
        srv = run_server(tmp_path, cap=cap)
        srv.ledger.add(0.5)
        port = await srv.start()
        try:
            return await post(port, "/api/me", {"initData": signed()})
        finally:
            await srv.close()

    r = asyncio.run(go())
    assert r.status_code == 200 and r.json()["spent_today"] == pytest.approx(0.5) and r.json()["cap"] == want
    assert "left" not in r.json()


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
        app = MiniApp(cfg, tunnel_factory=factory, bot=bot,
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
    assert cfg.enabled and cfg.model == "claude-opus-5-5" and cfg.make_effort == "high" and cfg.owner_ids == OWNERS
    assert TOKEN not in repr(cfg)


@pytest.mark.parametrize("raw, cap", [(None, 0.0), ("", 0.0), ("0", 0.0), ("-3", 0.0), ("lots", 0.0), ("7.5", 7.5)])
def test_there_is_no_spend_cap_unless_one_is_set(raw, cap: float) -> None:
    env = {"CC_BUDDY_TELEGRAM": "1", "CC_BUDDY_TELEGRAM_TOKEN": TOKEN, "CC_BUDDY_TELEGRAM_OWNER": str(OWNER),
           "CC_BUDDY_MINIAPP": "1", "ANTHROPIC_API_KEY": "k"}
    if raw is not None:
        env["CC_BUDDY_MINIAPP_DAILY_USD"] = raw
    assert miniapp.configured(env).daily_usd == cap


# ---- the apps routes: build, delete, rename, undo ------------------------------------------------

DOC = ("<!doctype html><html><head><title>Habit Tracker</title><meta name=\"buddy-icon\" content=\"✅\">"
       "<meta name=\"description\" content=\"Check in daily and keep streaks.\"></head><body>MARK<script>"
       "function render() {} buddy.load().then(render); function add() { buddy.save(state); }</script></body></html>")
USAGE = {"input_tokens": 2000, "output_tokens": 10000}


def page_doc(mark: str, title: str = "Habit Tracker") -> str:
    return DOC.replace("MARK", mark).replace("Habit Tracker", title)


class SlowClaude:
    """A fake maker Claude that goes through the stages with a pause at each, so the stream can see them.
    ``gate`` holds the answer back until the test sets it."""

    def __init__(self, *answers: str, gate: asyncio.Event | None = None) -> None:
        self.answers, self.gate, self.calls = list(answers), gate, 0

    async def __call__(self, messages, progress):
        from cc_buddy_bridge.apps_maker import Generated

        self.calls += 1
        progress("thinking", 0)
        await asyncio.sleep(0.05)
        if self.gate is not None:
            await self.gate.wait()
        text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        progress("writing", 12_345)
        await asyncio.sleep(0.05)
        return Generated(f"```html\n{text}\n```", dict(USAGE), "end_turn", content=[{"type": "text", "text": text}])


class PhoneCheck:
    """The phone check, faked: a page holding ``bad`` has one problem; every page gets a picture."""

    def __init__(self, bad: str = "\0") -> None:
        self.bad = bad

    async def __call__(self, html: str, *, seed=None):
        from cc_buddy_bridge.app_check import CheckReport

        await asyncio.sleep(0.05)
        issues = ["Tapping Add threw TypeError."] if self.bad in html else []
        return CheckReport(ok=not issues, issues=issues, taps=9, fields=2, saves=3, screenshot=b"\xff\xd8jpeg")


def apps_server(tmp_path: Path, claude=None, check=None, cap: float = 0.0):
    """A MiniApp (so the real wiring: AppStore, AppMaker, ledger) with a fake Claude and a fake phone check."""
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=OWNERS, daily_usd=cap,
                        ledger_path=tmp_path / "spend.json")
    app = MiniApp(cfg, tunnel_factory=lambda: None, bot=FakeBot(),
                  pins_path=tmp_path / "pins.json", apps_root=tmp_path / "apps",
                  generate=claude or SlowClaude(page_doc("v1")), check=check or PhoneCheck(),
                  context=lambda: "Today is Thursday.")
    return app


def with_server(app, body):
    async def go():
        port = await app.server.start()
        try:
            return await body(port)
        finally:
            await app.server.close()

    return asyncio.run(go())


def test_miniapp_wires_the_maker_with_the_ledger_the_check_and_the_context(tmp_path: Path) -> None:
    claude = SlowClaude(page_doc("v1"))
    app = apps_server(tmp_path, claude)
    made = asyncio.run(app.maker.make("a habit tracker"))
    assert made.ok and made.check.summary() == "passed: 2 fields filled, 9 taps, 3 saves"
    assert app.ledger.spent() == pytest.approx(cost_usd("claude-opus-5-5", USAGE))           # tracked, no cap
    assert app.server.changing is app.changing and app.ledger.cap is None


def test_a_build_streams_its_stages_then_the_app_the_check_and_todays_spend(tmp_path: Path) -> None:
    app = apps_server(tmp_path, SlowClaude(page_doc("bad"), page_doc("good")), PhoneCheck(bad="bad"))

    async def body(port):
        return await post(port, "/api/make", {"initData": signed(), "request": "a habit tracker with streaks"})

    r = with_server(app, body)
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    stages = [e["s"] for e in evs if "s" in e]
    seen = [s for i, s in enumerate(stages) if i == 0 or stages[i - 1] != s]
    assert seen[:4] == ["thinking", "writing", "testing", "fixing"]
    assert {"s": "writing", "p": 12_345} in evs and {"s": "fixing", "p": 1} in evs
    done = evs[-1]
    assert done["done"] is True and done["url"].startswith("apps/habit-tracker/?t=") and done["app"]["icon"] == "✅"
    assert done["check"] == "passed: 2 fields filled, 9 taps, 3 saves" and done["rounds"] == 1 and done["problems"] == 0
    assert done["spent_today"] == pytest.approx(2 * cost_usd("claude-opus-5-5", USAGE)) and done["cap"] is None
    assert "good" in app.store.html("habit-tracker")


def test_a_change_goes_to_the_app_named_by_its_slug_and_an_unknown_one_is_refused(tmp_path: Path) -> None:
    app = apps_server(tmp_path, SlowClaude(page_doc("v2")))
    app.store.save(page_doc("v1"), "habit tracker")

    async def body(port):
        changed = await post(port, "/api/make", {"initData": signed(), "request": "add streaks", "app": "habit-tracker"})
        missing = await post(port, "/api/make", {"initData": signed(), "request": "x", "app": "no-such-app"})
        return changed, missing

    changed, missing = with_server(app, body)
    assert events(changed.text)[-1]["app"]["versions"] == 1 and "v2" in app.store.html("habit-tracker")
    assert missing.status_code == 404 and not app.changing


def test_a_build_that_fails_says_why_and_what_today_cost(tmp_path: Path) -> None:
    class Refuses(SlowClaude):
        async def __call__(self, messages, progress):
            from cc_buddy_bridge.apps_maker import Generated

            return Generated("I can't help with that.", dict(USAGE), "refusal")

    app = apps_server(tmp_path, Refuses("x"))
    evs = events(with_server(app, lambda port: post(port, "/api/make", {"initData": signed(), "request": "x"})).text)
    assert evs[-1]["error"].startswith("I couldn't build it: Claude declined") and evs[-1]["spent_today"] > 0


def test_a_build_is_refused_only_at_a_cap_the_owner_set(tmp_path: Path) -> None:
    capped = apps_server(tmp_path / "a", cap=1.0)
    capped.ledger.add(1.0)
    free = apps_server(tmp_path / "b")
    free.ledger.add(1000.0)
    ask = {"initData": signed(), "request": "a habit tracker"}
    assert with_server(capped, lambda port: post(port, "/api/make", ask)).status_code == 429
    assert events(with_server(free, lambda port: post(port, "/api/make", ask)).text)[-1]["done"] is True


def test_one_build_at_a_time_per_owner(tmp_path: Path) -> None:
    gate = asyncio.Event()
    app = apps_server(tmp_path, SlowClaude(page_doc("v1"), gate=gate))

    async def body(port):
        build = asyncio.create_task(post(port, "/api/make", {"initData": signed(), "request": "a habit tracker"}))
        await asyncio.sleep(0.2)
        second = await post(port, "/api/make", {"initData": signed(), "request": "another"})
        gate.set()
        return second, await build

    second, build = with_server(app, body)
    assert second.status_code == 409
    assert events(build.text)[-1]["done"] is True


def test_rename_undo_and_delete_from_the_page(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save(page_doc("v2"), "add streaks", slug="habit-tracker")

    async def body(port):
        base = "/api/apps/habit-tracker/"
        return [await post(port, base + "rename", {"initData": signed(), "title": "Daily habits"}),
                await post(port, base + "revert", {"initData": signed()}),
                await post(port, base + "revert", {"initData": signed()}),
                await post(port, base + "delete", {"initData": signed()}),
                await post(port, base + "delete", {"initData": signed()})]

    renamed, undone, nothing_left, deleted, again = with_server(app, body)
    assert renamed.json()["app"]["title"] == "Daily habits"
    assert undone.json()["app"]["versions"] == 0 and undone.json()["app"]["title"] == "Daily habits"
    assert nothing_left.status_code == 409 and "earlier version" in nothing_left.json()["error"]
    assert deleted.json() == {"ok": True, "deleted": "Daily habits"} and again.status_code == 404
    assert app.store.list() == [] and len(list((tmp_path / "apps" / ".trash").iterdir())) == 1


def test_an_empty_name_and_a_missing_app_are_refused(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")

    async def body(port):
        return [await post(port, "/api/apps/habit-tracker/rename", {"initData": signed(), "title": "   "}),
                await post(port, "/api/apps/nope/rename", {"initData": signed(), "title": "x"}),
                await post(port, "/api/apps/nope/revert", {"initData": signed()}),
                await post(port, "/api/apps/../delete", {"initData": signed()})]

    blank, *missing = with_server(app, body)
    assert blank.status_code == 400 and [r.status_code for r in missing] == [404, 404, 404]
    assert app.store.info("habit-tracker").title == "Habit Tracker"


def test_only_the_owner_can_delete_rename_or_undo(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save(page_doc("v2"), "add streaks", slug="habit-tracker")

    async def body(port):
        return [await post(port, f"/api/apps/habit-tracker/{action}", {"initData": d, "title": "Mine now"})
                for action in ("delete", "rename", "revert") for d in ("", signed(999), signed().replace("AAH", "AAB"))]

    assert all(r.status_code == 403 for r in with_server(app, body))
    info = app.store.info("habit-tracker")
    assert info.title == "Habit Tracker" and info.versions == 1 and "v2" in app.store.html("habit-tracker")


def test_an_app_being_changed_cannot_be_deleted_renamed_or_undone_under_the_build(tmp_path: Path) -> None:
    gate = asyncio.Event()
    app = apps_server(tmp_path, SlowClaude(page_doc("v3"), gate=gate))
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save(page_doc("v2"), "x", slug="habit-tracker")

    async def body(port):
        build = asyncio.create_task(post(port, "/api/make", {"initData": signed(), "request": "dark mode",
                                                              "app": "habit-tracker"}))
        await asyncio.sleep(0.2)
        during = [await post(port, f"/api/apps/habit-tracker/{a}", {"initData": signed(), "title": "New"})
                  for a in ("delete", "rename", "revert")]
        busy = set(app.changing)
        gate.set()
        await build
        after = await post(port, "/api/apps/habit-tracker/rename", {"initData": signed(), "title": "New"})
        return during, busy, after

    during, busy, after = with_server(app, body)
    assert [r.status_code for r in during] == [409, 409, 409] and busy == {"habit-tracker"}
    assert after.status_code == 200 and not app.changing and "v3" in app.store.html("habit-tracker")


def test_a_build_from_the_chat_door_also_holds_off_the_page(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.changing.add("habit-tracker")                     # what ChatMaker.building does, once the daemon shares it
    r = with_server(app, lambda port: post(port, "/api/apps/habit-tracker/delete", {"initData": signed()}))
    assert r.status_code == 409 and app.store.info("habit-tracker") is not None


def test_the_daemon_shares_one_set_of_apps_being_changed_between_the_doors(monkeypatch) -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge import apps_maker, daemon

    pytest.importorskip("anthropic")

    class FakeMiniApp:
        def __init__(self, cfg) -> None:
            self.changing: set[str] = set()
            self.url = ""

        async def run(self) -> None:
            return None

    monkeypatch.setattr(miniapp, "configured", lambda: MiniAppConfig(enabled=True, token=TOKEN, owner_ids=OWNERS))
    monkeypatch.setattr(miniapp, "MiniApp", FakeMiniApp)
    inlet = SimpleNamespace(_maker=None, _spawn=lambda coro, name: None)
    host = SimpleNamespace(_telegram=inlet)
    tasks: list = []

    async def go():
        daemon.Daemon._start_miniapp(host, tasks)
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert isinstance(inlet._maker, apps_maker.ChatMaker) and inlet._maker.building is host._miniapp.changing


# ---- the home page itself -------------------------------------------------------------------------

def page_script() -> str:
    import re

    return re.findall(r"<script>(.*?)</script>", miniapp.PAGE_PATH.read_text(), re.S)[-1]


def test_the_home_pages_script_parses(tmp_path: Path) -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    (tmp_path / "page.js").write_text(page_script())
    (tmp_path / "broken.js").write_text(page_script() + "\n})(;")                     # positive control
    assert subprocess.run([node, "--check", str(tmp_path / "page.js")], capture_output=True).returncode == 0
    assert subprocess.run([node, "--check", str(tmp_path / "broken.js")], capture_output=True).returncode != 0


def test_app_names_and_descriptions_only_reach_the_page_as_text() -> None:
    # They come from pages Claude wrote. The only innerHTML left is the chat's own Markdown renderer.
    script = page_script()
    apps_part = script[script.index("// ---- apps ----"):script.rindex("})();")]
    assert "innerHTML" not in apps_part and "insertAdjacentHTML" not in apps_part
    assert "textContent" in apps_part


TELEGRAM_STUB = """window.__confirms = []; window.__haptics = []; window.__popups = [];
window.Telegram = { WebApp: { initData: %s, ready() {}, expand() {}, isVersionAtLeast: () => true,
  showConfirm(text, cb) { window.__confirms.push(text); setTimeout(() => cb(window.__answer !== false), 0); },
  showPopup(p, cb) { window.__confirms.push(p.message); window.__popups.push(p);
    setTimeout(() => cb(window.__answer !== false ? "yes" : "no"), 0); },
  HapticFeedback: { notificationOccurred(k) { window.__haptics.push(k); }, impactOccurred() {} },
  initDataUnsafe: { user: { id: 4242, first_name: "O" } }, version: "9.1", platform: "ios",
  themeParams: { bg_color: "#000000", button_color: "#3e88f7" },
  BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
  MainButton: { show() {}, hide() {}, setText() {}, onClick() {}, offClick() {} } } };"""


def browse(tmp_path: Path, script, *, color_scheme: str = "light", claude=None):
    """Open the real home page, served by the real server, in headless Chromium at a phone's size, with a
    stand-in Telegram that carries a signed initData; then run ``script(page, app)``."""
    pw = pytest.importorskip("playwright.async_api")
    app = apps_server(tmp_path, claude)

    async def go():
        port = await app.server.start()
        try:
            async with pw.async_playwright() as p:
                try:
                    b = await p.chromium.launch(headless=True)
                except Exception as e:  # noqa: BLE001
                    pytest.skip(f"Chromium does not start here ({type(e).__name__})")
                try:
                    ctx = await b.new_context(viewport={"width": 390, "height": 760}, has_touch=True,
                                              color_scheme=color_scheme)
                    stub = TELEGRAM_STUB % json.dumps(signed())
                    await ctx.route("https://telegram.org/**", lambda route: route.fulfill(
                        status=200, content_type="application/javascript", body=stub))
                    page = await ctx.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    await page.goto(f"http://127.0.0.1:{port}/")
                    out = await script(page, app)
                    return out, errors
                finally:
                    await b.close()
        finally:
            await app.server.close()

    return asyncio.run(go())


def seed_apps(app) -> None:
    app.store.save(page_doc("v1", "Workout Log").replace("✅", "🏋️").replace("Check in daily and keep streaks.",
                                                                           "Sets, reps and weights."), "a workout log")
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save(page_doc("v2"), "add streaks", slug="habit-tracker")
    app.ledger.add(0.25)


def test_the_home_page_lists_apps_with_icons_descriptions_and_todays_spend(tmp_path: Path) -> None:
    async def script(page, app):
        seed_apps(app)
        await page.reload()
        await page.wait_for_selector(".app")
        return await page.evaluate("""() => ({
            rows: [...document.querySelectorAll('.app')].map((r) => ({
                icon: r.querySelector('.icon').textContent, name: r.querySelector('.name').textContent,
                desc: r.querySelector('.desc') && r.querySelector('.desc').textContent,
                more: r.querySelector('.more').getAttribute('aria-label'),
                moreIsButton: r.querySelector('.more').tagName })),
            spent: document.getElementById('spent').textContent,
            wide: document.documentElement.scrollWidth > document.documentElement.clientWidth })""")

    out, errors = browse(tmp_path, script)
    assert errors == []
    assert [(r["icon"], r["name"], r["desc"]) for r in out["rows"]] == [
        ("✅", "Habit Tracker", "Check in daily and keep streaks."), ("🏋️", "Workout Log", "Sets, reps and weights.")]
    assert out["rows"][0]["more"] == "More for Habit Tracker" and out["rows"][0]["moreIsButton"] == "BUTTON"
    assert out["spent"] == "$0.25 spent today" and out["wide"] is False


def test_a_hostile_app_name_is_shown_as_text_never_run(tmp_path: Path) -> None:
    async def script(page, app):
        app.store.save(page_doc("v1"), "habit tracker")
        app.store.rename("habit-tracker", '<img src=x onerror="window.__pwned=1">')
        await page.reload()
        await page.wait_for_selector(".app")
        await page.click(".app .more")
        await page.wait_for_timeout(100)
        return await page.evaluate("""() => ({ pwned: window.__pwned === 1, imgs: document.querySelectorAll('img').length,
            name: document.querySelector('.app .name').textContent,
            sheet: document.getElementById('sheet-title').textContent })""")

    out, _ = browse(tmp_path, script)
    assert out == {"pwned": False, "imgs": 0, "name": '<img src=x onerror="window.__pwned=1">',
                   "sheet": '<img src=x onerror="window.__pwned=1">'}


def test_the_sheet_offers_undo_only_when_there_is_an_earlier_version_and_escape_closes_it(tmp_path: Path) -> None:
    async def script(page, app):
        seed_apps(app)
        await page.reload()
        await page.wait_for_selector(".app")
        seen = {}
        for name in ("Habit Tracker", "Workout Log"):
            await page.click(f"button[aria-label='More for {name}']")
            seen[name] = await page.evaluate("""() => ({ open: document.getElementById('sheet').open,
                undo: !document.getElementById('act-undo').hidden,
                focus: document.activeElement.textContent,
                actions: [...document.querySelectorAll('#sheet-actions button')].filter((b) => !b.hidden)
                  .map((b) => b.textContent) })""")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(50)
            seen[name]["after"] = await page.evaluate("""() => ({ open: document.getElementById('sheet').open,
                focus: document.activeElement.getAttribute('aria-label') })""")
        return seen

    seen, errors = browse(tmp_path, script)
    assert errors == []
    habit, workout = seen["Habit Tracker"], seen["Workout Log"]
    assert habit["open"] and habit["undo"] and habit["focus"] == "Change"
    assert habit["actions"] == ["Change", "Rename", "Undo last change", "Delete"]
    assert workout["actions"] == ["Change", "Rename", "Delete"] and not workout["undo"]
    assert habit["after"] == {"open": False, "focus": "More for Habit Tracker"}                  # focus comes back


def test_rename_undo_and_delete_from_the_sheet(tmp_path: Path) -> None:
    async def script(page, app):
        seed_apps(app)
        await page.reload()
        await page.wait_for_selector(".app")
        more = "button[aria-label='More for {}']"
        await page.click(more.format("Habit Tracker"))
        await page.click("#sheet-actions button[data-act=rename]")
        await page.fill("#rename-input", "Daily habits")
        await page.press("#rename-input", "Enter")
        await page.wait_for_selector("button[aria-label='More for Daily habits']")
        renamed = app.store.info("habit-tracker").title

        await page.click(more.format("Daily habits"))
        await page.click("#sheet-actions button[data-act=undo]")
        await page.wait_for_function("document.getElementById('apps-notice').textContent.includes('back to how')")
        versions = app.store.info("habit-tracker").versions

        await page.evaluate("window.__answer = false")               # "Delete?" answered no: nothing happens
        await page.click(more.format("Workout Log"))
        await page.click("#sheet-actions button[data-act=delete]")
        await page.wait_for_timeout(150)
        kept = app.store.info("workout-log") is not None
        await page.evaluate("window.__answer = true")
        await page.click("#sheet-actions button[data-act=delete]")
        await page.wait_for_function("document.querySelectorAll('.app').length === 1")
        gone = app.store.info("workout-log") is None
        notice = await page.text_content("#apps-notice")
        await page.click("#notice-undo")                                  # Undo brings the deleted app back
        await page.wait_for_function("document.querySelectorAll('.app').length === 2")
        return {"renamed": renamed, "versions": versions, "kept": kept, "gone": gone,
                "confirms": await page.evaluate("window.__confirms"),
                "popups": await page.evaluate("window.__popups"), "notice": notice,
                "back": app.store.info("workout-log") is not None,
                "after": await page.text_content("#apps-notice")}

    out, errors = browse(tmp_path, script)
    assert errors == []
    assert out["renamed"] == "Daily habits" and out["versions"] == 0 and out["kept"] and out["gone"]
    assert out["confirms"][0].startswith("Undo the last change to Daily habits?")
    assert out["confirms"][1:] == ["Delete Workout Log? It goes to buddy's trash on the Mac, with its data, and "
                                   "Undo brings it back."] * 2
    assert out["popups"][1]["buttons"][0] == {"id": "yes", "type": "destructive", "text": "Delete"}
    assert out["notice"] == "Deleted Workout Log." and out["back"] and out["after"] == "Workout Log is back."


def test_a_build_from_the_page_shows_each_stage_in_words_then_opens_the_app(tmp_path: Path) -> None:
    async def script(page, app):
        await page.evaluate("""() => { window.__said = [];
            new MutationObserver(() => window.__said.push(document.getElementById('make-status').textContent))
              .observe(document.getElementById('make-status'), { childList: true, characterData: true, subtree: true }); }""")
        await page.fill("#want", "a habit tracker")
        await page.click("#make")
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Ready')")
        said = await page.evaluate("window.__said")
        await page.wait_for_url("**/apps/habit-tracker/?t=*")
        return said, app.ledger.spent()

    (said, spent), errors = browse(tmp_path, script, claude=SlowClaude(page_doc("v1")))
    assert errors == []
    words = [s.rsplit(" ", 1)[0] for s in said if not s.startswith("Ready")]      # without the clock
    assert "Thinking it through…" in words and "Writing it… 12k" in words and "Testing it on a phone…" in words
    assert said[-1] == "Ready: Habit Tracker. Tested on a phone."
    assert spent > 0


def test_a_repair_round_shows_how_many_problems_are_being_fixed(tmp_path: Path) -> None:
    async def script(page, app):
        app.maker._check = PhoneCheck(bad="bad")
        await page.evaluate("""() => { window.__said = [];
            new MutationObserver(() => window.__said.push(document.getElementById('make-status').textContent))
              .observe(document.getElementById('make-status'), { childList: true, characterData: true, subtree: true }); }""")
        await page.fill("#want", "a habit tracker")
        await page.click("#make")
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Ready')")
        return await page.evaluate("window.__said")

    said, errors = browse(tmp_path, script, claude=SlowClaude(page_doc("bad"), page_doc("good")))
    assert any(s.startswith("Fixing 1 problem… ") for s in said)


def test_the_page_follows_the_dark_theme(tmp_path: Path) -> None:
    async def bg(page, app):
        return await page.evaluate("getComputedStyle(document.body).backgroundColor")

    light, _ = browse(tmp_path / "l", bg)
    dark, _ = browse(tmp_path / "d", bg, color_scheme="dark")
    assert light == "rgb(255, 255, 255)" and dark == "rgb(23, 23, 27)"


# ---- review fixes: apps fenced in, a bounded shutdown, a build that outlives its page --------------------------

async def raw(port: int, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        return await c.request(method, path, json=body, headers=headers or {})


def test_an_app_page_opens_only_with_its_own_token_and_is_served_sandboxed(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("SECRET-DEFAULT"), "habit tracker")
    app.store.save(page_doc("other", "Reading List"), "reading list")

    async def body(port):
        good = miniapp.app_token("habit-tracker", TOKEN)
        return {"none": await raw(port, "GET", "/apps/habit-tracker/"),
                "missing": await raw(port, "GET", "/apps/no-such-app/"),
                "other": await raw(port, "GET", f"/apps/reading-list/?t={good}"),
                "forged": await raw(port, "GET", "/apps/habit-tracker/?t=9999999999." + "0" * 40),
                "old": await raw(port, "GET", "/apps/habit-tracker/?t="
                                 + miniapp.app_token("habit-tracker", TOKEN, now=time.time() - 2 * 86400)),
                "good": await raw(port, "GET", f"/apps/habit-tracker/?t={good}"), "port": port}

    out = with_server(app, body)
    for key in ("none", "missing", "other", "forged", "old"):             # the same answer, and no page
        assert out[key].status_code == 302 and "SECRET" not in out[key].text, key
    assert out["none"].headers["location"] == "/?open=habit-tracker"
    assert out["missing"].headers["location"] == "/?open=no-such-app"
    good = out["good"]
    csp = good.headers["content-security-policy"]
    assert good.status_code == 200 and "SECRET-DEFAULT" in good.text
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    assert f"connect-src http://127.0.0.1:{out['port']}/api/apps/habit-tracker/ " in csp
    assert good.headers["referrer-policy"] == "no-referrer"


def test_an_app_token_reaches_its_own_data_and_nothing_else(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save(page_doc("x", "Reading List"), "reading list")
    app.store.save_data("reading-list", {"v": 1, "books": ["private"]})

    async def body(port):
        t = miniapp.app_token("habit-tracker", TOKEN)
        mine = "/api/apps/habit-tracker/"
        out = {"save": await raw(port, "POST", mine + "save", {"t": t, "data": {"v": 1}}),
               "load": await raw(port, "POST", mine + "load", {"t": t}),
               "preflight": await raw(port, "OPTIONS", mine + "save")}
        for name, path, extra in [("other_load", "/api/apps/reading-list/load", {}),
                                  ("other_save", "/api/apps/reading-list/save", {"data": 1}),
                                  ("list", "/api/apps", {}), ("make", "/api/make", {"request": "x"}),
                                  ("delete", mine + "delete", {})]:
            out[name] = await raw(port, "POST", path, {"t": t, **extra})
        # the owner's initData from a sandboxed page (Origin: null) or another site opens nothing either
        out["null_origin"] = await raw(port, "POST", "/api/apps", {"initData": signed()}, {"Origin": "null"})
        app.server.public_url = "https://quiet-owl.trycloudflare.com"
        out["other_site"] = await raw(port, "POST", "/api/apps", {"initData": signed()},
                                      {"Origin": "https://evil.example"})
        out["home"] = await raw(port, "POST", "/api/apps", {"initData": signed()},
                                {"Origin": "https://quiet-owl.trycloudflare.com"})
        return out

    out = with_server(app, body)
    assert out["save"].json()["ok"] and out["load"].json() == {"data": {"v": 1}}
    assert out["load"].headers["access-control-allow-origin"] == "*"
    assert out["preflight"].status_code == 204
    for key in ("other_load", "other_save", "list", "make", "delete", "null_origin", "other_site"):
        assert out[key].status_code == 403, key
    assert out["home"].status_code == 200
    assert app.store.load_data("reading-list") == {"v": 1, "books": ["private"]}
    assert app.store.info("habit-tracker") is not None


def test_a_sandboxed_app_in_a_browser_reaches_its_own_data_only(tmp_path: Path) -> None:
    """The real page, the real server and Chromium: what the app's own script can reach."""
    pw = pytest.importorskip("playwright.async_api")
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save_data("habit-tracker", {"v": 1, "n": 1})
    app.store.save(page_doc("x", "Reading List"), "reading list")
    app.store.save_data("reading-list", {"v": 1, "books": ["private"]})

    probe = """async (initData) => {
      const out = {};
      const tryFetch = async (name, path, body) => {
        try { const r = await fetch(path, { method: "POST", headers: { "Content-Type": "text/plain" },
                                            body: JSON.stringify(body) }); out[name] = r.status; }
        catch (e) { out[name] = "blocked"; } };
      await tryFetch("list", "/api/apps", { initData });
      await tryFetch("other", "/api/apps/reading-list/load", { initData });
      await tryFetch("make", "/api/make", { initData, request: "x" });
      try { localStorage.getItem("buddy-claude-history"); out.storage = "read"; } catch (e) { out.storage = "denied"; }
      out.loaded = await buddy.load();
      await buddy.save({ v: 1, n: 2 });
      out.initData = !!(window.Telegram && Telegram.WebApp && Telegram.WebApp.initData);
      return out; }"""

    async def go():
        port = await app.server.start()
        try:
            async with pw.async_playwright() as p:
                try:
                    b = await p.chromium.launch(headless=True)
                except Exception as e:  # noqa: BLE001
                    pytest.skip(f"Chromium does not start here ({type(e).__name__})")
                try:
                    ctx = await b.new_context()
                    await ctx.route("https://telegram.org/**", lambda route: route.fulfill(
                        status=200, content_type="application/javascript", body="window.Telegram={WebApp:{}};"))
                    page = await ctx.new_page()
                    url = f"http://127.0.0.1:{port}/" + app.server.open_url("habit-tracker")
                    await page.goto(url)
                    return await page.evaluate(probe, signed())
                finally:
                    await b.close()
        finally:
            await app.server.close()

    out = asyncio.run(go())
    assert out["list"] == out["other"] == out["make"] == "blocked"         # the CSP stops them at the page
    assert out["storage"] == "denied" and out["initData"] is False
    assert out["loaded"] == {"v": 1, "n": 1} and app.store.load_data("habit-tracker") == {"v": 1, "n": 2}
    assert app.store.load_data("reading-list") == {"v": 1, "books": ["private"]}


def test_shutdown_does_not_wait_for_a_build_that_is_still_streaming(tmp_path: Path) -> None:
    gate = asyncio.Event()
    app = apps_server(tmp_path, SlowClaude(page_doc("v1"), gate=gate))

    async def go():
        port = await app.server.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        raw_body = json.dumps({"initData": signed(), "request": "a habit tracker"}).encode()
        writer.write(b"POST /api/make HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                     + f"Content-Length: {len(raw_body)}\r\n\r\n".encode() + raw_body)
        await writer.drain()
        await reader.readuntil(b"data: ")                                   # the build is streaming
        t0 = time.perf_counter()
        await asyncio.wait_for(app.server.close(), 20)
        took = time.perf_counter() - t0
        writer.close()
        return took

    assert asyncio.run(go()) < miniapp.CLOSE_WAIT_SECS + 1.0


def test_a_build_whose_page_went_away_sends_its_result_to_the_chat(tmp_path: Path) -> None:
    gate = asyncio.Event()
    app = apps_server(tmp_path, SlowClaude(page_doc("v1"), gate=gate))
    bot = app.pins._bot

    async def body(port):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        raw_body = json.dumps({"initData": signed(), "request": "a habit tracker"}).encode()
        writer.write(b"POST /api/make HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                     + f"Content-Length: {len(raw_body)}\r\n\r\n".encode() + raw_body)
        await writer.drain()
        await reader.readuntil(b"data: ")
        building = (await raw(port, "POST", "/api/apps", {"initData": signed()})).json()["building"]
        again = await raw(port, "POST", "/api/make", {"initData": signed(), "request": "another"})
        writer.transport.abort()                                            # the phone locked
        await asyncio.sleep(0.1)
        gate.set()
        for _ in range(300):
            if any(m == "sendMessage" for m, _ in bot.calls):
                break
            await asyncio.sleep(0.02)
        return building, again

    building, again = with_server(app, body)
    assert building and building[0]["request"] == "a habit tracker" and building[0]["app"] == ""
    assert again.status_code == 409 and again.json()["building"]
    sent = [d for m, d in bot.calls if m == "sendMessage"]
    assert sent and sent[0]["chat_id"] == OWNER and sent[0]["text"].startswith("✅ Habit Tracker is ready.")
    assert app.store.info("habit-tracker") is not None and app.server.builds == {}


def test_an_undo_sent_twice_pops_one_version_only(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save(page_doc("v2"), "x", slug="habit-tracker")
    app.store.save(page_doc("v3"), "y", slug="habit-tracker")

    async def body(port):
        base = "/api/apps/habit-tracker/revert"
        return [await post(port, base, {"initData": signed(), "versions": 2}),
                await post(port, base, {"initData": signed(), "versions": 2})]

    first, second = with_server(app, body)
    assert first.status_code == 200 and second.status_code == 409 and "already" in second.json()["error"]
    assert "v2" in app.store.html("habit-tracker") and app.store.info("habit-tracker").versions == 1


def test_a_deleted_app_is_restored_from_the_page(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")
    app.store.save_data("habit-tracker", {"v": 1, "n": 5})

    async def body(port):
        base = "/api/apps/habit-tracker/"
        return [await post(port, base + "delete", {"initData": signed()}),
                await post(port, base + "restore", {"initData": signed()}),
                await post(port, base + "restore", {"initData": signed()})]

    deleted, restored, again = with_server(app, body)
    assert deleted.status_code == 200 and restored.json()["app"]["slug"] == "habit-tracker"
    assert again.status_code == 404 and app.store.load_data("habit-tracker") == {"v": 1, "n": 5}


def test_each_spend_mark_crossed_is_a_line_in_the_chat_and_nothing_is_refused(tmp_path: Path) -> None:
    led = SpendLedger(tmp_path / "s.json", today=lambda: "2026-09-24")
    marks: list[tuple[float, float]] = []
    led.on_cross = lambda mark, total: marks.append((mark, total))
    led.add(4.0)
    led.add(2.0)                                           # past 5
    led.add(1.0)
    led.add(60.0)                                          # past 20 and 50 at once: one line, the higher mark
    assert marks == [(5.0, 6.0), (50.0, 67.0)] and not led.over()

    app = apps_server(tmp_path / "b")

    async def go():
        app.ledger.add(6.0)
        await asyncio.sleep(0.05)

    asyncio.run(go())
    sent = [d for m, d in app.pins._bot.calls if m == "sendMessage"]
    assert len(sent) == 1 and "$6.00 today" in sent[0]["text"] and "Nothing is capped" in sent[0]["text"]


def test_an_open_button_goes_through_the_home_page(tmp_path: Path) -> None:
    app = apps_server(tmp_path)
    app.url = "https://quiet-owl.trycloudflare.com"
    assert app.app_url("habit-tracker") == "https://quiet-owl.trycloudflare.com/?open=habit-tracker"


# ---- review fixes on the home page ------------------------------------------------------------------------

def test_an_open_button_from_the_chat_opens_the_app_without_the_owners_signature(tmp_path: Path) -> None:
    async def script(page, app):
        app.store.save(page_doc("v1"), "habit tracker")
        base = page.url.rstrip("/")
        await page.goto(base + "/?open=habit-tracker")
        await page.wait_for_url("**/apps/habit-tracker/?t=*")
        at_app = page.url
        await page.goto(base + "/?open=no-such-app")
        await page.wait_for_function("document.getElementById('apps-notice').textContent !== ''")
        return at_app, await page.text_content("#apps-notice")

    (url, missing), errors = browse(tmp_path, script)
    from urllib.parse import unquote

    hash_part = unquote(unquote(url.split("#", 1)[1]))
    assert "tgWebAppVersion=9.1" in hash_part and '"first_name":"O"' in hash_part
    assert "hash=" not in hash_part and "auth_date" not in hash_part and str(OWNER) not in hash_part
    assert missing == "That app is no longer here. It may have been deleted."


def test_a_change_picked_during_a_build_keeps_its_words_and_target(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def script(page, app):
        app.store.save(page_doc("v1", "Reading List"), "reading list")
        await page.reload()
        await page.wait_for_selector(".app")
        await page.fill("#want", "a habit tracker")
        await page.click("#make")
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Thinking')")
        await page.click("button[aria-label='More for Reading List']")
        await page.click("#sheet-actions button[data-act=change]")
        await page.fill("#want", "add a dark mode")
        gate.set()
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Ready')")
        await page.wait_for_timeout(1500)
        return await page.evaluate("""() => ({ url: location.href, want: document.getElementById('want').value,
            editing: document.getElementById('editing-text').textContent,
            editingShown: !document.getElementById('editing').hidden,
            open: [...document.querySelectorAll('#make-result button')].map((b) => b.textContent) })""")

    out, errors = browse(tmp_path, script, claude=SlowClaude(page_doc("v1"), gate=gate))
    assert errors == []
    assert out["want"] == "add a dark mode" and out["editingShown"] and out["editing"] == "Changing Reading List"
    assert "/apps/" not in out["url"] and out["open"] == ["Open Habit Tracker"]    # no jump away mid-change


def test_a_build_with_problems_names_the_first_one_and_does_not_jump_into_the_app(tmp_path: Path) -> None:
    async def script(page, app):
        app.maker._check = PhoneCheck(bad="v1")
        app.maker.max_repairs = 0
        await page.fill("#want", "a habit tracker")
        await page.click("#make")
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Ready')")
        await page.wait_for_timeout(1500)
        return await page.evaluate("""() => ({ url: location.href, line: document.getElementById('make-status').textContent,
            open: [...document.querySelectorAll('#make-result button')].map((b) => b.textContent) })""")

    out, _ = browse(tmp_path, script)
    assert out["line"] == "Ready: Habit Tracker, but 1 thing may not work: Tapping Add threw TypeError."
    assert "/apps/" not in out["url"] and out["open"] == ["Open Habit Tracker"]


def test_a_page_opened_again_mid_build_shows_it_and_holds_off_build_and_the_sheet(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def script(page, app):
        app.store.save(page_doc("v1"), "habit tracker")
        await page.reload()
        await page.wait_for_selector(".app")
        await page.click("button[aria-label='More for Habit Tracker']")
        await page.click("#sheet-actions button[data-act=change]")
        await page.fill("#want", "add streaks")
        await page.click("#make")
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Changing')")
        await page.reload()                                           # the phone locked; the page is back
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Still building')")
        during = await page.evaluate("""() => ({ build: document.getElementById('make').disabled,
            more: document.querySelector('.app .more').disabled,
            when: document.querySelector('.app .when').textContent })""")
        gate.set()
        await page.wait_for_function("document.getElementById('make-status').textContent.startsWith('Finished')",
                                     timeout=15000)
        return during, await page.text_content("#make-status")

    (during, after), errors = browse(tmp_path, script, claude=SlowClaude(page_doc("v2"), gate=gate))
    assert during == {"build": True, "more": True, "when": "Being changed…"}
    assert after == "Finished. buddy sent the result to the chat."


def test_the_sheet_waits_while_an_action_is_on_its_way(tmp_path: Path) -> None:
    async def script(page, app):
        seed_apps(app)
        await page.reload()
        await page.wait_for_selector(".app")

        async def slow(route):
            await asyncio.sleep(0.6)
            await route.continue_()

        await page.route("**/revert", slow)
        await page.click("button[aria-label='More for Habit Tracker']")
        await page.click("#sheet-actions button[data-act=undo]")
        await page.wait_for_timeout(150)
        busy = await page.evaluate("""() => ({ undo: document.querySelector('[data-act=undo]').disabled,
            del: document.querySelector('[data-act=delete]').disabled,
            said: document.getElementById('sheet-error').textContent })""")
        await page.wait_for_function("document.getElementById('apps-notice').textContent.includes('back to how')")
        return busy, app.store.info("habit-tracker").versions

    (busy, versions), errors = browse(tmp_path, script)
    assert busy == {"undo": True, "del": True, "said": "Undoing…"} and versions == 0 and errors == []


def test_changing_an_app_deleted_meanwhile_stops_changing_it(tmp_path: Path) -> None:
    async def script(page, app):
        app.store.save(page_doc("v1"), "habit tracker")
        await page.reload()
        await page.wait_for_selector(".app")
        await page.click("button[aria-label='More for Habit Tracker']")
        await page.click("#sheet-actions button[data-act=change]")
        app.store.delete("habit-tracker")                             # from the chat, meanwhile
        await page.fill("#want", "add streaks")
        await page.click("#make")
        await page.wait_for_function("document.getElementById('make-status').textContent.includes('deleted')")
        return await page.evaluate("""() => ({ line: document.getElementById('make-status').textContent,
            editing: !document.getElementById('editing').hidden, button: document.getElementById('make').textContent,
            want: document.getElementById('want').value })""")

    out, _ = browse(tmp_path, script)
    assert out == {"line": "Habit Tracker was deleted. Describe it again to build a new one.", "editing": False,
                   "button": "Build it", "want": "add streaks"}




def test_an_app_reports_what_it_does_on_the_phone_to_the_log(tmp_path: Path, caplog) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")

    async def body(port):
        t = miniapp.app_token("habit-tracker", TOKEN)
        mine = "/api/apps/habit-tracker/report"
        ok = await raw(port, "POST", mine, {"t": t, "platform": "ios", "version": "9.1", "bridge": "proxy",
                                            "main_button": 'shown "New group"',
                                            "errors": ["TypeError: x is null\nsecond line @12"] * 7})
        forged = await raw(port, "POST", mine, {"t": "nope", "platform": "ios"})
        other = await raw(port, "POST", "/api/apps/reading-list/report", {"t": t, "platform": "ios"})
        return ok, forged, other

    with caplog.at_level("INFO", logger="cc_buddy_bridge.miniapp"):
        ok, forged, other = with_server(app, body)
    assert ok.status_code == 200 and forged.status_code == 403 and other.status_code == 403
    lines = [r.getMessage() for r in caplog.records if "on the phone" in r.getMessage()]
    assert len(lines) == 1 and "platform=ios" in lines[0] and "bridge=proxy" in lines[0] and "errors=7" in lines[0]
    assert "\n" not in lines[0] and lines[0].count(" | ") == 5                       # five errors at most, one line


def test_opening_loading_and_saving_an_app_are_logged(tmp_path: Path, caplog) -> None:
    app = apps_server(tmp_path)
    app.store.save(page_doc("v1"), "habit tracker")

    async def body(port):
        t = miniapp.app_token("habit-tracker", TOKEN)
        page = await raw(port, "GET", f"/apps/habit-tracker/?t={t}")
        await raw(port, "POST", "/api/apps/habit-tracker/save", {"t": t, "data": {"v": 1}})
        await raw(port, "POST", "/api/apps/habit-tracker/load", {"t": t})
        return page

    with caplog.at_level("INFO", logger="cc_buddy_bridge.miniapp"):
        page = with_server(app, body)
    said = " / ".join(r.getMessage() for r in caplog.records)
    assert page.status_code == 200 and "/buddy.js" in page.text
    assert "habit-tracker opened" in said and "habit-tracker saved" in said and "habit-tracker loaded (has data)" in said
