"""buddy makes apps: "make me a habit tracker" becomes a small web app that opens inside Telegram.

Owner, 2026-09-24: "mini apps are to replace websites, like i want to build a habit tracker, it should do it".
Claude (``claude-opus-5-5``) writes one self-contained HTML file for the request; it is kept on the Mac under
``~/.config/cc-buddy-bridge/apps/<slug>/`` and served by the Mini App server (miniapp.py) at ``/apps/<slug>/``,
so it opens from a button in the chat or from the Mini App's home screen. "Add streaks to the habit tracker"
edits the same app: Claude gets the current file and returns the whole new one.

An app keeps its data through ``window.buddy`` (``/buddy.js``, injected into every app): ``await buddy.load()``
returns what it saved last (or null) and ``await buddy.save(obj)`` keeps a JSON value, on the Mac, in
``data.json`` beside the app. Every load and save carries Telegram's signed initData, checked like every other
Mini App call, so an app's data is the owner's only. The previous version of an edited app is kept as
``index.prev.html``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / ".config" / "cc-buddy-bridge" / "apps"
MAX_DATA_BYTES = 1_000_000
MAX_APP_BYTES = 600_000
MAKE_MAX_TOKENS = 64000
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

MAKER_PROMPT = """You build small, polished web apps that open inside Telegram as Mini Apps on the owner's phone.

