"""The paid reader (watch.firecrawl) and its rung on the watcher's ladder: "" -> tls -> browser -> firecrawl -> search.

firecrawl() is tested against a fake http_request, so no credit is spent: the request it sends, and which failures
are the page's (they move a watch down the ladder) and which are Firecrawl's (they never do). The watcher tests
lend fakes, as test_watch does.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_watch import JSON_LD_EVENT, args, make_watcher

from cc_buddy_bridge import spend, watch
from cc_buddy_bridge.watch import FetchError


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """firecrawl()'s requests, answered by ``sent.reply`` (a payload, or a FetchError to raise)."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(watch, "check_url", lambda url, **k: "")

    def fake(url: str, *, data: bytes = b"", headers: Any = None, timeout: float = 0) -> tuple[int, str]:
        calls.append({"url": url, "body": json.loads(data), "headers": headers, "timeout": timeout})
        reply = fake.reply
        if isinstance(reply, Exception):
            raise reply
        return 200, json.dumps(reply)

    fake.reply = {"success": True, "data": {"rawHtml": JSON_LD_EVENT, "metadata": {"statusCode": 200}}}
    monkeypatch.setattr(watch, "http_request", fake)
    calls.append({"fake": fake})
    return calls


def _reply(sent: list[dict[str, Any]], reply: Any) -> None:
    sent[0]["fake"].reply = reply


def test_it_asks_for_a_fresh_raw_page(sent: list[dict[str, Any]]) -> None:
    status, html = watch.firecrawl("https://shop.example/p")
    assert status == 200 and html == JSON_LD_EVENT
    call = sent[1]
    assert call["url"] == watch.FIRECRAWL_URL
    assert call["headers"]["Authorization"] == "Bearer fc-test"
    body = call["body"]
    # maxAge 0: Firecrawl answers from a cache up to two days old otherwise, and a watch is about now
    assert body["url"] == "https://shop.example/p" and body["maxAge"] == 0 and body["storeInCache"] is False
    assert body["formats"] == ["rawHtml"] and body["onlyMainContent"] is False     # JSON-LD lives in <head>
    assert body["timeout"] < call["timeout"] * 1000                               # Firecrawl gives up first


def test_the_pages_own_refusal_moves_the_ladder_firecrawls_never_does(sent: list[dict[str, Any]]) -> None:
    _reply(sent, {"success": True, "data": {"rawHtml": "<html>Access Denied</html>", "metadata": {"statusCode": 403}}})
    with pytest.raises(FetchError) as e:
        watch.firecrawl("https://shop.example/p")
    assert e.value.status == 403 and not e.value.host                 # the site's: check() moves the watch on
    for status in (401, 402, 429):
        _reply(sent, FetchError(f"the site answered HTTP {status}", status, 30.0 if status == 429 else None))
        with pytest.raises(FetchError) as e:
            watch.firecrawl("https://shop.example/p")
        assert e.value.host == watch.FIRECRAWL_HOST and e.value.status == status
    assert e.value.retry_after == 30.0                                  # the limiter holds Firecrawl, not the shop
    _reply(sent, {"success": False, "error": "boom"})
    with pytest.raises(FetchError) as e:
        watch.firecrawl("https://shop.example/p")
    assert e.value.host == watch.FIRECRAWL_HOST


def test_no_key_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(watch, "http_request", lambda *a, **k: pytest.fail("no key, no request"))
    with pytest.raises(FetchError, match="FIRECRAWL_API_KEY") as e:
        watch.firecrawl("https://shop.example/p")
    assert e.value.host == watch.FIRECRAWL_HOST


def test_a_private_link_is_never_sent_to_firecrawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(watch, "http_request", lambda *a, **k: pytest.fail("a private link is not sent"))
    with pytest.raises(FetchError, match="private or unknown"):
        watch.firecrawl("http://192.168.1.1/admin")


def test_the_switches() -> None:
    assert watch.configured({}).firecrawl is False
    on = watch.configured({"FIRECRAWL_API_KEY": "fc-x"})
    assert on.firecrawl and on.firecrawl_calls_per_day == watch.DEFAULT_FIRECRAWL_CALLS_PER_DAY
    assert on.firecrawl_usd is None                                      # never guessed: recorded unpriced
    assert watch.configured({"FIRECRAWL_API_KEY": "fc-x", "CC_BUDDY_WATCH_FIRECRAWL": "0"}).firecrawl is False
    assert watch.configured({"FIRECRAWL_API_KEY": "fc-x", "CC_BUDDY_FIRECRAWL_USD": "0.004"}).firecrawl_usd == 0.004
    assert watch.configured({"CC_BUDDY_FIRECRAWL_USD": "cheap"}).firecrawl_usd is None
    assert watch.configured({"CC_BUDDY_WATCH_FIRECRAWL_CALLS": "5"}).firecrawl_calls_per_day == 5


# ---- the watcher --------------------------------------------------------------------------------

def refused(status: int = 403) -> Any:
    def fetch(url: str, **kw: Any) -> tuple[int, str]:
        raise FetchError(f"the site answered HTTP {status}", status)
    return fetch


def crawler(pages: list[str], html: str = JSON_LD_EVENT) -> Any:
    return lambda url, **kw: pages.append(url) or (200, html)


