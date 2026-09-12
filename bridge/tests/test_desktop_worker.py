"""desktop_worker.py with a fake pyautogui: exec semantics, output shaping,
Retina normalisation, error and fail-safe handling, the line protocol, the
auto-screenshot rule with helpers, the observe operation and --release."""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from types import SimpleNamespace

from PIL import Image

from cc_buddy_bridge import desktop_worker as dw
from cc_buddy_bridge.desktop_helpers import Frame, Helpers


class FailSafeException(Exception):
    pass


class FakeAutoGUI:
    FailSafeException = FailSafeException

    def __init__(self, points=(100, 50), pixels=(200, 100)) -> None:
        self._points = points
        self._pixels = pixels
        self.clicks: list[tuple[int, int]] = []
        self.hotkeys: list[tuple] = []
        self.presses: list[str] = []

    def size(self):
        return self._points

    def screenshot(self, **kwargs):
        return Image.new("RGB", self._pixels, (10, 20, 30))

    def click(self, x, y, clicks=1):
        self.clicks.append((x, y))

    def hotkey(self, *keys):
        self.hotkeys.append(tuple(keys))

    def press(self, key):
        self.presses.append(key)


def _ns(gui: FakeAutoGUI | None = None) -> dict:
    return {"__builtins__": __builtins__, "pyautogui": gui or FakeAutoGUI()}


def test_log_and_stdout_become_text_items() -> None:
    r = dw.execute("log('a', 1)\nprint('b')\nprint()", _ns())
    assert r == {"output": [{"type": "input_text", "text": "a 1"}, {"type": "input_text", "text": "b"}]}


def test_display_image_becomes_png_data_url() -> None:
    r = dw.execute("display(pyautogui.screenshot())", _ns())
    item = r["output"][0]
    assert item["type"] == "input_image" and item["detail"] == "original"
    png = base64.b64decode(item["image_url"].split(",", 1)[1])
    assert png.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(png)).size == (200, 100)     # not normalised without normalize_screenshots


def test_normalize_screenshots_resizes_to_points_and_crops_regions() -> None:
    gui = FakeAutoGUI(points=(100, 50), pixels=(200, 100))
    dw.normalize_screenshots(gui)
    assert gui.screenshot().size == (100, 50)
    assert gui.screenshot(region=(10, 10, 20, 5)).size == (20, 5)


def test_display_rejects_non_png() -> None:
    r = dw.execute("display(b'not a png')", _ns())
    assert "TypeError" in r["output"][0]["text"]


def test_globals_persist_between_calls() -> None:
    ns = _ns()
    dw.execute("x = 41", ns)
    assert dw.execute("log(x + 1)", ns)["output"] == [{"type": "input_text", "text": "42"}]


def test_exceptions_come_back_as_text_and_keep_partial_output() -> None:
    r = dw.execute("log('before')\n1/0", _ns())
    assert r["output"][0]["text"] == "before"
    assert "ZeroDivisionError" in r["output"][1]["text"]


def test_failsafe_is_a_terminal_error() -> None:
    r = dw.execute("raise pyautogui.FailSafeException()", _ns())
    assert r == {"error": {"code": "failsafe", "message": "desktop fail-safe: the mouse hit a screen corner"}}


def test_no_output_placeholder() -> None:
    assert dw.execute("pass", _ns())["output"] == [{"type": "input_text", "text": "exec_py completed with no output."}]


def test_text_limit() -> None:
    r = dw.execute("log('x' * 70000)", _ns())
    assert "size limit" in r["output"][-1]["text"]


def test_model_code_can_click() -> None:
    gui = FakeAutoGUI()
    dw.execute("pyautogui.click(3, 4)", _ns(gui))
    assert gui.clicks == [(3, 4)]


# ---- line protocol -----------------------------------------------------------------

def _serve(lines: list[str], ns: dict | None = None, monkeypatch=None) -> list[dict]:
    out: list[dict] = []
    if monkeypatch is not None:
        monkeypatch.setattr(dw, "emit", out.append)
    dw.serve(ns or _ns(), iter(ln + "\n" for ln in lines))
    return out


def test_serve_executes_and_echoes_ids(monkeypatch) -> None:
    out = _serve([json.dumps({"id": 7, "operation": "execute", "code": "log('hi')"})], monkeypatch=monkeypatch)
    assert out == [{"id": 7, "output": [{"type": "input_text", "text": "hi"}]}]


def test_serve_rejects_bad_requests(monkeypatch) -> None:
    out = _serve(["not json", json.dumps({"id": 1, "operation": "ping"}),
                  json.dumps({"id": 2, "operation": "execute", "code": "   "})], monkeypatch=monkeypatch)
    assert [o["error"]["code"] for o in out] == ["bad_json", "unsupported", "bad_code"]


def test_serve_after_failsafe_refuses_everything(monkeypatch) -> None:
    out = _serve([json.dumps({"id": 1, "operation": "execute", "code": "raise pyautogui.FailSafeException()"}),
                  json.dumps({"id": 2, "operation": "execute", "code": "log('again')"})], monkeypatch=monkeypatch)
    assert out[0]["error"]["code"] == "failsafe"
    assert out[1] == {"id": 2, "error": out[0]["error"]}