Return exactly one complete HTML document in a single ```html fenced block, and nothing else after it. One
self-contained file: all CSS and JavaScript inline. No external scripts, stylesheets, fonts or images, with one
exception: the page may load libraries from https://cdn.jsdelivr.net/npm/ when a chart or similar truly needs one.
Put a short, human title in <title> (for example "Habit Tracker").

The environment:
- The page runs in Telegram's in-app browser. `window.Telegram.WebApp` is available; call
  `Telegram.WebApp.ready()` and `Telegram.WebApp.expand()` at start. Telegram's theme colors are CSS variables:
  --tg-theme-bg-color, --tg-theme-text-color, --tg-theme-hint-color, --tg-theme-link-color,
  --tg-theme-button-color, --tg-theme-button-text-color, --tg-theme-secondary-bg-color. Use them, with sensible
  fallbacks, so the app matches light and dark mode.
- Saving: `window.buddy` is provided. `await buddy.load()` returns the value saved last, or null the first time.
  `await buddy.save(value)` keeps any JSON value (up to about 1 MB). Load once at start, keep state in memory,
  and save after every change. Never use localStorage for the app's real data. If load or save throws, show a
  short, friendly message and keep working in memory.
- Design for a phone held in one hand: large tap targets (at least 44px), 16px side padding, readable type, no
  horizontal scrolling, and no hover-only controls. Make it feel like a real app: clear empty states, quick
  entry, and immediate visual feedback.
- Dates use the phone's local time zone.

Build exactly what was asked, complete and working, with no placeholders. When asked to change an existing
app, return the whole updated file, keep its saved data format compatible (migrate old data in code if the
format has to change), and keep everything the owner did not ask to change."""


@dataclass
class AppInfo:
    slug: str
    title: str
    created: str
    updated: str

    def as_dict(self) -> dict[str, str]:
        return {"slug": self.slug, "title": self.title, "created": self.created, "updated": self.updated}


def slugify(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return ("-".join(words) or "app")[:40].strip("-") or "app"


def title_of(html: str, fallback: str = "App") -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    t = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return (t or fallback)[:60]


def extract_html(text: str) -> Optional[str]:
    """The HTML document in Claude's answer: the ```html block, else the whole answer if it is a document."""
    m = re.search(r"```html\s*\n(.*?)```", text, re.S | re.I) or re.search(r"```\s*\n(<!doctype.*?)```", text, re.S | re.I)
    html = (m.group(1) if m else text).strip()
    low = html.lower()
    if ("<!doctype html" in low or "<html" in low) and "</html>" in low and len(html.encode()) <= MAX_APP_BYTES:
        return html
    return None


class AppStore:
    """The owner's apps on disk: ``<root>/<slug>/index.html``, ``meta.json`` and ``data.json``."""

    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def _dir(self, slug: str) -> Optional[Path]:
        return self.root / slug if SLUG_RE.match(slug or "") else None

    def list(self) -> list[AppInfo]:
        apps = []
        for meta in sorted(self.root.glob("*/meta.json")):
            info = self.info(meta.parent.name)
            if info is not None:
                apps.append(info)
        return sorted(apps, key=lambda a: a.updated, reverse=True)

    def info(self, slug: str) -> Optional[AppInfo]:
        d = self._dir(slug)
        if d is None:
            return None
        try:
            m = json.loads((d / "meta.json").read_text())
            return AppInfo(slug, str(m["title"]), str(m["created"]), str(m["updated"]))
        except (OSError, ValueError, KeyError):
            return None

    def html(self, slug: str) -> Optional[str]:
        d = self._dir(slug)
        try:
            return (d / "index.html").read_text() if d is not None else None
        except OSError:
            return None

    def find(self, name: str) -> Optional[AppInfo]:
        """The app a request names ("the habit tracker", "habit-tracker"): exact slug, else best word overlap."""
        apps = self.list()
        s = slugify(name)
        for a in apps:
            if a.slug == s:
                return a
        words = set(re.findall(r"[a-z0-9]+", name.lower())) - {"the", "my", "a", "app", "an"}
        scored = [(len(words & set(re.findall(r"[a-z0-9]+", (a.title + " " + a.slug).lower()))), a) for a in apps]
        scored = [x for x in scored if x[0] > 0]
        return max(scored, key=lambda x: x[0])[1] if scored else None

    def save(self, html: str, request: str, slug: str = "") -> AppInfo:
        now = _dt.datetime.now().isoformat(timespec="seconds")
        title = title_of(html, fallback=request[:40] or "App")
        if not slug:
            base = slugify(title)
            slug, n = base, 2
            while (self.root / slug).exists():
                slug, n = f"{base}-{n}", n + 1
        d = self._dir(slug)
        if d is None:
            raise ValueError("bad app name")
        d.mkdir(parents=True, exist_ok=True)
        old = self.info(slug)
        if (d / "index.html").exists():
            (d / "index.html").replace(d / "index.prev.html")
        (d / "index.html").write_text(html)
        (d / "meta.json").write_text(json.dumps({"title": title, "created": old.created if old else now,
                                                 "updated": now, "request": request[:500]}))
        return AppInfo(slug, title, old.created if old else now, now)

    def load_data(self, slug: str) -> Any:
        d = self._dir(slug)
        if d is None or not (d / "index.html").exists():
            raise KeyError(slug)
        try:
            return json.loads((d / "data.json").read_text())
        except (OSError, ValueError):
            return None

    def save_data(self, slug: str, value: Any) -> int:
        d = self._dir(slug)
        if d is None or not (d / "index.html").exists():
            raise KeyError(slug)
        raw = json.dumps(value)
        if len(raw.encode()) > MAX_DATA_BYTES:
            raise ValueError("too large")
        tmp = d / "data.tmp"
        tmp.write_text(raw)
        tmp.replace(d / "data.json")
        return len(raw)


# ---- Claude writes the app ------------------------------------------------------------------------

@dataclass
class Made:
    ok: bool
    app: Optional[AppInfo] = None
    reason: str = ""
    usd: float = 0.0
    secs: float = 0.0


Generate = Callable[[str, Callable[[int], None]], Awaitable[tuple[str, Any, str]]]


