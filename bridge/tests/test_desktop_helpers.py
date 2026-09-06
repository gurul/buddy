"""desktop_helpers.py with fakes for every backend: app/URL opening, Vision box
→ point mapping, find/click/wait, change detection, clipboard typing, zoom,
the context and [after] lines, the 30 s wait cap and the install() wiring."""

from __future__ import annotations

import subprocess
from datetime import datetime

import pytest
from PIL import Image

from cc_buddy_bridge import desktop_helpers as dh
from cc_buddy_bridge.desktop_helpers import CHANGE_THRESHOLD, HELPER_NAMES, Frame, Helpers, changed_fraction

# ---- fakes -------------------------------------------------------------------------


class FakeAutoGUI:
    def __init__(self, points=(100, 50)) -> None:
        self._points = points
        self.clicks: list[tuple] = []
        self.hotkeys: list[tuple] = []
        self.presses: list[str] = []

    def size(self):
        return self._points

    def screenshot(self, **kwargs):
        return Image.new("RGB", (self._points[0] * 2, self._points[1] * 2), (10, 20, 30))

    def click(self, x, y, clicks=1):
        self.clicks.append((x, y) if clicks == 1 else (x, y, clicks))

    def hotkey(self, *keys):
        self.hotkeys.append(tuple(keys))

    def press(self, key):
        self.presses.append(key)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t = round(self.t + s, 6)


class FakeClipboard:
    def __init__(self, initial: str = "") -> None:
        self.value = initial
        self.history: list[str] = []

    def copy(self, text: str) -> None:
        self.value = text
        self.history.append(text)

    def paste(self) -> str:
        return self.value


class Runner:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.argv: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.argv.append(list(argv))
        return subprocess.CompletedProcess(argv, self.rc)


def scripted(values: list, cycle: bool = False):
    """A zero-arg fake that serves `values` in order; the last repeats (or the list cycles)."""
    calls: list = []

    def fn(*_a):
        i = len(calls)
        calls.append(i)
        if cycle:
            return values[i % len(values)]
        return values[min(i, len(values) - 1)]

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def front(app: str, title: str = "", bundle: str = "") -> dict:
    return {"app": app, "bundle": bundle, "title": title, "pid": 1}


def img(size=(200, 100), color=(10, 20, 30)):
    return Image.new("RGB", size, color)


def with_block(base, frac: float, size=(200, 100)):
    """A copy of `base` with a block covering `frac` of the area painted white."""
    out = base.copy()
    w, h = size
    bw = max(1, round(w * frac))
    out.paste((255, 255, 255), (0, 0, bw, h))
    return out


def captures(images: list, cycle: bool = False):
    frames = scripted([Frame.from_pil(i) for i in images], cycle=cycle)
    return frames


class Bench:
    """One Helpers with every backend faked, plus the text and images it produced."""

    def __init__(self, *, gui=None, frames=None, ocr=None, fronts=None, rc=0, clipboard=None) -> None:
        self.gui = gui or FakeAutoGUI()
        self.clock = Clock()
        self.run = Runner(rc)
        self.clipboard = clipboard or FakeClipboard("old")
        self.capture = captures(frames or [img()])
        self.ocr = ocr or scripted([[]])
        self.fronts = fronts or scripted([front("Warp", "zsh")])
        self.logs: list[str] = []
        self.images: list = []
        self.h = Helpers(self.gui, capture=self.capture, ocr=self.ocr, frontmost_fn=self.fronts, run=self.run,
                         clipboard=self.clipboard, clock=self.clock.now, sleep=self.clock.sleep,
                         now=lambda: datetime(2026, 9, 6, 14, 2))
        self.h.bind(self.logs.append, self.images.append)


def ocr_line(text: str, bx=0.1, by=0.8, bw=0.2, bh=0.1, conf=0.9) -> dict:
    return {"text": text, "bx": bx, "by": by, "bw": bw, "bh": bh, "conf": conf}


# ---- open_app / open_url -----------------------------------------------------------


def test_open_app_runs_open_and_waits_for_frontmost() -> None:
    b = Bench(fronts=scripted([front("Warp"), front("Warp"), front("Spotify")]))
    result = b.h.open_app("Spotify")
    assert b.run.argv == [["open", "-a", "Spotify"]]
    assert "opened Spotify" in result and "after 0.2 s" in result
    assert b.clock.sleeps[:2] == [0.1, 0.1] and b.clock.sleeps[2] == 0.25   # two polls, then the settle
    assert b.logs == [result]


