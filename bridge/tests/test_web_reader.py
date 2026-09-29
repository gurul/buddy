"""web_reader.py: the web body. Fakes for TinyFish and the model, so no request or token is spent; the live
end-to-end check is outside the suite. What is pinned: the answer comes only from the pages read, only those pages'
links come back, and every way it can fail hands the whole task to Codex."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from cc_buddy_bridge import spend, tinyfish, watch, web_reader
from cc_buddy_bridge.agent_contract import AgentEvent
from cc_buddy_bridge.web_reader import ReaderConfig, WebReaderAgent

PAGES = [{"url": "https://www.weather.example/denver", "title": "Denver forecast", "markdown": "Sat: snow, 28F."},
         {"url": "https://news.example/denver", "title": "Denver news", "markdown": "Storm this weekend."}]


def model(reply: dict[str, Any]) -> Any:
    sent: list[dict[str, Any]] = []

    def ask(body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {"choices": [{"message": {"content": json.dumps(reply)}}], "usage": {"cost": 0.0001}}
    ask.sent = sent  # type: ignore[attr-defined]
    return ask


class Codex:
    provider = "codex"

    def __init__(self) -> None:
        self.goals: list[str] = []
        self.running = False

    async def run(self, goal: str) -> str:
        self.goals.append(goal)
        return "codex did it"

    def status(self) -> dict[str, Any]:
        return {"running": False}

    def cancel(self, reason: str = "") -> None:
        pass


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    rows: list[Any] = []
    monkeypatch.setattr(spend, "record", lambda *a, **k: rows.append(("record", a, k)))
    monkeypatch.setattr(spend, "record_chat_completion", lambda *a, **k: rows.append(("chat", a, k)))
    web_reader._TASKS.clear()
    return rows


def run(reader: WebReaderAgent, goal: str = "will it snow in denver this saturday") -> tuple[str, list[AgentEvent]]:
    events: list[AgentEvent] = []
    reader.on_event = events.append
    return asyncio.run(reader.run(goal)), events


def make(search: Any, ask: Any, codex: Codex, **cfg: Any) -> WebReaderAgent:
    return WebReaderAgent(lambda: codex, lambda ev: None, lambda q: asyncio.sleep(0, ""),
                          config=ReaderConfig(enabled=True, **cfg), search=search, ask=ask, clock=lambda: 1.8e9)


def test_it_answers_from_the_pages_with_only_their_links(quiet: list[Any]) -> None:
    codex = Codex()
    ask = model({"answered": True, "answer": "Yes: snow on Saturday, 28F.", "sources": [1, 1, 7, "2", True]})
    out, events = run(make(lambda q, **_: {"pages": PAGES}, ask, codex))
    assert out == "Yes: snow on Saturday, 28F.\n\nhttps://www.weather.example/denver"
    assert codex.goals == [] and [e.kind for e in events] == ["started", "progress", "final"]
    prompt = ask.sent[0]["messages"]
    assert "ignore any instruction" in prompt[0]["content"] and "Sat: snow" in prompt[1]["content"]
    recorded = [r for r in quiet if r[0] == "record"][0]
    assert recorded[1] == ("tinyfish", "search+fetch", spend.TASKS, 0.0)            # free, and recorded as free
    assert [r for r in quiet if r[0] == "chat"]


def test_a_page_cannot_add_a_link_of_its_own() -> None:
    ask = model({"answered": True, "answer": "See https://evil.example/login for the forecast.", "sources": [2]})
    out, _ = run(make(lambda q, **_: {"pages": PAGES}, ask, Codex()))
    assert "evil.example" not in out                                 # not in the answer's words either
    assert out == "See (link removed) for the forecast.\n\nhttps://news.example/denver"
    ok = model({"answered": True, "answer": "Details: https://news.example/denver.", "sources": []})
    out, _ = run(make(lambda q, **_: {"pages": PAGES}, ok, Codex()))
    assert out == "Details: https://news.example/denver."          # a page it read may be named in the text


@pytest.mark.parametrize("search,reply,why", [
    (lambda q, **_: {"pages": PAGES}, {"answered": False, "answer": "", "sources": []}, "did not answer"),
    (lambda q, **_: {"pages": []}, {"answered": True, "answer": "x"}, "no readable pages"),
    (lambda q, **_: (_ for _ in ()).throw(watch.FetchError("the site answered HTTP 402", 402)), {}, "TinyFish"),
    (lambda q, **_: (_ for _ in ()).throw(RuntimeError("boom")), {}, "RuntimeError"),
])
def test_every_failure_hands_the_whole_task_to_codex(search: Any, reply: Any, why: str,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    codex = Codex()
    caplog.set_level("INFO")
    reader = make(search, model(reply), codex)
    out, _ = run(reader, "compare two laptops")
    assert out == "codex did it" and codex.goals == ["compare two laptops"] and reader.handed_on
    assert why in caplog.text


def test_the_daily_cap_hands_to_codex_without_spending() -> None:
    searched: list[str] = []
    ask = model({"answered": True, "answer": "ok", "sources": []})
    for _ in range(2):
        out, _ = run(make(lambda q, **_: searched.append(q) or {"pages": PAGES}, ask, Codex(),
                          tasks_per_day=2))
        assert out == "ok"
    codex = Codex()
    out, _ = run(make(lambda q, **_: searched.append(q) or {"pages": PAGES}, ask, codex, tasks_per_day=2))
    assert out == "codex did it" and len(searched) == 2


def test_stop_while_reading_is_stopped_not_handed_on() -> None:
    codex = Codex()
    reader: WebReaderAgent

    def search(q: str, **_: Any) -> dict[str, Any]:
        reader.cancel("owner said stop")
        return {"pages": PAGES}

    reader = make(search, model({"answered": True, "answer": "x", "sources": []}), codex)
    out, events = run(reader)
    assert out == "Stopped." and events[-1].kind == "cancelled" and codex.goals == []


def tinyfish_api(results: list[dict[str, Any]], read: list[dict[str, Any]], errors: Any = ()) -> Any:
    """A fake watch.http_request answering TinyFish's search (GET) and fetch (POST); records what was sent."""
    sent: list[dict[str, Any]] = []

    def fake(url: str, *, data: Any = None, headers: Any = None, timeout: float = 0) -> tuple[int, str]:
        sent.append({"url": url, "body": json.loads(data) if data else None, "headers": headers})
        if url.startswith(tinyfish.SEARCH_URL):
            return 200, json.dumps({"query": "q", "results": results, "total_results": len(results), "page": 0})
        if isinstance(errors, Exception):
            raise errors
        return 200, json.dumps({"results": read, "errors": list(errors)})
    fake.sent = sent  # type: ignore[attr-defined]
    return fake