def claude_generate(model: str, effort: str, client: Any = None) -> Generate:
    """(answer text, usage, stop reason) for one maker prompt; ``progress(chars so far)`` as it streams."""
    import anthropic

    api = client or anthropic.AsyncAnthropic()

    async def generate(prompt: str, progress: Callable[[int], None]) -> tuple[str, Any, str]:
        parts: list[str] = []
        async with api.messages.stream(model=model, max_tokens=MAKE_MAX_TOKENS, system=MAKER_PROMPT,
                                       messages=[{"role": "user", "content": prompt}],
                                       thinking={"type": "adaptive"}, output_config={"effort": effort}) as s:
            async for text in s.text_stream:
                parts.append(text)
                progress(sum(len(p) for p in parts))
            final = await s.get_final_message()
        return "".join(parts), final.usage, str(final.stop_reason or "")

    return generate


class AppMaker:
    def __init__(self, store: AppStore, generate: Generate, *, cost: Callable[[Any], float],
                 spend: Callable[[float], Any], left: Callable[[], float]) -> None:
        self.store, self._generate = store, generate
        self._cost, self._spend, self._left = cost, spend, left

    async def make(self, request: str, progress: Callable[[int], None] = lambda n: None) -> Made:
        return await self._run(f"Build this app: {request.strip()}", request, "", progress)

    async def edit(self, name: str, change: str, progress: Callable[[int], None] = lambda n: None) -> Made:
        app = self.store.find(name)
        if app is None:
            return Made(False, reason=f"there is no app called {name!r}")
        html = self.store.html(app.slug) or ""
        prompt = (f"Here is the current app ({app.title}):\n\n```html\n{html}\n```\n\n"
                  f"Change it as asked: {change.strip()}")
        return await self._run(prompt, change, app.slug, progress)

    async def _run(self, prompt: str, request: str, slug: str, progress: Callable[[int], None]) -> Made:
        if self._left() <= 0:
            return Made(False, reason="today's Claude budget is used up")
        t0 = time.perf_counter()
        text, usage, stop = await self._generate(prompt, progress)
        usd = self._cost(usage) if usage is not None else 0.0
        if usage is not None:
            self._spend(usd)
        secs = time.perf_counter() - t0
        if stop == "refusal":
            return Made(False, reason="Claude declined to build that", usd=usd, secs=secs)
        html = extract_html(text)
        if html is None:
            why = "the app was too long to finish" if stop == "max_tokens" else "Claude did not return a complete app"
            return Made(False, reason=why, usd=usd, secs=secs)
        app = self.store.save(html, request, slug)
        log.info("apps: %s %s in %.0f s, $%.3f", "edited" if slug else "made", app.slug, secs, usd)
        return Made(True, app=app, usd=usd, secs=secs)


# ---- window.buddy, injected into every app --------------------------------------------------------

BUDDY_JS = """(() => {
  const slug = location.pathname.split("/").filter(Boolean)[1] || "";
  const initData = () => (window.Telegram && Telegram.WebApp && Telegram.WebApp.initData) || "";
  async function call(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ initData: initData() }, body || {})) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }
  window.buddy = {
    slug,
    load: async () => (await call("/api/apps/" + slug + "/load")).data ?? null,
    save: async (value) => { await call("/api/apps/" + slug + "/save", { data: value }); return true; },
  };
})();
"""


def serve_app_html(html: str) -> str:
    """The app as served: Telegram's script and window.buddy put in the head, ahead of the app's own code."""
    inject = ""
    if "telegram-web-app.js" not in html:
        inject += '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
    inject += '<script src="/buddy.js"></script>'
    m = re.search(r"<head[^>]*>", html, re.I)
    return html[:m.end()] + inject + html[m.end():] if m else inject + html


# ---- the chat door: "make me a habit tracker" texted to buddy ---------------------------------------

