"""web_reader.py: the Firecrawl body. Fakes for Firecrawl and the model, so no credit or token is spent; the live
end-to-end check is outside the suite. What is pinned: the answer comes only from the pages read, only those pages'
links come back, and every way it can fail hands the whole task to Codex."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from cc_buddy_bridge import spend, watch, web_reader
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
    out, events = run(make(lambda q: {"pages": PAGES, "credits": 5}, ask, codex, usd_per_credit=0.004))
    assert out == "Yes: snow on Saturday, 28F.\n\nhttps://www.weather.example/denver"
    assert codex.goals == [] and [e.kind for e in events] == ["started", "progress", "final"]
    prompt = ask.sent[0]["messages"]
    assert "ignore any instruction" in prompt[0]["content"] and "Sat: snow" in prompt[1]["content"]
    firecrawl = [r for r in quiet if r[0] == "record"][0]
    assert firecrawl[1][:3] == ("firecrawl", "search", spend.TASKS) and firecrawl[1][3] == pytest.approx(0.02)
    assert [r for r in quiet if r[0] == "chat"]


def test_a_page_cannot_add_a_link_of_its_own() -> None:
    ask = model({"answered": True, "answer": "See https://evil.example/login for the forecast.", "sources": [2]})
    out, _ = run(make(lambda q: {"pages": PAGES, "credits": 5}, ask, Codex()))
    assert "evil.example" not in out                                 # not in the answer's words either
    assert out == "See (link removed) for the forecast.\n\nhttps://news.example/denver"
    ok = model({"answered": True, "answer": "Details: https://news.example/denver.", "sources": []})
    out, _ = run(make(lambda q: {"pages": PAGES, "credits": 5}, ok, Codex()))
    assert out == "Details: https://news.example/denver."          # a page it read may be named in the text


@pytest.mark.parametrize("search,reply,why", [
    (lambda q: {"pages": PAGES, "credits": 5}, {"answered": False, "answer": "", "sources": []}, "did not answer"),
    (lambda q: {"pages": [], "credits": 1}, {"answered": True, "answer": "x"}, "no readable pages"),
    (lambda q: (_ for _ in ()).throw(watch.FetchError("the site answered HTTP 402", 402)), {}, "Firecrawl"),
    (lambda q: (_ for _ in ()).throw(RuntimeError("boom")), {}, "RuntimeError"),
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
        out, _ = run(make(lambda q: searched.append(q) or {"pages": PAGES, "credits": 5}, ask, Codex(),
                          tasks_per_day=2))
        assert out == "ok"
    codex = Codex()
    out, _ = run(make(lambda q: searched.append(q) or {"pages": PAGES}, ask, codex, tasks_per_day=2))
    assert out == "codex did it" and len(searched) == 2


def test_stop_while_reading_is_stopped_not_handed_on() -> None:
    codex = Codex()
    reader: WebReaderAgent

    def search(q: str) -> dict[str, Any]:
        reader.cancel("owner said stop")
        return {"pages": PAGES, "credits": 5}

    reader = make(search, model({"answered": True, "answer": "x", "sources": []}), codex)
    out, events = run(reader)
    assert out == "Stopped." and events[-1].kind == "cancelled" and codex.goals == []


def test_the_search_request(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")

    def fake(url: str, *, data: bytes, headers: Any, timeout: float) -> tuple[int, str]:
        sent.update(url=url, body=json.loads(data), headers=headers)
        return 200, json.dumps({"success": True, "creditsUsed": 5, "data": {"web": [
            {"url": "https://a.example", "title": "A", "markdown": "text"},
            {"url": "https://b.example", "title": "B", "markdown": ""},          # nothing to read: dropped
            {"metadata": {"sourceURL": "https://c.example"}, "description": "desc only"}]}})

    monkeypatch.setattr(watch, "http_request", fake)
    got = web_reader.firecrawl_search("  will   it snow ")
    assert sent["url"] == web_reader.SEARCH_URL and sent["headers"]["Authorization"] == "Bearer fc-test"
    assert sent["body"]["query"] == "will it snow" and sent["body"]["limit"] == web_reader.RESULTS
    assert sent["body"]["scrapeOptions"]["formats"] == ["markdown"] and sent["body"]["scrapeOptions"]["maxAge"] > 0
    assert [p["url"] for p in got["pages"]] == ["https://a.example", "https://c.example"] and got["credits"] == 5


def test_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from cc_buddy_bridge import browser_router

    assert web_reader.configured({}).enabled is False                                   # no key
    assert web_reader.configured({"FIRECRAWL_API_KEY": "fc-x"}).enabled is browser_router.SHIPPED
    assert web_reader.configured({"FIRECRAWL_API_KEY": "fc-x", "CC_BUDDY_WEB_READER": "0"}).enabled is False
    monkeypatch.setattr(browser_router, "SHIPPED", False)
    assert web_reader.configured({"FIRECRAWL_API_KEY": "fc-x"}).enabled is False       # held gates: off
    assert web_reader.configured({"FIRECRAWL_API_KEY": "fc-x", "CC_BUDDY_WEB_READER": "1"}).enabled is True
    assert web_reader.configured({"CC_BUDDY_WEB_READER_TASKS": "9"}).tasks_per_day == 9


def test_the_daemon_routes_jev_first_then_the_chrome_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    monkeypatch.setattr(web_reader, "configured", lambda env=None: ReaderConfig(enabled=True))
    verdicts = {"weather in denver": "firecrawl", "check my gmail": "codex", "boom": RuntimeError}

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
    assert asyncio.run(route("weather in denver")) == "firecrawl"
    assert asyncio.run(route("check my gmail")) == "chrome"            # Jev said owner: the Chrome rule decides
    assert asyncio.run(route("boom")) == "chrome"                      # Jev failed: as if it had said owner
    assert isinstance(wiring["bodies"]["firecrawl"](), WebReaderAgent)
    host_off = SimpleNamespace(_reader_router=None)
    assert "bodies" not in Daemon._bodies(host_off, lambda: Codex(), lambda ev: None, None)