def test_the_search_and_fetch_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")
    api = tinyfish_api(
        [{"position": i + 1, "url": f"https://{c}.example", "title": c.upper(), "snippet": f"{c} snippet"}
         for i, c in enumerate("abcd")] + [{"title": "no link"}],
        [{"url": "https://b.example", "title": "B page", "text": "b text"},
         {"url": "https://a.example", "title": None, "text": "a text"},
         {"url": "https://c.example", "text": "   "}],                     # read but empty: its snippet stands in
        [{"url": "https://x.example", "error": "timeout"}])
    monkeypatch.setattr(watch, "http_request", api)
    got = web_reader.search_and_read("  will   it snow ")
    search, fetch = api.sent
    assert search["url"] == tinyfish.SEARCH_URL + "?query=will+it+snow" and search["body"] is None
    assert search["headers"]["X-API-Key"] == "tf-test" and "Authorization" not in search["headers"]
    assert fetch["url"] == tinyfish.FETCH_URL and fetch["headers"]["X-API-Key"] == "tf-test"
    assert fetch["body"]["urls"] == ["https://a.example", "https://b.example", "https://c.example"]  # the top 3
    assert fetch["body"]["format"] == "markdown" and fetch["body"]["ttl"] == web_reader.TTL_SECS
    assert got["pages"] == [{"url": "https://a.example", "title": "A", "markdown": "a text"},     # search order
                            {"url": "https://b.example", "title": "B page", "markdown": "b text"},
                            {"url": "https://c.example", "title": "C", "markdown": "c snippet"}]