@pytest.fixture
def spent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    monkeypatch.setattr(spend, "record", lambda *a, **k: rows.append((a, k)))
    return rows


def test_a_page_every_local_reader_is_refused_is_read_through_firecrawl(tmp_path: Path,
                                                                        spent: list[Any]) -> None:
    crawled: list[str] = []
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), tls=True, browser=True, firecrawl=True)
    w._tls = refused(403)
    w._render = lambda url, **kw: (_ for _ in ()).throw(FetchError("the page shows a bot check", 403))
    w._crawl = crawler(crawled)
    out = asyncio.run(w.add(args(target="https://shop.example/p", condition="below", value=100)))
    item = w.watches[0]
    assert out["ok"] and "85" in out["now"] and "Firecrawl" in out["note"]
    assert item.via == "firecrawl" and item.every_secs >= watch.FLOOR_SECS["firecrawl"]
    assert crawled == ["https://shop.example/p"]
    assert spent and spent[0][0][:3] == ("firecrawl", "scrape", spend.WATCH) and spent[0][0][3] is None
    # from now on straight to Firecrawl; nothing local is tried
    w._fetch = w._tls = lambda url, **kw: pytest.fail("a firecrawl page is not read locally")
    w._render = lambda url, **kw: pytest.fail("a firecrawl page is not rendered")
    asyncio.run(w.read(item))
    assert len(crawled) == 2
    assert "(through Firecrawl)" in w.listing()


def test_without_the_browser_firecrawl_comes_straight_after_the_chrome_like_read(tmp_path: Path,
                                                                                 spent: list[Any]) -> None:
    crawled: list[str] = []
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), tls=True, browser=False, firecrawl=True)
    w._tls = refused(403)
    w._crawl = crawler(crawled)
    assert asyncio.run(w.add(args(condition="below", value=100)))["ok"]
    assert w.watches[0].via == "firecrawl" and len(crawled) == 1


def test_a_page_firecrawl_is_refused_too_goes_to_search(tmp_path: Path, spent: list[Any]) -> None:
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), firecrawl=True)
    w._crawl = refused(403)
    asyncio.run(w.add(args(condition="below", value=100)))
    assert w.watches[0].via == "search"


def test_firecrawls_own_trouble_never_moves_the_watch(tmp_path: Path, spent: list[Any]) -> None:
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), firecrawl=True)

    def out_of_credits(url: str, **kw: Any) -> tuple[int, str]:
        raise FetchError("Firecrawl: the site answered HTTP 402", 402, host=watch.FIRECRAWL_HOST)

    w._crawl = out_of_credits
    asyncio.run(w.add(args(condition="below", value=100)))
    item = w.watches[0]
    assert item.via == "firecrawl" and item.errors == 1 and "402" in item.last_error


def test_the_daily_cap(tmp_path: Path, spent: list[Any]) -> None:
    crawled: list[str] = []
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), firecrawl=True, firecrawl_calls_per_day=2)
    w._crawl = crawler(crawled)
    asyncio.run(w.add(args(condition="below", value=100)))
    item = w.watches[0]
    asyncio.run(w.read(item))
    with pytest.raises(FetchError, match="Firecrawl reads are used up") as e:
        asyncio.run(w.read(item))
    assert e.value.host == watch.FIRECRAWL_HOST and len(crawled) == 2
    w._clock.t += 86400                                                  # the next day
    asyncio.run(w.read(item))
    assert len(crawled) == 3


def test_off_without_a_key(tmp_path: Path) -> None:
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), firecrawl=False)
    w._crawl = lambda url, **kw: pytest.fail("Firecrawl is off")
    asyncio.run(w.add(args(condition="below", value=100)))
    assert w.watches[0].via == "search"
    assert watch.Watcher(watch.WatchConfig(enabled=True, path=tmp_path / "x.json", firecrawl=True))._crawl is None


def test_a_firecrawl_page_moves_to_search_when_firecrawl_is_turned_off(tmp_path: Path, spent: list[Any]) -> None:
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), firecrawl=True)
    w._crawl = crawler([])
    asyncio.run(w.add(args(condition="below", value=100)))
    assert w.watches[0].via == "firecrawl"
    searched: list[Any] = []
    later = make_watcher(tmp_path / "w.json", firecrawl=False)             # the key removed, the daemon restarted
    later._crawl = lambda url, **kw: pytest.fail("Firecrawl is off")
    item = later.watches[0]

    async def search(w: Any) -> Any:
        searched.append(w.id)
        return watch.Reading(value=80.0, currency="USD", source="search")

    later._read_search = search
    assert asyncio.run(later.read(item)).value == 80.0
    assert item.via == "search" and searched == [item.id] and item.every_secs >= watch.FLOOR_SECS["search"]


def test_the_via_survives_a_restart(tmp_path: Path, spent: list[Any]) -> None:
    w = make_watcher(tmp_path / "w.json", fetch=refused(403), firecrawl=True)
    w._crawl = crawler([])
    asyncio.run(w.add(args(condition="below", value=100)))
    again = make_watcher(tmp_path / "w.json", firecrawl=True)
    assert again.watches[0].via == "firecrawl" and again.watches[0].every_secs >= watch.FLOOR_SECS["firecrawl"]