def test_open_app_reports_when_not_frontmost_and_rejects_bad_names() -> None:
    b = Bench(fronts=scripted([front("Warp")]))
    assert b.h.open_app("Spotify", wait=1.0) == "opened Spotify but Warp is still frontmost after 1.0 s"
    bad = Bench(rc=1)
    with pytest.raises(RuntimeError, match="Spotifyy"):
        bad.h.open_app("Spotifyy")
    for name in ("../x", "-a"):
        with pytest.raises(ValueError):
            b.h.open_app(name)
    assert b.run.argv == [["open", "-a", "Spotify"]]          # the bad names never reached `open`


def test_open_url_argv_scheme_and_wait() -> None:
    b = Bench(fronts=scripted([front("Warp", "zsh"), front("Warp", "zsh"), front("Safari", "Google", "com.apple.Safari")]))
    assert b.h.open_url("https://x") == "opened https://x in Safari"
    assert b.run.argv == [["open", "https://x"]]
    b2 = Bench(fronts=scripted([front("Warp", "zsh"), front("Safari", "x", "com.apple.Safari")]))
    b2.h.open_url("https://x", app="Safari")
    assert b2.run.argv == [["open", "-a", "Safari", "https://x"]]
    # a non-browser whose title changed ends the wait too (e.g. a mailto: handler)
    b3 = Bench(fronts=scripted([front("Mail", "Inbox"), front("Mail", "Inbox"), front("Mail", "New Message")]))
    assert b3.h.open_url("mailto:a@b.c") == "opened mailto:a@b.c in Mail"
    for url in ("file:///etc/passwd", "javascript:alert(1)"):
        with pytest.raises(ValueError, match="only http, https and mailto"):
            b.h.open_url(url)
    assert b.run.argv == [["open", "https://x"]]


# ---- screen_text / find_text / click_text ------------------------------------------


def test_screen_text_maps_vision_boxes_to_points_sorts_and_caps() -> None:
    b = Bench(ocr=scripted([[ocr_line("a", 0.1, 0.8, 0.2, 0.1)]]))
    [item] = b.h.screen_text()
    assert (item["x"], item["y"], item["w"], item["h"]) == (10, 5, 20, 5)
    assert b.logs == ["screen_text: 1 lines"]
    # the real Warp menu-bar box from the bench (bottom-left origin) lands at the top-left in points
    real = Bench(gui=FakeAutoGUI(points=(1512, 982)), ocr=scripted([[ocr_line("Warp", 0.035, 0.975, 0.025, 0.013)]]))
    [warp] = real.h.screen_text()
    assert abs(warp["x"] - 53) <= 1 and abs(warp["y"] - 11) <= 1
    # top-to-bottom, then left-to-right
    lines = [ocr_line("bottom", 0.5, 0.1, 0.1, 0.05), ocr_line("top-right", 0.6, 0.9, 0.1, 0.05),
             ocr_line("top-left", 0.1, 0.9, 0.1, 0.05)]
    b = Bench(ocr=scripted([lines]))
    assert [i["text"] for i in b.h.screen_text()] == ["top-left", "top-right", "bottom"]
    # region filter by centre
    assert [i["text"] for i in b.h.screen_text(region=(0, 40, 100, 10))] == ["bottom"]
    # cap: 300 lines → 120 kept + a tail
    many = [ocr_line(f"l{i}", 0.0, 1.0 - (i + 1) * 0.003, 0.05, 0.002) for i in range(300)]
    b = Bench(ocr=scripted([many]))
    out = b.h.screen_text()
    assert len(out) == 121 and "180 more lines" in out[-1]["text"]


def test_find_text_whole_line_then_substring_casefold() -> None:
    lines = [ocr_line("Search results", 0.1, 0.5, 0.4, 0.1), ocr_line("Search", 0.1, 0.8, 0.2, 0.1)]
    b = Bench(ocr=scripted([lines]))
    hit = b.h.find_text("search")
    assert hit["text"] == "Search" and (hit["cx"], hit["cy"]) == (20, 7)
    assert b.h.find_text("result")["text"] == "Search results"
    assert b.h.find_text("nope") is None
    assert b.logs[0] == "found 'search' at (20,7)" and b.logs[-1] == "'nope' is not on screen"