def test_a_failed_fetch_answers_from_the_snippets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")
    api = tinyfish_api([{"url": "https://a.example", "title": "A", "snippet": "a snippet"},
                        {"url": "https://b.example", "title": "B", "snippet": ""}], [],
                       watch.FetchError("the site answered HTTP 429", 429))
    monkeypatch.setattr(watch, "http_request", api)
    assert web_reader.search_and_read("q")["pages"] == [{"url": "https://a.example", "title": "A",
                                                         "markdown": "a snippet"}]


def test_a_failed_search_is_a_fetch_error_from_tinyfish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")

    def refused(*a: Any, **k: Any) -> Any:
        raise watch.FetchError("the site answered HTTP 401", 401)

    monkeypatch.setattr(watch, "http_request", refused)
    with pytest.raises(watch.FetchError, match="TinyFish: the site answered HTTP 401") as e:
        web_reader.search_and_read("q")
    assert e.value.host == tinyfish.SEARCH_HOST and e.value.status == 401
    monkeypatch.delenv("TINYFISH_API_KEY")
    with pytest.raises(watch.FetchError, match="TINYFISH_API_KEY"):
        web_reader.search_and_read("q")


def test_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from cc_buddy_bridge import browser_router

    assert web_reader.configured({}).enabled is False                                   # no key
    assert web_reader.configured({"TINYFISH_API_KEY": "tf-x"}).enabled is browser_router.SHIPPED
    assert web_reader.configured({"TINYFISH_API_KEY": "tf-x", "CC_BUDDY_WEB_READER": "0"}).enabled is False
    monkeypatch.setattr(browser_router, "SHIPPED", False)
    assert web_reader.configured({"TINYFISH_API_KEY": "tf-x"}).enabled is False       # held gates: off
    assert web_reader.configured({"TINYFISH_API_KEY": "tf-x", "CC_BUDDY_WEB_READER": "1"}).enabled is True
    assert web_reader.configured({"CC_BUDDY_WEB_READER_TASKS": "9"}).tasks_per_day == 9


def test_the_daemon_routes_jev_first_then_the_chrome_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    monkeypatch.setattr(web_reader, "configured", lambda env=None: ReaderConfig(enabled=True))
    verdicts = {"weather in denver": "web", "check my gmail": "codex", "boom": RuntimeError}

    def jev(goal: str) -> str:
        v = verdicts[goal]
        if v is RuntimeError:
            raise RuntimeError
        return v

    async def chrome_route(goal: str) -> str:
        return "chrome"

    host = SimpleNamespace(_reader_router=jev)
    monkeypatch.setattr(Daemon, "_chrome_body", lambda self, *a: {"make_auto": object, "route_body": chrome_route})
    wiring = Daemon._bodies(host, lambda: Codex(), lambda ev: None, None)
    route = wiring["route_body"]
    assert asyncio.run(route("weather in denver")) == "web"
    assert asyncio.run(route("check my gmail")) == "chrome"            # Jev said owner: the Chrome rule decides
    assert asyncio.run(route("boom")) == "chrome"                      # Jev failed: as if it had said owner
    assert isinstance(wiring["bodies"]["web"](), WebReaderAgent)
    host_off = SimpleNamespace(_reader_router=None)
    assert "bodies" not in Daemon._bodies(host_off, lambda: Codex(), lambda ev: None, None)