# ---- helpers: auto-screenshot, observe, --release --------------------------------------

def _helpers(gui: FakeAutoGUI) -> tuple[Helpers, dict, list[float]]:
    """A Helpers on fakes (static screen, Warp frontmost) installed into a namespace."""
    settled: list[float] = []
    frame = Frame.from_pil(Image.new("RGB", (200, 100), (10, 20, 30)))
    h = Helpers(gui, capture=lambda: frame, ocr=lambda f, level: [{"text": "Warp", "bx": 0.0, "by": 0.9,
                                                                   "bw": 0.1, "bh": 0.05, "conf": 0.9}],
                frontmost_fn=lambda: {"app": "Warp", "bundle": "dev.warp.Warp-Stable", "title": "zsh", "pid": 1},
                run=lambda *a, **k: None, clipboard=SimpleNamespace(copy=lambda t: None, paste=lambda: ""),
                clock=lambda: 0.0, sleep=lambda s: None,
                now=lambda: datetime(2026, 9, 6, 14, 2))
    original = h.settled_screenshot

    def spy(max_wait: float):
        settled.append(max_wait)
        return original(max_wait)
    h.settled_screenshot = spy      # type: ignore[method-assign]
    ns = _ns(gui)
    h.install(ns)
    return h, ns, settled


def _kinds(result: dict) -> list[str]:
    return [o["type"] for o in result["output"]]


def test_auto_screenshot_after_actions_errors_and_empty_calls() -> None:
    gui = FakeAutoGUI()
    h, ns, settled = _helpers(gui)
    r = dw.execute("pyautogui.click(1,2)", ns, h)
    # the call clicked, so the closing line is "[after your input]" (a call that only looked says "[after]")
    assert _kinds(r) == ["input_image", "input_text"] and r["output"][1]["text"].startswith(
        "[after your input] frontmost: Warp")
    assert settled == [1.5] and gui.clicks == [(1, 2)]
    r = dw.execute("x = screen_text()", ns, h)                       # text only: no image
    assert _kinds(r) == ["input_text", "input_text"] and r["output"][0]["text"] == "screen_text: 1 lines"
    assert settled == [1.5]
    r = dw.execute("pass", ns, h)                                     # empty: a picture, no wait
    assert _kinds(r) == ["input_image", "input_text"] and settled == [1.5, 0.0]
    r = dw.execute("display(pyautogui.screenshot())", ns, h)          # displayed itself: exactly one image
    assert _kinds(r) == ["input_image", "input_text"] and settled == [1.5, 0.0]
    r = dw.execute("1/0", ns, h)
    assert _kinds(r) == ["input_text", "input_image", "input_text"]
    assert "ZeroDivisionError" in r["output"][0]["text"] and r["output"][2]["text"].startswith("[after]")
    assert settled == [1.5, 0.0, 0.0]
    r = dw.execute("type_text('hi')", ns, h)                          # helper actions count too
    assert _kinds(r) == ["input_text", "input_image", "input_text"] and settled[-1] == 1.5
    assert gui.hotkeys == [("command", "v")]


def test_serve_observe_operation(monkeypatch) -> None:
    gui = FakeAutoGUI()
    h, ns, _ = _helpers(gui)
    out: list[dict] = []
    monkeypatch.setattr(dw, "emit", out.append)
    dw.serve(ns, iter([json.dumps({"id": 1, "operation": "observe"}) + "\n",
                       json.dumps({"id": 2, "operation": "ping"}) + "\n"]), h)
    assert out[0]["id"] == 1
    kinds = [o["type"] for o in out[0]["output"]]
    assert kinds.count("input_image") == 1
    assert out[0]["output"][1]["text"] == "frontmost: Warp — 'zsh'; screen 100x50; 14:02"
    assert out[1]["error"]["code"] == "unsupported" and "observe" in out[1]["error"]["message"]


def test_release_inputs_posts_key_and_mouse_ups() -> None:
    posted: list[tuple] = []
    quartz = SimpleNamespace(
        kCGHIDEventTap="hid", kCGEventLeftMouseUp="lup", kCGEventRightMouseUp="rup", kCGEventOtherMouseUp="oup",
        kCGMouseButtonLeft=0, kCGMouseButtonRight=1, kCGMouseButtonCenter=2,
        CGEventCreateKeyboardEvent=lambda src, code, down: ("key", code, down),
        CGEventCreate=lambda src: "ev", CGEventGetLocation=lambda ev: (3, 4),
        CGEventCreateMouseEvent=lambda src, kind, loc, button: ("mouse", kind, loc, button),
        CGEventPost=lambda tap, ev: posted.append((tap, ev)),
    )
    assert dw.release_inputs(quartz) == 131
    keys = [ev for tap, ev in posted if ev[0] == "key"]
    mice = [ev for tap, ev in posted if ev[0] == "mouse"]
    assert len(keys) == 128 and all(ev[2] is False for ev in keys) and [ev[1] for ev in keys] == list(range(128))
    assert mice == [("mouse", "lup", (3, 4), 0), ("mouse", "rup", (3, 4), 1), ("mouse", "oup", (3, 4), 2)]
    assert all(tap == "hid" for tap, _ in posted)
