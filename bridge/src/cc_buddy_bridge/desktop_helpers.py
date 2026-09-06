"""On-device senses and effectors for the desktop worker: what the model's
exec_py code gets besides raw PyAutoGUI.

Every helper is a plain method on `Helpers`; `install()` binds them into the
worker's exec namespace and wraps the PyAutoGUI input functions so the worker
knows when a call acted (it then appends a settled screenshot, see
desktop_worker.execute). Each helper returns a value AND logs one short
sentence (≤ 80 chars, never the typed text) that the human reads on the robot.

Perception is local, so it costs 0.04–0.31 s instead of a model turn
(measured 2026-09-06 on a 3024x1964 display):

- `cg_capture`: CGDisplayCreateImage of the main display, 0.037 s, BGRA →
  a Pillow RGB image pixel-identical to pyautogui.screenshot() (0.114 s).
- `vision_ocr`: VNRecognizeTextRequest on that CGImage, accurate 0.31 s /
  fast 0.042 s (fast merges neighbouring words). Boxes are normalized with a
  bottom-left origin; `screen_text` maps them to click points.
- `ax_frontmost`: NSWorkspace frontmost app + the AX focused-window title.

Only desktop_worker.main() and the tests import this module; pyobjc is
imported lazily inside the three backend functions so the tests run anywhere.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

HELPER_NAMES = ("open_app", "open_url", "frontmost", "screen_text", "find_text", "click_text",
                "click_element", "wait_for", "wait_settled", "type_text", "zoom", "observe")
# Roles an AX hit-test may climb to before clicking (handyman's snap: coordinates
# only need to land INSIDE the control; the control absorbs the model's error).
AX_PRESSABLE_ROLES = frozenset({"AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXMenuItem",
                                "AXMenuBarItem", "AXPopUpButton", "AXTab", "AXTextField", "AXTextArea",
                                "AXComboBox", "AXCell", "AXRow", "AXImage", "AXStaticText", "AXDisclosureTriangle",
                                "AXIncrementor", "AXSlider", "AXSearchField"})
AX_MAX_HOPS = 6
ACTION_NAMES = ("click", "doubleClick", "rightClick", "middleClick", "mouseDown", "mouseUp", "dragTo",
                "dragRel", "moveTo", "scroll", "hscroll", "press", "hotkey", "keyDown", "keyUp",
                "write", "typewrite")
BROWSER_BUNDLES = frozenset({"com.apple.Safari", "com.google.Chrome", "org.mozilla.firefox",
                             "company.thebrowser.Browser", "com.brave.Browser", "com.microsoft.edgemac"})
MAX_WAIT_SECS = 30.0          # every wait is clamped here: 30 s + 0.31 s OCR stays under the 60 s exec deadline
CHANGE_THRESHOLD = 0.003      # fraction of 1/8-scale pixels differing by > 24/255; caret/clock blink ≈ 2-4 px of 92k
SCREEN_TEXT_MAX_LINES = 120
SCREEN_TEXT_MAX_BYTES = 6 * 1024
ZOOM_MAX_POINTS = (800, 600)
EMPTY_FRONT = {"app": "", "bundle": "", "title": "", "pid": 0}


@dataclass
class Frame:
    """One physical-pixel capture of the screen (2x on Retina)."""

    cg: Any                    # CGImageRef, or None in tests
    width: int
    height: int
    _pil: Any = None
    _thumb: Any = None

    @classmethod
    def from_pil(cls, img: Any) -> "Frame":
        return cls(None, int(img.width), int(img.height), img)

    def pil(self) -> Any:
        """The frame as a Pillow RGB image (cached)."""
        if self._pil is None:
            import Quartz
            from PIL import Image

            data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(self.cg))
            bpr = Quartz.CGImageGetBytesPerRow(self.cg)
            self._pil = Image.frombuffer("RGBA", (self.width, self.height), bytes(data), "raw", "BGRA", bpr, 1)
            self._pil = self._pil.convert("RGB")
        return self._pil

    def thumb(self) -> Any:
        """1/8-scale grayscale, for change detection (0.003 s)."""
        if self._thumb is None:
            w, h = max(1, self.width // 8), max(1, self.height // 8)
            self._thumb = self.pil().convert("L").resize((w, h))
        return self._thumb


def changed_fraction(a_thumb: Any, b_thumb: Any) -> float:
    """Fraction of thumbnail pixels that differ by more than 24/255 (1.0 when the sizes differ)."""
    from PIL import ImageChops

    if a_thumb.size != b_thumb.size:
        return 1.0
    diff = ImageChops.difference(a_thumb, b_thumb).point(lambda v: 255 if v > 24 else 0)
    return diff.histogram()[255] / float(a_thumb.width * a_thumb.height)


# ---- real backends (macOS only) ------------------------------------------------------

def cg_capture() -> Frame:
    """The main display as a CGImage (physical pixels)."""
    import Quartz

    cg = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
    if cg is None:
        raise RuntimeError("screen capture failed — Screen Recording is not granted to this python")
    return Frame(cg, int(Quartz.CGImageGetWidth(cg)), int(Quartz.CGImageGetHeight(cg)))


def vision_ocr(frame: Frame, level: str = "accurate") -> list[dict[str, Any]]:
    """Vision text recognition. Boxes are normalized, bottom-left origin: bx, by, bw, bh."""
    import Vision

    if frame.cg is None:
        raise RuntimeError("vision_ocr needs a CGImage frame")
    req = Vision.VNRecognizeTextRequest.alloc().init()
    fast = level == "fast"
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if fast
                             else Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(frame.cg, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    out: list[dict[str, Any]] = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        box = obs.boundingBox()
        out.append({"text": str(cands[0].string()), "bx": float(box.origin.x), "by": float(box.origin.y),
                    "bw": float(box.size.width), "bh": float(box.size.height),
                    "conf": float(cands[0].confidence())})
    return out


def ax_frontmost() -> dict[str, Any]:
    """{"app","bundle","title","pid"} of the frontmost app; title "" on any AX error."""
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return dict(EMPTY_FRONT)
    info: dict[str, Any] = {"app": str(app.localizedName() or ""), "bundle": str(app.bundleIdentifier() or ""),
                            "title": "", "pid": int(app.processIdentifier())}
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            kAXFocusedWindowAttribute,
            kAXTitleAttribute,
        )

        element = AXUIElementCreateApplication(info["pid"])
        err, window = AXUIElementCopyAttributeValue(element, kAXFocusedWindowAttribute, None)
        if err == 0 and window is not None:
            err, title = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
            if err == 0 and title:
                info["title"] = str(title)
    except Exception:  # noqa: BLE001 — a missing title is not an error for the model
        pass
    return info


def ax_element_at(x: int, y: int) -> Optional[dict[str, Any]]:
    """The accessibility element under a point (points, top-left origin), climbed to the
    nearest pressable ancestor: {"role","title","frame":(x,y,w,h),"pressable"}. None when
    nothing is there or Accessibility is not granted. Bench 2026-09-06: 1-24 ms."""
    try:
        from ApplicationServices import (
            AXUIElementCopyActionNames,
            AXUIElementCopyAttributeValue,
            AXUIElementCopyElementAtPosition,
            AXUIElementCreateSystemWide,
            AXValueGetValue,
            kAXValueCGPointType,
            kAXValueCGSizeType,
        )
    except Exception:  # noqa: BLE001
        return None

    def attr(el: Any, name: str) -> Any:
        err, val = AXUIElementCopyAttributeValue(el, name, None)
        return val if err == 0 else None

    def describe(el: Any) -> dict[str, Any]:
        role = str(attr(el, "AXRole") or "")
        title = attr(el, "AXTitle") or attr(el, "AXDescription") or ""
        if not title:
            val = attr(el, "AXValue")
            if isinstance(val, str):
                title = val[:60]
        err, names = AXUIElementCopyActionNames(el, None)
        actions = list(names) if err == 0 and names else []
        frame = None
        pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
        if pos is not None and size is not None:
            ok1, pt = AXValueGetValue(pos, kAXValueCGPointType, None)
            ok2, sz = AXValueGetValue(size, kAXValueCGSizeType, None)
            if ok1 and ok2:
                frame = (int(pt.x), int(pt.y), int(sz.width), int(sz.height))
        return {"role": role, "title": str(title), "frame": frame,
                "pressable": "AXPress" in actions or role in AX_PRESSABLE_ROLES}

    err, el = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), float(x), float(y), None)
    if err != 0 or el is None:
        return None
    cur, first = el, None
    for _ in range(AX_MAX_HOPS):
        d = describe(cur)
        first = first or d
        if d["pressable"] and (d["title"] or d["role"] != "AXGroup"):
            return d
        cur = attr(cur, "AXParent")
        if cur is None:
            break
    return first


def _matches(expect: str, role: str, title: str) -> bool:
    """Does the model's expectation ("the blue Sign in button") describe this element?"""
    e = expect.casefold()
    t = title.casefold()
    if t and (t in e or e in t):
        return True
    words = {w for w in re.findall(r"[a-z0-9]+", e) if len(w) > 2 and w not in ("the", "button", "link", "icon",
                                                                                   "field", "menu", "tab", "item")}
    hits = sum(1 for w in words if w in t)
    if words and hits / len(words) >= 0.5:
        return True
    # "the text field" against an untitled AXTextField: compare the role word without "AX"
    return not t and role[2:].casefold() in re.sub(r"\s+", "", e)