MAKER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function", "name": "make_app", "strict": True,
        "description": "Build a new small app (a tracker, a log, a list, a calculator, a planner…) that opens inside "
                       "Telegram and saves its data. Use it whenever the owner asks for an app, tracker or tool "
                       "to be made. Takes about a minute; an Open button is sent to the chat when it is ready.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["request"],
                       "properties": {"request": {"type": "string", "description":
                                                  "What the app should do, in the owner's words plus any detail "
                                                  "they gave."}}},
    },
    {
        "type": "function", "name": "change_app", "strict": True,
        "description": "Change one of the owner's existing apps (add a feature, fix something, restyle it). Its "
                       "saved data is kept. An Open button is sent when the new version is ready.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["app", "change"],
                       "properties": {"app": {"type": "string", "description": "Which app, by name."},
                                      "change": {"type": "string", "description": "What to change."}}},
    },
    {
        "type": "function", "name": "list_apps", "strict": True,
        "description": "The apps buddy has made for the owner, and a button to open each.",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
    },
]
MAKER_TOOL_NAMES = frozenset(t["name"] for t in MAKER_TOOLS)

SendButtons = Callable[[int, str, list[tuple[str, str]]], Awaitable[Any]]


class ChatMaker:
    """The Telegram brain's make_app / change_app / list_apps, over a live Mini App (miniapp.MiniApp).

    A build runs in the background like a Mac task: the tool returns at once, and the Open button (an inline
    ``web_app`` button, so it opens as a Mini App with the owner's signed initData) is sent when it is done."""

    def __init__(self, app: Any, spawn: Callable[[Awaitable[Any], str], Any]) -> None:
        self._app, self._spawn = app, spawn          # miniapp.MiniApp: .maker, .store, .app_url(slug), .url
        self.building: set[str] = set()

    @property
    def live(self) -> bool:
        return bool(getattr(self._app, "url", ""))

    def tools(self) -> list[dict[str, Any]]:
        return list(MAKER_TOOLS) if self.live else []

    def home_url(self) -> str:
        """The Mini App's home screen right now ("" while the tunnel is down)."""
        return f"{self._app.url}/" if self.live else ""

    async def handle(self, name: str, args: dict[str, Any], chat_id: int, send: SendButtons,
                     say: Callable[[int, str], Awaitable[Any]]) -> dict[str, Any]:
        if not self.live:
            return {"ok": False, "reason": "the Mini App is not running right now"}
        if name == "list_apps":
            apps = self._app.store.list()
            if not apps:
                return {"ok": True, "apps": [], "note": "no apps yet"}
            await send(chat_id, "Your apps:", [(a.title, self._app.app_url(a.slug)) for a in apps[:8]])
            return {"ok": True, "apps": [a.title for a in apps], "sent": "the buttons are in the chat already"}
        request = str(args.get("request") or args.get("change") or "").strip()
        target = str(args.get("app") or "").strip() if name == "change_app" else ""
        if not request:
            return {"ok": False, "reason": "say what the app should do"}
        if target and self._app.store.find(target) is None:
            names = ", ".join(a.title for a in self._app.store.list()) or "none yet"
            return {"ok": False, "reason": f"no app called {target!r}; the apps are: {names}"}
        key = target or request
        if key in self.building:
            return {"ok": False, "reason": "that one is already being built"}
        self.building.add(key)
        self._spawn(self._build(chat_id, request, target, key, send, say), "apps-build")
        return {"ok": True, "sent": "building now (about a minute); the Open button is sent by itself when it is "
                                    "ready — say only a word or two"}

    async def _build(self, chat_id: int, request: str, target: str, key: str, send: SendButtons,
                     say: Callable[[int, str], Awaitable[Any]]) -> None:
        try:
            made = await (self._app.maker.edit(target, request) if target else self._app.maker.make(request))
        except Exception as e:  # noqa: BLE001 — a failed build is a line in the chat
            log.warning("apps: building from the chat failed (%s)", type(e).__name__)
            await say(chat_id, "I couldn't build that app: something went wrong. Try again?")
            return
        finally:
            self.building.discard(key)
        if not made.ok or made.app is None:
            await say(chat_id, f"I couldn't build that app: {made.reason}.")
            return
        url = self._app.app_url(made.app.slug)
        if not url:
            await say(chat_id, f"{made.app.title} is ready. Open it from the Apps tab on the menu button.")
            return
        verb = "updated" if target else "ready"
        await send(chat_id, f"{made.app.title} is {verb}.", [(f"Open {made.app.title}", url)])