def test_click_text_clicks_centre_and_raises_when_absent() -> None:
    b = Bench(ocr=scripted([[ocr_line("Search", 0.1, 0.8, 0.2, 0.1)]]))
    assert b.h.click_text("Search") == "clicked 'Search' at (20,7)"
    assert b.gui.clicks == [(20, 7)]
    assert b.h.acted is True
    b = Bench(ocr=scripted([[]]))
    with pytest.raises(LookupError, match="'Gone' is not on screen"):
        b.h.click_text("Gone", timeout=3.0)
    assert b.clock.t == pytest.approx(3.0) and b.gui.clicks == []


# ---- wait_for / wait_settled --------------------------------------------------------


def test_wait_for_polls_fast_then_confirms_accurate_and_supports_gone() -> None:
    levels: list[str] = []
    script = scripted([[], [], [ocr_line("Warp File", 0.0, 0.9, 0.2, 0.05)], [ocr_line("Warp", 0.0, 0.9, 0.1, 0.05)]])

    def ocr(frame, level):
        levels.append(level)
        return script()

    b = Bench(ocr=ocr)
    item = b.h.wait_for("Warp")
    assert item["text"] == "Warp" and item["w"] == 10
    assert levels == ["fast", "fast", "fast", "accurate"]
    assert b.clock.sleeps == [0.3, 0.3]
    assert b.logs == ["'Warp' appeared after 0.6 s"]
    gone = Bench(ocr=scripted([[ocr_line("Saving")], [ocr_line("Saving")], []]))
    assert gone.h.wait_for("Saving", gone=True) is True
    late = Bench(ocr=scripted([[]]))
    assert late.h.wait_for("Inbox", timeout=6.0) is None
    assert late.logs == ["'Inbox' not seen in 6.0 s"]


def test_wait_settled_and_changed_fraction() -> None:
    a = img()
    b_ = with_block(a, 0.5)
    c = with_block(a, 0.55)
    bench = Bench(frames=[a, b_, c, c])
    assert bench.h.wait_settled() is True
    assert bench.clock.sleeps == [0.25, 0.25, 0.25]
    assert bench.logs == ["screen settled after 0.8 s"]
    never = Bench(frames=[a, b_], )
    never.capture = captures([a, b_], cycle=True)
    never.h._capture_fn = never.capture
    assert never.h.wait_settled(timeout=2.0) is False
    assert never.logs == ["screen still changing after 2.0 s"]
    # positive / negative control on the threshold itself
    thumb = Frame.from_pil(a).thumb()
    one_px = a.copy()
    one_px.putpixel((0, 0), (255, 255, 255))
    assert changed_fraction(thumb, Frame.from_pil(one_px).thumb()) < CHANGE_THRESHOLD
    assert changed_fraction(thumb, Frame.from_pil(with_block(a, 0.01)).thumb()) > CHANGE_THRESHOLD


# ---- type_text / zoom / observe ------------------------------------------------------


def test_type_text_pastes_and_restores_clipboard() -> None:
    b = Bench()
    line = b.h.type_text("café ☕", submit=True)
    assert b.clipboard.history == ["café ☕", "old"]
    assert b.gui.hotkeys == [("command", "v")] and b.gui.presses == ["enter"]
    assert "café" not in line and line == "typed 6 characters and pressed enter"
    assert b.h.acted is True
    assert b.clock.sleeps == [0.15]


def test_zoom_crops_physical_capture_and_logs_mapping() -> None:
    b = Bench()
    b.h.zoom(10, 10, 20, 5)
    assert b.images[0].size == (40, 10)
    assert "points (10,10,20,5)" in b.logs[0] and "x+px/2" in b.logs[0]
    big = Bench(gui=FakeAutoGUI(points=(1000, 700)), frames=[img((2000, 1400))])
    big.h.zoom(0, 0, 900, 700)
    assert big.images[0].size == (1600, 1200) and "points (0,0,800,600)" in big.logs[0]


def test_observe_context_and_after_line() -> None:
    a = img()
    b = Bench(frames=[a, a, with_block(a, 0.05)], fronts=scripted([front("Safari", "Google")]))
    assert b.h.context_line() == "frontmost: Safari — 'Google'; screen 100x50; 14:02"
    assert b.h.observe() == b.h.context_line()
    assert len(b.images) == 1 and b.images[0].size == (100, 50) and b.logs == [b.h.context_line()]
    assert b.h.after_line() == "[after] frontmost: Safari — 'Google'; screen: unchanged"
    assert b.h.after_line().endswith("screen: changed")


