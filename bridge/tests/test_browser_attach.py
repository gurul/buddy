"""Attach mode: the lane drives the owner's running Chrome through DevToolsActivePort, in its own tab only."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from cc_buddy_bridge import browser_lane as bl
from cc_buddy_bridge.browser_lane import (
    AttachError,
    BrowserLane,
    BrowserLaneConfig,
    configured,
    devtools_endpoint,
)


def test_endpoint_is_read_like_chrome_devtools_mcp(tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("64687\n/devtools/browser/600e426b-7753\n")
    assert devtools_endpoint(tmp_path) == "ws://127.0.0.1:64687/devtools/browser/600e426b-7753"


@pytest.mark.parametrize("content", ["", "64687\n", "abc\n/devtools/browser/x\n", "70000\n/devtools/browser/x\n",
                                     "64687\n/json/version\n"])
def test_a_missing_or_malformed_file_means_no_endpoint(tmp_path: Path, content: str) -> None:
    assert devtools_endpoint(tmp_path) is None                               # no file at all
    (tmp_path / "DevToolsActivePort").write_text(content)
    assert devtools_endpoint(tmp_path) is None


def test_attach_is_off_unless_asked(tmp_path: Path) -> None:
    assert configured({}).attach is False
    cfg = configured({"CC_BUDDY_BROWSER_ATTACH": "1", "CC_BUDDY_CHROME_DIR": str(tmp_path)})
    assert cfg.attach is True and cfg.chrome_dir == tmp_path


def test_attach_without_remote_debugging_says_how_to_switch_it_on(tmp_path: Path) -> None:
    lane = BrowserLane(BrowserLaneConfig(enabled=True, attach=True, chrome_dir=tmp_path))
    with pytest.raises(AttachError, match="chrome://inspect/#remote-debugging"):
        lane._ensure()


def test_closing_in_attach_mode_closes_only_buddys_tab_never_the_owners_browser() -> None:
    calls: list[str] = []
    lane = BrowserLane(BrowserLaneConfig(enabled=True, attach=True))
    lane._browser = SimpleNamespace(close=lambda: calls.append("browser.close"))
    lane._context = SimpleNamespace(close=lambda: calls.append("context.close"))
    lane._pw = SimpleNamespace(stop=lambda: calls.append("playwright.stop"))
    lane._page = SimpleNamespace(page=SimpleNamespace(close=lambda: calls.append("buddy tab.close")))
    lane._close()
    assert calls == ["buddy tab.close", "playwright.stop"]               # never the context, never the browser
    assert lane._browser is lane._context is lane._pw is lane._page is None


def test_enter_is_a_key() -> None:
    assert bl.KEYS["enter"] == bl.KEYS["return"] == "Enter"


# ---- live: a stand-in Chrome, never the owner's -----------------------------------------------------

def _standin_chrome() -> str:
    root = Path.home() / "Library" / "Caches" / "ms-playwright"
    found = sorted(root.glob("chromium-*/chrome-mac*/*.app/Contents/MacOS/*"))
    return str(found[-1]) if found else ""


@pytest.mark.live
def test_live_attach_to_a_standin_chrome_works_in_its_own_tab_and_leaves_the_owners(tmp_path: Path) -> None:
    exe = _standin_chrome()
    if not exe:
        pytest.skip("Playwright's Chromium is not installed")
    page = tmp_path / "owner.html"
    page.write_text("<title>owner</title><input aria-label='Search here' type='search'>")
    proc = subprocess.Popen([exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={tmp_path / 'ud'}",
                             "--no-first-run", page.as_uri()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = tmp_path / "ud" / "DevToolsActivePort"
        for _ in range(60):
            if port_file.exists() and devtools_endpoint(tmp_path / "ud"):
                break
            time.sleep(0.1)
        port = port_file.read_text().split()[0]

        def tabs() -> list[str]:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as r:
                return sorted(t["url"] for t in json.load(r) if t["type"] == "page")

        owners = tabs()

        async def go() -> str:
            lane = BrowserLane(BrowserLaneConfig(enabled=True, attach=True, chrome_dir=tmp_path / "ud"))
            try:
                await lane.open_url(page.as_uri())
                assert len(tabs()) == len(owners) + 1                       # buddy's own new tab

                def type_into() -> str:
                    p = lane._ensure()
                    box = next(c for c in p.snapshot().elements if c.editable)
                    return p.focus_and_type(box, "hello")
                return await lane._run(type_into)
            finally:
                await lane.close()

        said = asyncio.run(go())
        assert said.startswith("typed 5 characters")
        time.sleep(0.3)
        assert tabs() == owners                                              # the owner's tabs, untouched
        assert proc.poll() is None                                           # and their browser still running
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_a_protected_file_falls_back_to_the_port_and_chromes_own_prompt(tmp_path: Path, monkeypatch) -> None:
    """macOS app-data protection refuses other apps a read of Chrome's folder: the endpoint is then the debugging
    port (Chrome asks the owner "Allow remote debugging?" per connection), and only while it is listening."""
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/x\n")

    def refused(self, *a, **k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(Path, "read_text", refused)
    monkeypatch.setattr(bl, "_listening", lambda port: port == 9333)
    assert devtools_endpoint(tmp_path, 9333) == "ws://127.0.0.1:9333/devtools/browser"
    assert devtools_endpoint(tmp_path, 9222) is None                           # nothing listening: off


def test_the_debug_port_is_configurable() -> None:
    assert configured({}).debug_port == 9222
    assert configured({"CC_BUDDY_CHROME_DEBUG_PORT": "9333"}).debug_port == 9333
    assert configured({"CC_BUDDY_CHROME_DEBUG_PORT": "nope"}).debug_port == 9222


class _GrowingPage:
    """A web app that says Loading… first, then fills in."""

    def __init__(self, texts: list[str], url: str = "https://mail.google.com/mail/u/0/#inbox") -> None:
        self.texts, self.url = list(texts), url

    def inner_text(self, selector: str, timeout: int = 0) -> str:
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]

    def title(self) -> str:
        return "Inbox"


def _lane_on(page: _GrowingPage, monkeypatch) -> BrowserLane:
    lane = BrowserLane(BrowserLaneConfig(enabled=True))
    monkeypatch.setattr(lane, "_ensure", lambda: SimpleNamespace(page=page))
    monkeypatch.setattr(bl, "READ_POLL_SECS", 0.0)
    return lane


def test_page_text_waits_for_a_loading_app_to_fill_in(monkeypatch) -> None:
    full = "Inbox " + "message " * 80
    lane = _lane_on(_GrowingPage(["Loading…", "Loading… Gmail", full, full]), monkeypatch)
    got = lane._page_text()
    assert got["text"] == " ".join(full.split()) and got["signed_out"] is False


def test_page_text_notices_a_sign_in_redirect(monkeypatch) -> None:
    lane = _lane_on(_GrowingPage(["Sign in to GitHub " * 30],
                                 url="https://github.com/login?return_to=https%3A%2F%2Fgithub.com%2Fnotifications"),
                    monkeypatch)
    assert lane._page_text()["signed_out"] is True
    for url in ("https://accounts.google.com/v3/signin/identifier?x=1", "https://www.amazon.com/ap/signin?x=1"):
        assert bl.SIGN_IN.search(url)
    for url in ("https://mail.google.com/mail/u/0/#inbox", "https://news.ycombinator.com/", "https://github.com/notifications"):
        assert not bl.SIGN_IN.search(url)


# ---- which Chrome profile ----------------------------------------------------------------------------

LIST_ACCOUNTS = ('["gaia.l.a.r",[["gaia.l.a",1,"Guru","owner@work.example","https://x/photo.jpg",1,1,0,null,1,"1"],'
                 '["gaia.l.a",1,"Guru","OWNER@gmail.com","https://x/p2.jpg",0,0,0,null,1,"2"]]]')
PROFILES = {"owner@gmail.com": "personal-ctx", "owner@work.example": "work-ctx", "student@school.example": "school-ctx"}


def test_accounts_are_read_primary_first_and_deduplicated() -> None:
    assert bl.accounts_in(LIST_ACCOUNTS) == ["owner@work.example", "owner@gmail.com"]
    assert bl.accounts_in("") == [] and bl.accounts_in("[]") == []


@pytest.mark.parametrize("request_text,expected", [
    ("check my work inbox", "owner@work.example"),
    ("open canvas on my school account", "student@school.example"),
    ("how many unread in gmail", "owner@gmail.com"),
    ("use student@school.example and open canvas", "student@school.example"),
    ("what's the weather", ""),                                  # nothing named, no default: the first profile
])
def test_a_request_names_its_profile(request_text: str, expected: str) -> None:
    assert bl.profile_for(request_text, PROFILES) == expected


def test_the_configured_default_applies_only_when_it_is_open() -> None:
    assert bl.profile_for("what's the weather", PROFILES, "owner@work.example") == "owner@work.example"
    assert bl.profile_for("what's the weather", PROFILES, "someone@else.com") == ""
    assert configured({"CC_BUDDY_CHROME_PROFILE": " Owner@Work.Example "}).chrome_profile == "owner@work.example"


class _Ctx:
    def __init__(self, body: str) -> None:
        self.body = body
        self.request = SimpleNamespace(get=lambda url, timeout=0: SimpleNamespace(text=lambda: self.body))


def test_profiles_map_primary_accounts_to_contexts_and_a_missing_one_is_refused() -> None:
    work, personal = _Ctx(LIST_ACCOUNTS), _Ctx('[[["x",1,"G","owner@gmail.com"]]]')
    lane = BrowserLane(BrowserLaneConfig(enabled=True, attach=True))
    lane._browser = SimpleNamespace(contexts=[work, personal])
    assert lane._profiles() == {"owner@work.example": work, "owner@gmail.com": personal}
    assert lane._pick_profile("owner@gmail.com") is personal
    assert lane._pick_profile("") is work
    with pytest.raises(AttachError, match="no open Chrome window for student@school.example"):
        lane._pick_profile("student@school.example")


def test_a_lost_tab_in_the_owners_chrome_is_replaced_by_a_new_one_never_theirs() -> None:
    """Regression: once attached, a dead buddy tab fell through to pages[0] — the owner's own first tab."""
    opened: list[str] = []

    class Page:
        def __init__(self, name: str, alive: bool = True) -> None:
            self.name, self.alive = name, alive

        def title(self) -> str:
            if not self.alive:
                raise RuntimeError("closed")
            return self.name

        def bring_to_front(self) -> None:
            pass

    owners = [Page("owner's inbox"), Page("owner's bank")]
    ctx = SimpleNamespace(pages=owners, new_page=lambda: opened.append("new") or Page("buddy's new tab"))
    lane = BrowserLane(BrowserLaneConfig(enabled=True, attach=True))
    lane._browser, lane._context = SimpleNamespace(), ctx
    lane._page = SimpleNamespace(page=Page("buddy's old tab", alive=False))
    got = lane._ensure()
    assert got.page.name == "buddy's new tab" and opened == ["new"]


def test_chrome_with_no_window_gets_one_before_connecting(monkeypatch) -> None:
    calls: list[str] = []
    counts = iter(["0", "1"])

    def run(argv, capture_output=True, text=True, timeout=0):
        script = argv[-1]
        calls.append("make" if "make new window" in script else "count")
        return SimpleNamespace(stdout=next(counts) if "count windows" in script else "")

    monkeypatch.setattr(bl.time, "sleep", lambda s: None)
    assert bl.ensure_chrome_window(run) is True and calls == ["count", "make", "count"]


def test_chrome_with_a_window_is_left_alone() -> None:
    calls: list[str] = []

    def run(argv, capture_output=True, text=True, timeout=0):
        calls.append(argv[-1])
        return SimpleNamespace(stdout="2")

    assert bl.ensure_chrome_window(run) is True and len(calls) == 1 and "make new window" not in calls[0]
