"""apps_maker: buddy builds small apps ("make me a habit tracker") that open inside Telegram and keep their data."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from cc_buddy_bridge.apps_maker import AppMaker, AppStore, extract_html, serve_app_html, slugify
from cc_buddy_bridge.miniapp import MiniAppConfig, MiniAppServer, SpendLedger, cost_usd, sign_init_data

TOKEN = "123456:TEST-token"
OWNER = 4242
DOC = "<!doctype html><html><head><title>Habit Tracker</title></head><body>hi</body></html>"


def signed(user_id: int = OWNER) -> str:
    return sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id})}, TOKEN)


# ---- pieces -------------------------------------------------------------------------------------

def test_slugs_are_short_and_safe() -> None:
    assert slugify("Habit Tracker!") == "habit-tracker" and slugify("../../etc") == "etc" and slugify("") == "app"


@pytest.mark.parametrize("text,ok", [
    (f"Here you go:\n```html\n{DOC}\n```\nEnjoy", True),
    (DOC, True),
    ("```html\n<!doctype html><html><head>", False),          # cut off: no </html>
    ("Sorry, I can't.", False),
])
def test_only_a_complete_document_counts(text: str, ok: bool) -> None:
    assert (extract_html(text) is not None) is ok


def test_served_apps_get_telegram_and_window_buddy_first() -> None:
    out = serve_app_html(DOC)
    assert out.index('src="/buddy.js"') < out.index("<title>") and "telegram-web-app.js" in out


# ---- the store ----------------------------------------------------------------------------------

def test_store_saves_lists_finds_and_keeps_the_previous_version(tmp_path: Path) -> None:
    store = AppStore(tmp_path)
    a = store.save(DOC, "a habit tracker")
    assert a.slug == "habit-tracker" and a.title == "Habit Tracker"
    b = store.save(DOC, "another")                               # same title: a second app, not an overwrite
    assert b.slug == "habit-tracker-2"
    edited = store.save(DOC.replace("hi", "v2"), "add streaks", slug=a.slug)
    assert edited.slug == a.slug and edited.created == a.created
    assert "v2" in store.html(a.slug) and (tmp_path / a.slug / "index.prev.html").exists()
    assert store.find("my habit tracker").slug.startswith("habit-tracker") and store.find("zebra") is None
    assert {x.slug for x in store.list()} == {"habit-tracker", "habit-tracker-2"}


def test_data_round_trips_and_is_bounded(tmp_path: Path) -> None:
    store = AppStore(tmp_path)
    slug = store.save(DOC, "x").slug
    assert store.load_data(slug) is None
    store.save_data(slug, {"habits": ["run"], "done": {"2026-09-24": ["run"]}})
    assert store.load_data(slug)["habits"] == ["run"]
    with pytest.raises(ValueError):
        store.save_data(slug, "x" * 1_100_000)
    with pytest.raises(KeyError):
        store.load_data("no-such-app")
    with pytest.raises(KeyError):
        store.load_data("../secrets")


# ---- the maker ----------------------------------------------------------------------------------

def fake_generate(text: str = f"```html\n{DOC}\n```", stop: str = "end_turn"):
    prompts: list[str] = []

    async def generate(prompt, progress):
        prompts.append(prompt)
        progress(len(text))
        return text, {"input_tokens": 2000, "output_tokens": 10000}, stop

    return generate, prompts


def maker_for(tmp_path: Path, generate, cap: float = 5.0):
    ledger = SpendLedger(tmp_path / "spend.json", cap)
    store = AppStore(tmp_path / "apps")
    return AppMaker(store, generate, cost=lambda u: cost_usd("claude-opus-5-5", u), spend=ledger.add,
                    left=ledger.left), ledger, store


def test_making_an_app_saves_it_and_bills_it(tmp_path: Path) -> None:
    gen, prompts = fake_generate()
    maker, ledger, store = maker_for(tmp_path, gen)
    made = asyncio.run(maker.make("a habit tracker with streaks"))
    assert made.ok and made.app.slug == "habit-tracker" and store.html("habit-tracker")
    assert "habit tracker with streaks" in prompts[0]
    assert ledger.spent() == pytest.approx(cost_usd("claude-opus-5-5", {"input_tokens": 2000, "output_tokens": 10000}))


def test_changing_an_app_sends_the_current_file_and_keeps_its_name(tmp_path: Path) -> None:
    gen, prompts = fake_generate()
    maker, _, store = maker_for(tmp_path, gen)
    asyncio.run(maker.make("habit tracker"))
    made = asyncio.run(maker.edit("the habit tracker", "add a weekly chart"))
    assert made.ok and made.app.slug == "habit-tracker" and DOC in prompts[1] and "weekly chart" in prompts[1]
    assert not asyncio.run(maker.edit("zebra planner", "x")).ok


@pytest.mark.parametrize("text,stop,why", [("```html\n<html><body>", "max_tokens", "too long"),
                                           ("no.", "refusal", "declined"), ("just words", "end_turn", "complete")])
def test_a_bad_build_saves_nothing_and_says_why(tmp_path: Path, text: str, stop: str, why: str) -> None:
    maker, ledger, store = maker_for(tmp_path, fake_generate(text, stop)[0])
    made = asyncio.run(maker.make("anything"))
    assert not made.ok and why in made.reason and store.list() == [] and ledger.spent() > 0


def test_no_build_past_the_cap(tmp_path: Path) -> None:
    gen, prompts = fake_generate()
    maker, ledger, _ = maker_for(tmp_path, gen, cap=0.01)
    ledger.add(1.0)
    made = asyncio.run(maker.make("anything"))
    assert not made.ok and "budget" in made.reason and prompts == []


# ---- the server's app routes --------------------------------------------------------------------

def server_for(tmp_path: Path, generate=None):
    cfg = MiniAppConfig(enabled=True, token=TOKEN, owner_ids=frozenset({OWNER}), ledger_path=tmp_path / "spend.json")
    maker, ledger, store = maker_for(tmp_path, generate or fake_generate()[0])

    async def no_chat(history, answer):
        yield ""

    return MiniAppServer(cfg, no_chat, ledger, page=b"home", store=store, maker=maker), store


async def call(port: int, method: str, path: str, body: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        return await (c.get(path) if method == "GET" else c.post(path, json=body or {}))


def test_the_app_routes_end_to_end(tmp_path: Path) -> None:
    async def go():
        srv, store = server_for(tmp_path)
        port = await srv.start()
        try:
            made = await call(port, "POST", "/api/make", {"initData": signed(), "request": "habit tracker"})
            listed = await call(port, "POST", "/api/apps", {"initData": signed()})
            page = await call(port, "GET", "/apps/habit-tracker/")
            js = await call(port, "GET", "/buddy.js")
            saved = await call(port, "POST", "/api/apps/habit-tracker/save",
                               {"initData": signed(), "data": {"habits": ["read"]}})
            loaded = await call(port, "POST", "/api/apps/habit-tracker/load", {"initData": signed()})
            missing = await call(port, "GET", "/apps/nope/")
            return made, listed, page, js, saved, loaded, missing
        finally:
            await srv.close()

    made, listed, page, js, saved, loaded, missing = asyncio.run(go())
    events = [json.loads(c[6:]) for c in made.text.split("\n\n") if c.startswith("data: ")]
    assert events[-1]["done"] and events[-1]["url"] == "/apps/habit-tracker/"
    assert [a["slug"] for a in listed.json()["apps"]] == ["habit-tracker"]
    assert page.status_code == 200 and "/buddy.js" in page.text and "window.buddy" in js.text
    assert saved.json()["ok"] and loaded.json()["data"] == {"habits": ["read"]}
    assert missing.status_code == 404


def test_the_app_api_is_owner_only(tmp_path: Path) -> None:
    gen, prompts = fake_generate()

    async def go():
        srv, store = server_for(tmp_path, gen)
        store.save(DOC, "x")
        port = await srv.start()
        try:
            return [await call(port, "POST", p, {"initData": d, "request": "x", "data": 1})
                    for p in ("/api/make", "/api/apps", "/api/apps/habit-tracker/load", "/api/apps/habit-tracker/save")
                    for d in ("", signed(999))], store.load_data("habit-tracker")
        finally:
            await srv.close()

    responses, data = asyncio.run(go())
    assert all(r.status_code == 403 for r in responses) and prompts == [] and data is None


# ---- the chat door ------------------------------------------------------------------------------

class FakeMiniApp:
    def __init__(self, maker, store, url="https://quiet-owl.trycloudflare.com") -> None:
        self.maker, self.store, self.url = maker, store, url

    def app_url(self, slug: str) -> str:
        return f"{self.url}/apps/{slug}/" if self.url else ""


def test_the_chat_door_builds_in_the_background_and_sends_an_open_button(tmp_path: Path) -> None:
    from cc_buddy_bridge.apps_maker import ChatMaker

    maker, _, store = maker_for(tmp_path, fake_generate()[0])
    sent: list[tuple[int, str, list]] = []
    said: list[str] = []

    async def send(chat, text, buttons):
        sent.append((chat, text, buttons))

    async def say(chat, text):
        said.append(text)

    async def go():
        spawned: list = []
        door = ChatMaker(FakeMiniApp(maker, store), lambda coro, name: spawned.append(asyncio.ensure_future(coro)))
        first = await door.handle("make_app", {"request": "habit tracker"}, 7, send, say)
        await asyncio.gather(*spawned)
        unknown = await door.handle("change_app", {"app": "zebra", "change": "x"}, 7, send, say)
        listed = await door.handle("list_apps", {}, 7, send, say)
        return first, unknown, listed

    first, unknown, listed = asyncio.run(go())
    assert first["ok"] and sent[0] == (7, "Habit Tracker is ready.",
                                       [("Open Habit Tracker", "https://quiet-owl.trycloudflare.com/apps/habit-tracker/")])
    assert not unknown["ok"] and "Habit Tracker" in unknown["reason"]
    assert listed["apps"] == ["Habit Tracker"] and sent[-1][2][0][0] == "Habit Tracker" and said == []


def test_the_chat_door_offers_nothing_while_the_tunnel_is_down(tmp_path: Path) -> None:
    from cc_buddy_bridge.apps_maker import MAKER_TOOL_NAMES, ChatMaker

    maker, _, store = maker_for(tmp_path, fake_generate()[0])
    down = ChatMaker(FakeMiniApp(maker, store, url=""), lambda coro, name: None)
    up = ChatMaker(FakeMiniApp(maker, store), lambda coro, name: None)
    assert down.tools() == [] and {t["name"] for t in up.tools()} == MAKER_TOOL_NAMES