# ---- the helpers ----------------------------------------------------------------------

class Helpers:
    """The model-facing helpers. Every backend is injectable for the tests."""

    def __init__(
        self,
        pyautogui: Any,
        *,
        capture: Callable[[], Frame] = cg_capture,
        ocr: Callable[[Frame, str], list[dict[str, Any]]] = vision_ocr,
        frontmost_fn: Callable[[], dict[str, Any]] = ax_frontmost,
        element_at: Callable[[int, int], Optional[dict[str, Any]]] = ax_element_at,
        run: Callable[..., Any] = subprocess.run,
        clipboard: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.gui = pyautogui
        self._capture_fn = capture
        self._ocr = ocr
        self._frontmost_fn = frontmost_fn
        self._element_at = element_at
        self._run = run
        self._last_click: Optional[str] = None      # "AXButton 'Search'" under the last raw click
        self._clipboard = clipboard
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self.acted = False
        self.log: Callable[..., None] = lambda *_a: None
        self.display: Callable[[Any], None] = lambda _i: None
        self._frame: Optional[Frame] = None
        self._last_thumb: Any = None
        self._elapsed = 0.0

    # -- wiring --
    def install(self, namespace: dict[str, Any]) -> None:
        """Bind the helpers into the exec namespace and make every PyAutoGUI input call mark `acted`."""
        for name in HELPER_NAMES:
            namespace[name] = getattr(self, name)
        for name in ACTION_NAMES:
            original = getattr(self.gui, name, None)
            if original is None or getattr(original, "_cc_buddy_wrapped", False):
                continue
            setattr(self.gui, name, self._acting(original))

    def _acting(self, original: Callable[..., Any]) -> Callable[..., Any]:
        name = getattr(original, "__name__", "action")

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self.acted = True
            if name in ("click", "doubleClick", "rightClick") and len(args) >= 2:
                # What is under a raw click, for the [after] line (handyman-style verification
                # of the model's coordinates — it sees "clicked on AXLink 'Weather'").
                self._last_click = self._describe_point(int(args[0]), int(args[1]))
            return original(*args, **kwargs)

        wrapped._cc_buddy_wrapped = True  # type: ignore[attr-defined]
        wrapped.__name__ = getattr(original, "__name__", "action")
        return wrapped

    def bind(self, log: Callable[..., None], display: Callable[[Any], None]) -> None:
        self.log, self.display = log, display

    def begin(self) -> None:
        self.acted = False
        self._last_click = None

    def _describe_point(self, x: int, y: int) -> Optional[str]:
        try:
            el = self._element_at(x, y)
        except Exception:  # noqa: BLE001
            return None
        if not el:
            return None
        return f"{el.get('role') or 'element'} {el.get('title')!r}" if el.get("title") else str(el.get("role") or "")

    def click_element(self, x: int, y: int, expect: str = "", clicks: int = 1) -> str:
        """Click the control under (x, y) — snapped to its centre — after checking it is the
        one you meant. `expect` is a few words from the screenshot ("Sign in button").
        A mismatch does NOT click; it tells you what is there instead."""
        el = None
        try:
            el = self._element_at(int(x), int(y))
        except Exception:  # noqa: BLE001
            el = None
        if el is None:
            self.acted = True
            self.gui.click(int(x), int(y), clicks=clicks)
            line = f"clicked at ({x},{y}) (no accessibility element there to check)"
            self.log(line)
            return line
        role, title = el.get("role") or "element", el.get("title") or ""
        what = f"{role} {title!r}" if title else role
        if expect and not _matches(expect, role, title):
            line = f"did not click: under ({x},{y}) is {what}, not {expect!r}. Look again (screen_text / zoom)."
            self.log(line)
            return line
        cx, cy = int(x), int(y)
        frame = el.get("frame")
        if frame and frame[2] > 0 and frame[3] > 0:
            cx, cy = int(frame[0] + frame[2] / 2), int(frame[1] + frame[3] / 2)
        self.acted = True
        self._last_click = what
        self.gui.click(cx, cy, clicks=clicks)
        line = f"clicked {what} at ({cx},{cy})"
        self.log(line)
        return line

    # -- apps and pages --
    def open_app(self, name: str, wait: float = 8.0) -> str:
        """Launch or focus an app with `open -a`, then wait until it is frontmost."""
        self.acted = True
        if "/" in name or name.startswith("-"):
            raise ValueError(f"open_app: {name!r} must be an app name, not a path or an option")
        result = self._run(["open", "-a", name], capture_output=True, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"no app called {name!r}")
        wait = min(float(wait), MAX_WAIT_SECS)
        t0 = self._clock()
        front = self._frontmost_fn()
        while front.get("app", "").casefold() != name.casefold() and self._clock() - t0 < wait:
            self._sleep(0.1)
            front = self._frontmost_fn()
        elapsed = self._clock() - t0
        if front.get("app", "").casefold() == name.casefold():
            text = f"opened {name} (frontmost after {elapsed:.1f} s)"
        else:
            text = f"opened {name} but {front.get('app') or 'something else'} is still frontmost after {wait:.1f} s"
        self._settle(1.5)
        self.log(text)
        return text

    def open_url(self, url: str, app: Optional[str] = None, wait: float = 8.0) -> str:
        """Open a URL in the default browser (or `app`) and wait for it to come up."""
        self.acted = True
        if urlsplit(url).scheme.lower() not in ("http", "https", "mailto"):
            raise ValueError("open_url: only http, https and mailto URLs")
        if app is not None and ("/" in app or app.startswith("-")):
            raise ValueError(f"open_url: {app!r} must be an app name")
        before = self._frontmost_fn()
        result = self._run(["open", *(["-a", app] if app else []), url], capture_output=True, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"could not open {url!r}" + (f" in {app!r}" if app else ""))
        wait = min(float(wait), MAX_WAIT_SECS)
        t0 = self._clock()

        def arrived(front: dict[str, Any]) -> bool:
            # A named app must itself come to the front: another browser being up
            # is not arrival (bench 2026-09-06: Chrome in front made Safari "done").
            if app:
                return front.get("app", "").casefold() == app.casefold()
            if front.get("bundle") in BROWSER_BUNDLES:
                return True
            return front.get("title", "") != before.get("title", "")

        front = self._frontmost_fn()
        while not arrived(front) and self._clock() - t0 < wait:
            self._sleep(0.2)
            front = self._frontmost_fn()
        if arrived(front):
            text = f"opened {url} in {front.get('app') or 'the browser'}"
        else:
            text = f"opened {url} but {front.get('app') or 'something else'} is still frontmost after {wait:.1f} s"
        self._settle(2.0)
        self.log(text)
        return text

    def frontmost(self) -> dict[str, Any]:
        front = self._frontmost_fn()
        self.log(f"frontmost: {front.get('app', '')} — {front.get('title', '')!r}")
        return front

    # -- reading the screen --
    def screen_text(self, region: Optional[tuple[int, int, int, int]] = None,
                    level: str = "accurate") -> list[dict[str, Any]]:
        """OCR the screen: [{"text","x","y","w","h","conf"}] in click coordinates, top to bottom."""
        lines = self._lines(region, level)
        total = len(lines)
        kept: list[dict[str, Any]] = []
        size = 0
        for item in lines:
            size += len(item["text"].encode("utf-8")) + 48
            if len(kept) >= SCREEN_TEXT_MAX_LINES or size > SCREEN_TEXT_MAX_BYTES:
                break
            kept.append(item)
        if len(kept) < total:
            kept.append({"text": f"({total - len(kept)} more lines; use region=)", "x": 0, "y": 0, "w": 0, "h": 0,
                         "conf": 0.0})
        self.log(f"screen_text: {total} lines")
        return kept

    def _lines(self, region: Optional[tuple[int, int, int, int]], level: str) -> list[dict[str, Any]]:
        frame = self._capture()
        w_pts, h_pts = self._size()
        items: list[dict[str, Any]] = []
        for r in self._ocr(frame, level):
            x = round(r["bx"] * w_pts)
            y = round((1.0 - r["by"] - r["bh"]) * h_pts)
            w = round(r["bw"] * w_pts)
            h = round(r["bh"] * h_pts)
            if region is not None:
                rx, ry, rw, rh = region
                cx, cy = x + w / 2, y + h / 2
                if not (rx <= cx <= rx + rw and ry <= cy <= ry + rh):
                    continue
            items.append({"text": str(r["text"]), "x": x, "y": y, "w": w, "h": h,
                          "conf": round(float(r.get("conf", 1.0)), 3)})
        items.sort(key=lambda i: (i["y"] // 8, i["x"]))
        return items

    def find_text(self, text: str, region: Optional[tuple[int, int, int, int]] = None,
                  level: str = "accurate") -> Optional[dict[str, Any]]:
        """The first on-screen line equal to `text` (else containing it), with its centre cx, cy."""
        item = self._find(text, region, level)
        if item is None:
            self.log(f"{text!r} is not on screen")
        else:
            self.log(f"found {text!r} at ({item['cx']},{item['cy']})")
        return item

    def _find(self, text: str, region: Optional[tuple[int, int, int, int]], level: str) -> Optional[dict[str, Any]]:
        needle = text.casefold().strip()
        if not needle:
            return None
        lines = self._lines(region, level)
        hit = next((i for i in lines if i["text"].casefold().strip() == needle), None)
        if hit is None:
            hit = next((i for i in lines if needle in i["text"].casefold()), None)
        if hit is None:
            return None
        return {**hit, "cx": hit["x"] + hit["w"] // 2, "cy": hit["y"] + hit["h"] // 2}

    def click_text(self, text: str, region: Optional[tuple[int, int, int, int]] = None,
                   timeout: float = 3.0, clicks: int = 1) -> str:
        """Find `text` on screen (waiting up to `timeout`) and click its centre."""
        item = self._wait_for(text, gone=False, timeout=timeout, interval=0.3, region=region)
        if not isinstance(item, dict):
            raise LookupError(f"{text!r} is not on screen")
        self.acted = True
        self.gui.click(item["cx"], item["cy"], clicks=clicks)
        line = f"clicked {text!r} at ({item['cx']},{item['cy']})"
        self.log(line)
        return line

    def wait_for(self, text: str, *, gone: bool = False, timeout: float = 6.0, interval: float = 0.3,
                 region: Optional[tuple[int, int, int, int]] = None) -> Any:
        """Poll (fast OCR) until `text` is on screen — or, with gone=True, until it has gone."""
        result = self._wait_for(text, gone=gone, timeout=timeout, interval=interval, region=region)
        elapsed = self._elapsed
        timeout = min(float(timeout), MAX_WAIT_SECS)
        if gone:
            self.log(f"{text!r} went away after {elapsed:.1f} s" if result else
                     f"{text!r} is still on screen after {timeout:.1f} s")
        else:
            self.log(f"{text!r} appeared after {elapsed:.1f} s" if result is not None else
                     f"{text!r} not seen in {timeout:.1f} s")
        return result

    def _wait_for(self, text: str, *, gone: bool, timeout: float, interval: float,
                  region: Optional[tuple[int, int, int, int]]) -> Any:
        timeout = min(float(timeout), MAX_WAIT_SECS)
        t0 = self._clock()
        while True:
            item = self._find(text, region, "fast")
            self._elapsed = self._clock() - t0
            if gone and item is None:
                return True
            if not gone and item is not None:
                return self._find(text, region, "accurate") or item
            if self._clock() - t0 >= timeout:
                return False if gone else None
            self._sleep(interval)

    def wait_settled(self, timeout: float = 5.0, interval: float = 0.25) -> bool:
        """Wait until two consecutive captures `interval` apart look the same."""
        settled = self._settle(timeout, interval)
        if settled:
            self.log(f"screen settled after {self._elapsed:.1f} s")
        else:
            self.log(f"screen still changing after {min(float(timeout), MAX_WAIT_SECS):.1f} s")
        return settled

    def _settle(self, timeout: float, interval: float = 0.25) -> bool:
        timeout = min(float(timeout), MAX_WAIT_SECS)
        t0 = self._clock()
        a = self._capture().thumb()
        if timeout <= 0:
            self._elapsed = 0.0
            return True
        while True:
            self._sleep(interval)
            b = self._capture().thumb()
            self._elapsed = self._clock() - t0
            if changed_fraction(a, b) < CHANGE_THRESHOLD:
                return True
            a = b
            if self._clock() - t0 >= timeout:
                return False

    # -- typing --
    def type_text(self, text: str, submit: bool = False) -> str:
        """Type any text (clipboard paste: emoji and accents survive), optionally pressing enter."""
        self.acted = True
        clipboard = self._clipboard
        if clipboard is None:
            import pyperclip

            clipboard = self._clipboard = pyperclip
        try:
            old = clipboard.paste()
        except Exception:  # noqa: BLE001 — a non-text clipboard is not worth failing over
            old = ""
        clipboard.copy(text)
        self.acted = True
        self.gui.hotkey("command", "v")
        self._sleep(0.15)
        clipboard.copy(old if isinstance(old, str) else "")
        line = f"typed {len(text)} characters"
        if submit:
            self.gui.press("enter")
            line += " and pressed enter"
        self.log(line)
        return line

    # -- looking --
    def zoom(self, x: int, y: int, w: int, h: int) -> str:
        """Display a close-up of that point region at the frame's physical scale."""
        frame = self._capture()
        w_pts, h_pts = self._size()
        scale = frame.width / float(w_pts) if w_pts else 1.0
        x, y = max(0, int(x)), max(0, int(y))
        w = max(1, min(int(w), ZOOM_MAX_POINTS[0], w_pts - x))
        h = max(1, min(int(h), ZOOM_MAX_POINTS[1], h_pts - y))
        box = (round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale))
        self.display(frame.pil().crop(box))
        line = (f"zoom of points ({x},{y},{w},{h}) at {scale:g}x: a pixel (px,py) in this image is point "
                f"(x+px/{scale:g}, y+py/{scale:g})")
        self.log(line)
        return line

    def observe(self, note: str = "") -> str:
        """A fresh screenshot plus the context line."""
        self.display(self.screenshot())
        line = self.context_line()
        if note:
            line += f" — {note}"
        self.log(line)
        return line

    # -- used by the worker --
    def screenshot(self) -> Any:
        """The screen in points, like pyautogui.screenshot() but from a CG capture."""
        return self._points(self._capture())

    def settled_screenshot(self, max_wait: float) -> Any:
        """Wait (up to max_wait) for the screen to stop changing, then the screenshot of that last frame."""
        if max_wait > 0:
            self._settle(max_wait)
        else:
            self._capture()
        assert self._frame is not None
        return self._points(self._frame)

    def context_line(self) -> str:
        front = self._frontmost_fn()
        w_pts, h_pts = self._size()
        return (f"frontmost: {front.get('app', '')} — {front.get('title', '')!r}; "
                f"screen {w_pts}x{h_pts}; {self._now():%H:%M}")

    def after_line(self) -> str:
        """What changed since the previous exec ended (compares 1/8-scale thumbnails)."""
        thumb = self._capture().thumb()
        changed = self._last_thumb is not None and changed_fraction(self._last_thumb, thumb) >= CHANGE_THRESHOLD
        self._last_thumb = thumb
        front = self._frontmost_fn()
        tail = f"; clicked on {self._last_click}" if self._last_click else ""
        return (f"[after] frontmost: {front.get('app', '')} — {front.get('title', '')!r}; "
                f"screen: {'changed' if changed else 'unchanged'}{tail}")

    # -- internals --
    def _capture(self) -> Frame:
        self._frame = self._capture_fn()
        return self._frame

    def _size(self) -> tuple[int, int]:
        w, h = self.gui.size()
        return int(w), int(h)

    def _points(self, frame: Frame) -> Any:
        from PIL import Image

        img = frame.pil()
        size = self._size()
        if tuple(img.size) != size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        return img