# ---- caps and wiring -------------------------------------------------------------------


def test_all_waits_are_capped_at_30s() -> None:
    b = Bench(ocr=scripted([[]]))
    b.h.wait_for("never", timeout=999)
    assert b.clock.t <= 30
    b = Bench(frames=[img(), with_block(img(), 0.5)])
    b.h._capture_fn = captures([img(), with_block(img(), 0.5)], cycle=True)
    b.h.wait_settled(timeout=999)
    assert b.clock.t <= 30
    b = Bench(fronts=scripted([front("Warp")]))
    b.h.open_app("Spotify", wait=999)
    polls_end = b.clock.t - 0.25 * (b.clock.sleeps.count(0.25))      # minus the settle's intervals
    assert polls_end <= 30 and "after 30.0 s" in b.logs[0]


def test_install_binds_helpers_and_tracks_actions() -> None:
    b = Bench()
    ns: dict = {"pyautogui": b.gui}
    b.h.install(ns)
    assert all(callable(ns[name]) for name in HELPER_NAMES)
    assert b.h.acted is False
    ns["pyautogui"].hotkey("command", "l")
    assert b.h.acted is True and b.gui.hotkeys == [("command", "l")]
    b.h.begin()
    assert b.h.acted is False
    ns["pyautogui"].screenshot()
    b.h.screenshot()
    assert b.h.acted is False
    b.h.install(ns)                                    # idempotent: no double wrapping
    ns["pyautogui"].click(1, 2)
    assert b.gui.clicks == [(1, 2)]
    assert set(dh.ACTION_NAMES) >= {"click", "hotkey", "press", "write"}


# ---- click_element (handyman-style snap and verify) -----------------------------------------


def _elements(table: dict) -> callable:
    def element_at(x, y):
        return table.get((x, y))
    return element_at


def test_click_element_snaps_to_the_control_centre_when_it_matches() -> None:
    b = Bench()
    b.h._element_at = _elements({(30, 20): {"role": "AXButton", "title": "Sign in", "frame": (20, 10, 40, 20), "pressable": True}})
    line = b.h.click_element(30, 20, "the Sign in button")
    assert b.gui.clicks == [(40, 20)] and line == "clicked AXButton 'Sign in' at (40,20)" and b.h.acted


def test_click_element_refuses_a_mismatch_and_says_what_is_there() -> None:
    b = Bench()
    b.h._element_at = _elements({(30, 20): {"role": "AXLink", "title": "Weather", "frame": (20, 10, 40, 20), "pressable": True}})
    line = b.h.click_element(30, 20, "Sign in button")
    assert b.gui.clicks == [] and line.startswith("did not click: under (30,20) is AXLink 'Weather', not 'Sign in button'")
    assert not b.h.acted


def test_click_element_without_an_element_clicks_the_raw_point() -> None:
    b = Bench()
    b.h._element_at = _elements({})
    line = b.h.click_element(7, 8, "anything")
    assert b.gui.clicks == [(7, 8)] and "no accessibility element" in line


def test_click_element_matches_untitled_fields_by_role_and_partial_words() -> None:
    b = Bench()
    b.h._element_at = _elements({(1, 1): {"role": "AXTextField", "title": "", "frame": None, "pressable": True},
                                 (2, 2): {"role": "AXButton", "title": "Compose new message", "frame": None, "pressable": True}})
    assert b.h.click_element(1, 1, "the text field").startswith("clicked AXTextField")
    assert b.h.click_element(2, 2, "compose button").startswith("clicked AXButton 'Compose new message'")


def test_raw_clicks_report_their_target_in_the_after_line() -> None:
    a = img()
    b = Bench(frames=[a, a, a], fronts=scripted([front("Safari", "Google")]))
    b.h._element_at = _elements({(5, 6): {"role": "AXLink", "title": "Weather", "frame": None, "pressable": True}})
    ns: dict = {}
    b.h.install(ns)
    b.h.begin()
    b.gui.click(5, 6)
    assert b.h.after_line().endswith("; clicked on AXLink 'Weather'")
    b.h.begin()
    assert "clicked on" not in b.h.after_line()
