"""desktop_worker.py with a fake pyautogui: exec semantics, output shaping,
Retina normalisation, error and fail-safe handling, and the line protocol."""

from __future__ import annotations

import base64
import io
import json

from PIL import Image

from cc_buddy_bridge import desktop_worker as dw


class FailSafeException(Exception):
    pass


class FakeAutoGUI:
    FailSafeException = FailSafeException

    def __init__(self, points=(100, 50), pixels=(200, 100)) -> None:
        self._points = points
        self._pixels = pixels
        self.clicks: list[tuple[int, int]] = []

    def size(self):
        return self._points

    def screenshot(self, **kwargs):
        return Image.new("RGB", self._pixels, (10, 20, 30))

    def click(self, x, y):
        self.clicks.append((x, y))


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
