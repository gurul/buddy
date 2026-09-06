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


def execute(code: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Run one exec_py call. Returns {"output": [...]} or {"error": {...}} (fail-safe)."""
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
    try:
        with contextlib.redirect_stdout(Writer()), contextlib.redirect_stderr(Writer()):
            exec(compile(code, "<exec_py>", "exec"), namespace)  # noqa: S102 — the model's code, on purpose
    except BaseException as error:  # noqa: BLE001 — every error goes back to the model
        failsafe = getattr(namespace.get("pyautogui"), "FailSafeException", ())
        if failsafe and isinstance(error, failsafe):
            return {"error": {"code": "failsafe", "message": "desktop fail-safe: the mouse hit a screen corner"}}
        output.append({"type": "input_text", "text": traceback.format_exc()[-4000:]})
    return {"output": output or [{"type": "input_text", "text": "exec_py completed with no output."}]}


def serve(namespace: dict[str, Any], stream: Any) -> None:
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
        if not isinstance(req, dict) or req.get("operation") != "execute":
            emit({"id": rid, "error": {"code": "unsupported", "message": "only execute is supported"}})
            continue
        code = req.get("code")
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_CODE_BYTES:
            emit({"id": rid, "error": {"code": "bad_code", "message": "code must be a non-empty string under 64 KiB"}})
            continue
        result = execute(code, namespace)
        terminal = result.get("error")
        emit({"id": rid, **result})


def main() -> None:
    import pyautogui

    width, height = check_desktop(pyautogui)
    normalize_screenshots(pyautogui)
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    namespace: dict[str, Any] = {"__builtins__": __builtins__, "pyautogui": pyautogui, "time": time}
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
    serve(namespace, sys.stdin)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:  # noqa: BLE001 — the parent needs a protocol error for any startup failure
        emit({"error": {"code": "startup", "message": f"{type(error).__name__}: {error}"[:4000]}})
        sys.exit(1)
