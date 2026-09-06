"""The hands: a persistent PyAutoGUI REPL that runs the model's Python on this Mac.

Runs as a child process of the daemon (`python -m cc_buddy_bridge.desktop_worker`),
one JSON request per stdin line, one JSON reply per stdout line:

    {"id": 1, "operation": "execute", "code": "display(pyautogui.screenshot())"}
    {"id": 1, "output": [{"type":"input_text","text":...}, {"type":"input_image",...}]}

Globals persist between calls, exactly like the `exec_py` tool in OpenAI's
computer-use sample app (openai/openai-cua-sample-app, python-app/app/desktop/
worker.py, MIT) which this file follows: `log(...)` collects text, `display(img)`
collects PNGs, exceptions come back as text so the model can correct itself,
and the PyAutoGUI fail-safe (mouse into a screen corner) is a terminal error.

Retina: PyAutoGUI screenshots are physical pixels (2x) while its input is
logical points. `normalize_screenshots` resizes every screenshot to the
point grid so the coordinates the model reads are the coordinates it clicks.

On top of raw PyAutoGUI the namespace carries the helpers from
desktop_helpers.py — open_app, open_url, frontmost, screen_text, find_text,
click_text, wait_for, wait_settled, type_text, zoom, observe — which sense
the screen locally (CG capture, Vision OCR, AX) and log one sentence each.

Auto-screenshot rule: when a call clicked, typed or pressed keys (any
PyAutoGUI input function, type_text, click_text), raised, or produced no
output, and did not display() an image itself, `execute` appends a screenshot
taken after the screen settles (≤ 1.5 s) — then always an `[after]` line with
the frontmost app and whether the screen changed since the previous call.
Text-only calls (screen_text, find_text) stay cheap: no image.

`{"operation": "observe"}` is `execute("observe()")`: the first turn's picture.
`python -m cc_buddy_bridge.desktop_worker --release` posts a key-up for every
key code and a mouse-up for every button, in a fresh process, so the daemon
can release anything a killed worker left held.

Nothing here is imported by the daemon; the daemon talks to it over the
pipe (computer_agent.py). `execute()` and `normalize_screenshots()` are plain
functions so tests can drive them with a fake pyautogui.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import signal
import sys
import threading
import time
import traceback
from typing import Any

MAX_TEXT_BYTES = 60 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 250
MAX_CODE_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 512 * 1024


def emit(payload: dict[str, Any]) -> None:
    out = sys.__stdout__
    if out is None:
        raise RuntimeError("worker stdout is unavailable")
    out.write(json.dumps(payload) + "\n")
    out.flush()


def check_desktop(pyautogui: Any) -> tuple[int, int]:
    """Fail loudly at startup when the daemon cannot see or touch the screen."""
    width, height = pyautogui.size()
    if width <= 0 or height <= 0:
        raise RuntimeError("no desktop: the daemon must run in the user's GUI session (LaunchAgent)")
    if sys.platform == "darwin":
        import ctypes

        services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        if not services.AXIsProcessTrusted():
            raise RuntimeError(
                f"Accessibility is not granted to {os.path.realpath(sys.executable)} — "
                "System Settings > Privacy & Security > Accessibility")
        import Quartz

        if not Quartz.CGPreflightScreenCaptureAccess():
            raise RuntimeError(
                f"Screen Recording is not granted to {os.path.realpath(sys.executable)} — "
                "System Settings > Privacy & Security > Screen & System Audio Recording")
    return int(width), int(height)


def normalize_screenshots(pyautogui: Any) -> None:
    """Make pyautogui.screenshot() return images in point coordinates (Retina 2x → 1x)."""
    from PIL import Image

    capture = pyautogui.screenshot

    def screenshot(imageFilename: Any = None, region: Any = None, **kwargs: Any) -> Any:
        image = capture(**kwargs)
        size = tuple(pyautogui.size())
        if tuple(image.size) != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        if region is not None:
            x, y, w, h = region
            image = image.crop((x, y, x + w, y + h))
        if imageFilename is not None:
            image.save(imageFilename)
        return image

    pyautogui.screenshot = screenshot


def execute(code: str, namespace: dict[str, Any], helpers: Any = None) -> dict[str, Any]:
    """Run one exec_py call. Returns {"output": [...]} or {"error": {...}} (fail-safe).

    With `helpers` (desktop_helpers.Helpers) the auto-screenshot rule from the
    module docstring applies and an `[after]` line closes every reply.
    """
    from PIL import Image

    output: list[dict[str, Any]] = []
    text_bytes = 0
    image_bytes = 0

    def log(*values: Any) -> None:
        nonlocal text_bytes
        text = " ".join(str(v) for v in values)
        text_bytes += len(text.encode("utf-8"))
        if text_bytes > MAX_TEXT_BYTES or len(output) >= MAX_ITEMS:
            raise ValueError("text output exceeds its size limit")
        output.append({"type": "input_text", "text": text})

    def display(value: Any) -> None:
        nonlocal image_bytes
        if isinstance(value, Image.Image):
            buf = io.BytesIO()
            value.save(buf, format="PNG")
            value = buf.getvalue()
        if not isinstance(value, bytes) or not value.startswith(b"\x89PNG\r\n\x1a\n"):
            raise TypeError("display() expects a Pillow image or PNG bytes")
        image_bytes += len(value)
        if image_bytes > MAX_IMAGE_BYTES or len(output) >= MAX_ITEMS:
            raise ValueError("image output exceeds its size limit")
        output.append({"type": "input_image", "detail": "original",
                       "image_url": "data:image/png;base64," + base64.b64encode(value).decode("ascii")})

    class Writer:
        def write(self, text: str) -> int:
            if text.strip():
                log(text.rstrip("\n"))
            return len(text)

        def flush(self) -> None:
            pass

    namespace.update(log=log, display=display)
    if helpers is not None:
        helpers.bind(log, display)
        helpers.begin()
    errored = False
    try:
        with contextlib.redirect_stdout(Writer()), contextlib.redirect_stderr(Writer()):
            exec(compile(code, "<exec_py>", "exec"), namespace)  # noqa: S102 — the model's code, on purpose
    except BaseException as error:  # noqa: BLE001 — every error goes back to the model
        failsafe = getattr(namespace.get("pyautogui"), "FailSafeException", ())
        if failsafe and isinstance(error, failsafe):
            return {"error": {"code": "failsafe", "message": "desktop fail-safe: the mouse hit a screen corner"}}
        errored = True
        output.append({"type": "input_text", "text": traceback.format_exc()[-4000:]})
    if helpers is not None:
        # An action needs its settled result in the same reply (a blind repeat
        # or a screenshot-only turn costs 3-5 s of model time; 1.5 s here caps
        # the worker at ~2 s). Text-only calls stay cheap; failed or empty
        # calls still end with a picture so the next turn can act.
        has_image = any(o["type"] == "input_image" for o in output)
        if not has_image and (helpers.acted or errored or not output):
            with contextlib.suppress(Exception):
                display(helpers.settled_screenshot(1.5 if helpers.acted else 0.0))
        with contextlib.suppress(Exception):
            log(helpers.after_line())
    return {"output": output or [{"type": "input_text", "text": "exec_py completed with no output."}]}


def serve(namespace: dict[str, Any], stream: Any, helpers: Any = None) -> None:
    terminal: Any = None
    for line in stream:
        if len(line.encode("utf-8")) > MAX_REQUEST_BYTES:
            emit({"error": {"code": "request_too_large", "message": "request exceeds 512 KiB"}})
            continue
        try:
            req = json.loads(line)
        except ValueError:
            emit({"error": {"code": "bad_json", "message": "request is not JSON"}})
            continue
        rid = req.get("id") if isinstance(req, dict) else None
        if terminal is not None:
            emit({"id": rid, "error": terminal})
            continue
        if isinstance(req, dict) and req.get("operation") == "observe":
            result = execute("observe()", namespace, helpers)
            terminal = result.get("error")
            emit({"id": rid, **result})
            continue
        if not isinstance(req, dict) or req.get("operation") != "execute":
            emit({"id": rid, "error": {"code": "unsupported", "message": "only execute and observe are supported"}})
            continue
        code = req.get("code")
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_CODE_BYTES:
            emit({"id": rid, "error": {"code": "bad_code", "message": "code must be a non-empty string under 64 KiB"}})
            continue
        result = execute(code, namespace, helpers)
        terminal = result.get("error")
        emit({"id": rid, **result})


def release_inputs(quartz: Any) -> int:
    """Post a key-up for every key code (0..127) and a mouse-up for each button.

    A killed worker can leave a modifier or a mouse button held at the HID
    level; posting the ups clears that. Port of openai/openai-cua-sample-app
    python-app/app/desktop/release_inputs.py (MIT). Returns the events posted.
    """
    count = 0
    for code in range(128):
        quartz.CGEventPost(quartz.kCGHIDEventTap, quartz.CGEventCreateKeyboardEvent(None, code, False))
        count += 1
    location = quartz.CGEventGetLocation(quartz.CGEventCreate(None))
    buttons = ((quartz.kCGEventLeftMouseUp, quartz.kCGMouseButtonLeft),
               (quartz.kCGEventRightMouseUp, quartz.kCGMouseButtonRight),
               (quartz.kCGEventOtherMouseUp, quartz.kCGMouseButtonCenter))
    for kind, button in buttons:
        quartz.CGEventPost(quartz.kCGHIDEventTap, quartz.CGEventCreateMouseEvent(None, kind, location, button))
        count += 1
    return count


def main() -> None:
    import pyautogui

    from .desktop_helpers import Helpers

    width, height = check_desktop(pyautogui)
    normalize_screenshots(pyautogui)
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    # __builtins__ stays open on purpose: exec_py is unrestricted Python as the
    # daemon user (docs/stackchan/voice.md, Safety); the helpers exist so the
    # model has no reason to reach past them.
    namespace: dict[str, Any] = {"__builtins__": __builtins__, "pyautogui": pyautogui, "time": time}
    helpers = Helpers(pyautogui)
    helpers.install(namespace)
    emit({"ready": True, "platform": sys.platform, "width": width, "height": height})
    if "--check" in sys.argv:
        return
    parent = os.getppid()

    def watch_parent() -> None:
        while True:
            time.sleep(1)
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGKILL)

    threading.Thread(target=watch_parent, daemon=True).start()
    serve(namespace, sys.stdin, helpers)


if __name__ == "__main__":
    if "--release" in sys.argv:
        import Quartz

        release_inputs(Quartz)
        sys.exit(0)
    try:
        main()
    except BaseException as error:  # noqa: BLE001 — the parent needs a protocol error for any startup failure
        emit({"error": {"code": "startup", "message": f"{type(error).__name__}: {error}"[:4000]}})
        sys.exit(1)
