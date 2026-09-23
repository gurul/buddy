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
