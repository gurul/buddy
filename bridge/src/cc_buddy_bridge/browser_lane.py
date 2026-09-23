"""The browser lane: Playwright drives a web task, the plan executor and Jev decide each step.

The fast lane's ceiling on the Mac is the Accessibility tree over web content: nameless buttons, stale
frames, coordinate clicks (Jev's holdout on the AX fixtures: 70.8 %, docs/stackchan/routing.md). A browser
driven through Playwright has none of that. Its page gives every control a role and an accessible name,
a click lands on the element and not on a point, and "did it work" is a DOM question with an exact answer
(the URL, the title, the text that is now on the page). Measured on this Mac, 2026-09-21: Chromium up in
0.19 s, a page in 0.52 s, a snapshot of every control in 40 ms.

This module changes the eyes and hands, not the brain. It presents a page as the same ``Snapshot`` of
``Candidate``s that ax_candidates.py builds from a window, so ``plan_executor.run_plan`` — astra's one
plan, the keyword gate, Jev's typed step answer, the sensitive-label table, "only the human approves" —
runs unchanged on top of it. Nothing about approval is reimplemented here.

    goal ── is it a web task? (WEB_WORDS, a URL, the router's search/open_url) ──▶ BrowserLane.run
                                                                                   │ outline of the page
                                                                                   ▼
                                                  astra plans once (plan_contract.plan_request)
                                                                                   │ Plan
                                                                                   ▼
                              plan_executor.run_plan(senses=PageSenses, effectors=PageEffectors, asker=Jev)
                                                                                   │ per step: candidates from
                                                                                   │ the DOM, gate + Jev pick,
                                                                                   ▼ Playwright clicks, expect
                                                            complete → one sentence · partial → today's loop

The browser is buddy's own: a persistent Chromium profile under ``~/.config/cc-buddy-bridge/browser``,
headed so the owner (and the phone's screenshot) sees it, signed in once by the owner.

ATTACH MODE (owner, 2026-09-23: "i want buddy to be able to control logged in browser, that's the most
important"). With ``CC_BUDDY_BROWSER_ATTACH=1`` the lane drives the owner's own running Chrome, signed in
to everything, instead of its own profile. Chrome 136+ ignores ``--remote-debugging-port`` on the default
profile; the supported route (Chrome 144+) is the owner switching on remote debugging once at
``chrome://inspect/#remote-debugging``, after which Chrome writes ``DevToolsActivePort`` (port, then the
browser's WebSocket path) into its user data directory. ``devtools_endpoint`` reads that file exactly as
Google's chrome-devtools-mcp ``--autoConnect`` does, and Playwright's ``connect_over_cdp`` attaches.
In attach mode the lane works in a NEW TAB of its own — never one of the owner's — and closing it closes
that tab and disconnects: the owner's browser, windows and tabs are never closed. The same gate applies as
everywhere: a sensitive control (buy, send, delete, pay…) stops the plan for the owner's yes.

Playwright's sync API must stay on the thread that created it, so the lane owns one worker thread and
everything runs there; the daemon awaits it. It ships OFF (``BROWSER_LANE_DEFAULT``): its evaluation set
(recorded page snapshots, tools/browser_lane_eval.py) does not exist yet.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .ax_candidates import Candidate, Snapshot, is_sensitive

log = logging.getLogger(__name__)

BROWSER_LANE_DEFAULT = False
DEFAULT_PROFILE = "~/.config/cc-buddy-bridge/browser"
DEFAULT_CHROME_DIR = "~/Library/Application Support/Google/Chrome"   # the owner's Chrome user data directory
DEFAULT_VIEWPORT = (1280, 860)
MAX_CANDIDATES = 120
SNAPSHOT_TIMEOUT_MS = 3000
NAV_TIMEOUT_MS = 15000
SETTLE_CAP_SECS = 3.0
KEYS = {"return": "Enter", "enter": "Enter", "escape": "Escape", "tab": "Tab", "space": " ", "up": "ArrowUp", "down": "ArrowDown",
        "left": "ArrowLeft", "right": "ArrowRight", "delete": "Backspace"}

# A goal for the web: a URL, a site, or the browser named. Anything else stays on the Mac lane.
WEB_WORDS = re.compile(
    r"(https?://|www\.|\.(com|org|net|io|ai|gov|edu)\b|\b(browser|chrome|safari|website|web ?page|webpage|"
    r"google|youtube|amazon|github|wikipedia|reddit|twitter|linkedin|gmail|news|hacker ?news|bbc|nytimes|"
    r"stack ?overflow|search (the )?web|look up|google it)\b)", re.I)

# What the page's JavaScript is asked for: every visible interactive element, with its accessible name,
# role, state and box. One evaluate, ~1 ms on a busy page; no screenshots, no OCR.
_COLLECT_JS = """() => {
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab],[role=menuitem],' +
              '[role=checkbox],[role=radio],[role=textbox],[role=combobox],[role=option],[role=switch],' +
              '[role=searchbox],[role=menuitemcheckbox],[role=menuitemradio],[contenteditable=true]';
  const roleOf = (e) => {
    const r = e.getAttribute('role'); if (r) return r;
    const t = e.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'input') { const ty = (e.type || 'text').toLowerCase();
      if (ty === 'submit' || ty === 'button' || ty === 'reset') return 'button';
      if (ty === 'checkbox') return 'checkbox'; if (ty === 'radio') return 'radio';
      if (ty === 'search') return 'searchbox'; if (ty === 'password') return 'password'; return 'textbox'; }
    if (t === 'textarea') return 'textbox'; if (t === 'select') return 'combobox';
    if (t === 'summary') return 'button'; if (e.isContentEditable) return 'textbox';
    return t; };
  const nameOf = (e) => {
    const lab = e.getAttribute('aria-label'); if (lab) return lab;
    const by = e.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ').trim(); if (t) return t; }
    if (e.labels && e.labels.length) { const t = Array.from(e.labels).map(l => l.innerText).join(' ').trim(); if (t) return t; }
    const own = (e.innerText || e.value || e.placeholder || e.title || e.getAttribute('alt') || '').trim();
    if (own) return own;
    const img = e.querySelector('img[alt],svg[aria-label],[aria-label]');
    return img ? (img.getAttribute('alt') || img.getAttribute('aria-label') || '').trim() : ''; };
  const out = []; const H = window.innerHeight, W = window.innerWidth; let i = 0;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > H || r.right < 0 || r.left > W) continue;
    const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const role = roleOf(e); const name = nameOf(e).replace(/\\s+/g, ' ').slice(0, 120);
    let value = '';
    if (role === 'checkbox' || role === 'switch' || role === 'menuitemcheckbox') value = (e.checked || e.getAttribute('aria-checked') === 'true') ? 'checked' : 'unchecked';
    else if (role === 'radio' || role === 'menuitemradio' || role === 'tab' || role === 'option') value = (e.checked || e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-selected') === 'true') ? 'selected' : '';
    else if (e.getAttribute('aria-expanded') !== null) value = e.getAttribute('aria-expanded') === 'true' ? 'expanded' : 'collapsed';
    else if ((role === 'textbox' || role === 'searchbox' || role === 'combobox') && typeof e.value === 'string') value = e.value.slice(0, 40);
    const editable = ['textbox', 'searchbox', 'combobox'].includes(role) && !(e.tagName.toLowerCase() === 'select');
    e.setAttribute('data-buddy-id', String(i));
    out.push({id: String(i++), role, name, value, x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width),
              h: Math.round(r.height), enabled: !(e.disabled || e.getAttribute('aria-disabled') === 'true'),
              secure: role === 'password', editable, dialog: !!e.closest('dialog,[role=dialog],[role=alertdialog]'),
              focused: document.activeElement === e});
    if (out.length >= %d) break; }
  const dlg = document.querySelector('dialog[open],[role=dialog],[role=alertdialog]');
  const focused = document.activeElement;
  return {elements: out, title: document.title, url: location.href,
          dialog: dlg ? (dlg.innerText || '').trim().slice(0, 80) : '',
          focused_id: focused && focused.getAttribute ? focused.getAttribute('data-buddy-id') : null,
          text_hash: (document.body ? document.body.innerText : '').length + ':' + document.body?.innerText?.slice(0, 4000)}; }
""" % MAX_CANDIDATES

ROLE_WORDS = {"link": "link", "button": "button", "checkbox": "checkbox", "radio": "radio button",
              "searchbox": "search field", "textbox": "text field", "password": "secure text field",
              "combobox": "combo box", "tab": "tab", "menuitem": "menu item", "option": "option",
              "switch": "switch", "menuitemcheckbox": "checkbox", "menuitemradio": "radio button"}


@dataclass(frozen=True)
class BrowserLaneConfig:
    enabled: bool = BROWSER_LANE_DEFAULT
    profile: Path = Path(DEFAULT_PROFILE).expanduser()
    headless: bool = False
    attach: bool = False                          # drive the owner's running Chrome (see ATTACH MODE)
    chrome_dir: Path = Path(DEFAULT_CHROME_DIR).expanduser()


def configured(environ: Any = None) -> BrowserLaneConfig:
    """``CC_BUDDY_BROWSER_LANE=1`` turns it on; ``CC_BUDDY_BROWSER_PROFILE`` moves the profile;
    ``CC_BUDDY_BROWSER_HEADLESS=1`` hides the window (tests and benches — the owner should see it);
    ``CC_BUDDY_BROWSER_ATTACH=1`` drives the owner's running Chrome instead (``CC_BUDDY_CHROME_DIR`` moves
    where its ``DevToolsActivePort`` is looked for)."""
    env = os.environ if environ is None else environ
    switch = (env.get("CC_BUDDY_BROWSER_LANE") or ("1" if BROWSER_LANE_DEFAULT else "0")).strip().lower()
    profile = Path((env.get("CC_BUDDY_BROWSER_PROFILE") or "").strip() or DEFAULT_PROFILE).expanduser()
    headless = (env.get("CC_BUDDY_BROWSER_HEADLESS") or "0").strip().lower() in ("1", "true", "yes", "on")
    attach = (env.get("CC_BUDDY_BROWSER_ATTACH") or "0").strip().lower() in ("1", "true", "yes", "on")
    chrome_dir = Path((env.get("CC_BUDDY_CHROME_DIR") or "").strip() or DEFAULT_CHROME_DIR).expanduser()
    return BrowserLaneConfig(enabled=switch in ("1", "true", "yes", "on"), profile=profile, headless=headless,
                             attach=attach, chrome_dir=chrome_dir)


def devtools_endpoint(chrome_dir: Path) -> Optional[str]:
    """The running Chrome's browser WebSocket, from ``<chrome_dir>/DevToolsActivePort`` (a port line, then a
    path line), or None when the file is missing or malformed — remote debugging is off, or Chrome is not
    running. The same reading as chrome-devtools-mcp's autoConnect."""
    try:
        lines = [ln.strip() for ln in (Path(chrome_dir) / "DevToolsActivePort").read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    if len(lines) < 2 or not lines[0].isdigit() or not lines[1].startswith("/devtools/browser/"):
        return None
    port = int(lines[0])
    return f"ws://127.0.0.1:{port}{lines[1]}" if 0 < port < 65536 else None


class AttachError(RuntimeError):
    """The owner's Chrome cannot be reached: remote debugging is off, or Chrome is not running."""


def is_web_goal(goal: str, route_kind: str = "") -> bool:
    """Code decides which lane sees a goal; the browser gets a URL, a site, or a named browser."""
    return route_kind in ("open_url", "search") or bool(WEB_WORDS.search(goal or ""))


def snapshot_from_page(raw: Mapping[str, Any], seq: int, secs: float = 0.0) -> Snapshot:
    """The page's collected elements as the Snapshot the gate and Jev already read."""
    elements: list[Candidate] = []
    focused: Optional[Candidate] = None
    for e in raw.get("elements") or []:
        role = ROLE_WORDS.get(str(e.get("role") or ""), str(e.get("role") or "control"))
        c = Candidate(id=str(e["id"]), role=role, label=str(e.get("name") or ""), value=str(e.get("value") or ""),
                      frame=(int(e.get("x", 0)), int(e.get("y", 0)), int(e.get("w", 0)), int(e.get("h", 0))),
                      # AXPress so every role is pressable to the gate; in_content False because on the Mac
                      # lane that flag means "web content the AX walk cannot click reliably" — here the page
                      # is the whole app and every control is a first-class one.
                      actions=("AXPress",), enabled=bool(e.get("enabled", True)), app="browser",
                      in_content=False, in_dialog=bool(e.get("dialog")), secure=bool(e.get("secure")),
                      editable=bool(e.get("editable")))
        elements.append(c)
        if e.get("focused"):
            focused = c
    elements.sort(key=lambda c: (c.frame[1] // 8, c.frame[0]))
    title = str(raw.get("title") or "")
    context = tuple(x for x in (f"title: {title}" if title else "", f"url: {str(raw.get('url') or '')[:120]}") if x)
    return Snapshot(seq=seq, app="browser", bundle="buddy.browser", title=title, pid=1, elements=tuple(elements),
                    context_lines=context, focused=focused, dialog_text=str(raw.get("dialog") or ""),
                    node_count=len(elements), truncated=len(elements) >= MAX_CANDIDATES, secs=secs)


def outline_lines(snap: Snapshot, limit: int = 80) -> list[str]:
    """What the planner is shown: roles and labels, never field values, never the title."""
    lines: list[str] = []
    for c in snap.elements:
        if not c.label or c.secure:
            continue
        state = f", {c.value}" if c.value in ("selected", "checked", "unchecked", "expanded", "collapsed") else ""
        lines.append(f"{c.role}: {c.label[:60]}{state}")
        if len(lines) >= limit:
            break
    return lines


class _Page:
    """Senses and effectors over one Playwright page, on the lane's thread. The methods and their return
    strings are the contract desktop_helpers' adapter keeps, because run_plan and run_delegate read them."""

    def __init__(self, page: Any, clock: Callable[[], float] = time.perf_counter) -> None:
        self.page = page
        self.clock = clock
        self.approve: Optional[str] = None
        self._seq = 0
        self._snapshot: Optional[Snapshot] = None
        self._hash_before = ""
        self._hash0 = ""

    # -- senses --
    def _collect(self) -> dict[str, Any]:
        raw = self.page.evaluate(_COLLECT_JS)
        return raw if isinstance(raw, dict) else {"elements": []}

    def snapshot(self) -> Snapshot:
        self._seq += 1
        t0 = self.clock()
        raw = self._collect()
        snap = snapshot_from_page(raw, self._seq, self.clock() - t0)
        self._snapshot = snap
        # What "changed" means here: the URL, the title, the page text, or any control's state or label.
        # A toggled checkbox changes no text, so the controls are in the hash too.
        controls = [(e.get("role"), e.get("name"), e.get("value")) for e in raw.get("elements") or []]
        digest = hashlib.sha1(json.dumps([raw.get("url"), raw.get("title"), raw.get("text_hash"), controls]).encode())
        self._hash_before, self._hash0 = self._hash0, digest.hexdigest()
        return snap

    def text_visible(self, s: str) -> bool:
        needle = " ".join(str(s).split()).casefold()
        if not needle:
            return False
        try:
            # The page text, the title, and what is typed into its fields (innerText has no field values).
            body = self.page.evaluate("() => (document.body ? document.body.innerText : '') + ' ' + "
                                      "Array.from(document.querySelectorAll('input,textarea')).map(e => e.value || '').join(' ')") or ""
            if needle in " ".join(str(body).split()).casefold():
                return True
            return needle in " ".join(str(self.page.title()).split()).casefold()
        except Exception:  # noqa: BLE001 — a page mid-navigation has no text yet
            return False

    def focused(self) -> Optional[Candidate]:
        snap = self._snapshot if self._snapshot is not None else self.snapshot()
        return snap.focused

    def frontmost_pid(self) -> int:
        return 1                                           # one page; the executor's focus check never fires

    def screen_changed(self) -> Optional[bool]:
        if not self._hash_before or not self._hash0:
            return None
        return self._hash_before != self._hash0

    # -- effectors --
    def _locator(self, c: Candidate) -> Any:
        return self.page.locator(f'[data-buddy-id="{c.id}"]').first

    def click_candidate(self, c: Candidate) -> str:
        norm = " ".join(c.label.split()).casefold()
        if is_sensitive(c.label) and (self.approve or "").casefold() != norm:
            return f"refused: {c.label!r} is a sensitive control without approval"
        loc = self._locator(c)
        try:
            if loc.count() == 0:
                return f"refused: {c.label!r} is no longer on the page"
            loc.scroll_into_view_if_needed(timeout=SNAPSHOT_TIMEOUT_MS)
            loc.click(timeout=SNAPSHOT_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001 — covered, detached, navigated away: nothing was clicked
            return f"refused: could not click {c.label!r} ({type(e).__name__})"
        x, y, w, h = c.frame
        return f"clicked {c.role} {c.label!r} at ({x + w // 2},{y + h // 2})"

    def focus_and_type(self, c: Candidate, text: str) -> str:
        if c.secure:
            return f"refused: {c.label!r} is a secure field"
        loc = self._locator(c)
        try:
            if loc.count() == 0:
                return f"refused: {c.label!r} is no longer on the page"
            loc.click(timeout=SNAPSHOT_TIMEOUT_MS)
            focused = self.page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-buddy-id')")
            if str(focused) != c.id:
                return f"refused: focus is not on the {c.role} {c.label!r}"
            self.page.keyboard.type(text, delay=5)
        except Exception as e:  # noqa: BLE001
            return f"refused: could not type into {c.label!r} ({type(e).__name__})"
        return f"typed {len(text)} characters into {c.role} {c.label!r}"

    def press(self, key: str) -> str:
        name = KEYS.get(str(key).lower())
        if name is None:
            raise ValueError(f"not a key: {key!r}")
        self.page.keyboard.press(name)
        return f"pressed {key}"

    def settle(self, secs: float) -> float:
        t0 = self.clock()
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=int(min(secs, SETTLE_CAP_SECS) * 1000))
            self.page.wait_for_load_state("networkidle", timeout=int(min(secs, SETTLE_CAP_SECS) * 1000))
        except Exception:  # noqa: BLE001 — a page that never goes idle is still settled enough
            pass
        return self.clock() - t0

    def open_url(self, url: str) -> str:
        if not re.match(r"^(https?|file)://", url, re.I):
            url = "https://" + url
        self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        return f"opened {url}"


class BrowserLane:
    """Owns the browser and its thread. Every public coroutine hands one job to that thread."""

    def __init__(self, config: BrowserLaneConfig, *, step_asker: Any = None,
                 launcher: Optional[Callable[[], Any]] = None) -> None:
        self.config = config
        self._step_asker = step_asker
        self._launcher = launcher
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-lane")
        self._pw: Any = None
        self._context: Any = None
        self._page: Optional[_Page] = None
        self._plain_http: dict[str, str] = {}          # tests only: an https URL the plan contract insists on → a loopback http one
        self._browser: Any = None                         # attach mode: the owner's Chrome, connected over CDP

    # -- the thread --
    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def _ensure(self) -> _Page:
        if self._page is not None:
            try:
                self._page.page.title()               # alive?
                return self._page
            except Exception:  # noqa: BLE001 — the owner closed the window: open a fresh one
                self._page = None
        if self._context is None and self.config.attach and self._launcher is None:
            endpoint = devtools_endpoint(self.config.chrome_dir)
            if endpoint is None:
                raise AttachError("Chrome's remote debugging is off (or Chrome is not running): open "
                                  "chrome://inspect/#remote-debugging in Chrome and switch it on")
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(endpoint)
            self._context = self._browser.contexts[0]        # the owner's profile: their cookies and logins
            page = self._context.new_page()                   # buddy's own tab; the owner's tabs are never touched
            page.bring_to_front()
            self._page = _Page(page)
            return self._page
        if self._context is None:
            if self._launcher is not None:
                self._context = self._launcher()
            else:
                from playwright.sync_api import sync_playwright

                self._pw = sync_playwright().start()
                self.config.profile.mkdir(parents=True, exist_ok=True)
                w, h = DEFAULT_VIEWPORT
                self._context = self._pw.chromium.launch_persistent_context(
                    str(self.config.profile), headless=self.config.headless, viewport={"width": w, "height": h},
                    args=["--disable-blink-features=AutomationControlled"])
        pages = list(self._context.pages)
        page = pages[0] if pages else self._context.new_page()
        page.bring_to_front()
        self._page = _Page(page)
        return self._page

    def _close(self) -> None:
        if self._browser is not None:
            # Attach mode: close buddy's own tab and disconnect. Never the context or the browser — they are
            # the owner's Chrome, with every window and tab they have open.
            try:
                if self._page is not None:
                    self._page.page.close()
            except Exception:  # noqa: BLE001 — the owner may have closed it already
                pass
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._browser = self._context = self._pw = None
            self._page = None
            return
        for obj in (self._context, self._pw):
            try:
                if obj is not None:
                    obj.close() if obj is self._context else obj.stop()
            except Exception:  # noqa: BLE001
                pass
        self._context = self._pw = None
        self._page = None

    async def close(self) -> None:
        await self._run(self._close)
        self._pool.shutdown(wait=False)

    # -- what the agent calls --
    def _outline(self) -> dict[str, Any]:
        p = self._ensure()
        snap = p.snapshot()
        return {"app": "browser", "lines": outline_lines(snap), "title": snap.title,
                "url": next((ln[5:] for ln in snap.context_lines if ln.startswith("url: ")), "")}

    async def outline(self) -> dict[str, Any]:
        return await self._run(self._outline)

    def _run_plan(self, plan_dict: dict[str, Any], goal: str, start: int, approved: Mapping[str, str]) -> dict[str, Any]:
        from . import plan_contract as pc
        from .plan_executor import run_plan

        p = self._ensure()
        plan = pc.parse_plan(plan_dict, goal, source=str(plan_dict.get("source") or "astra"))
        ok = {int(k): v for k, v in (approved or {}).items()}
        p.approve = next(iter(ok.values()), None)

        def open_app(name: str) -> str:
            if name.casefold() in ("browser", "chrome", "chromium", "google chrome", "safari"):
                return f"opened {name}"                    # the browser is already the whole world here
            raise ValueError(f"{name} is not the browser: this plan belongs on the Mac lane")

        result = run_plan(plan, senses=p, effectors=p, asker=self._step_asker, open_app=open_app,
                          open_url=lambda url: p.open_url(self._plain_http.get(url, url)), frontmost_app=lambda: "browser", start=start, approved=ok,
                          decide="jev" if self._step_asker is not None else "keyword", planned_for="browser")
        return result.to_dict()

    async def run_plan(self, plan_dict: dict[str, Any], goal: str, start: int = 0,
                       approved: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
        return await self._run(self._run_plan, plan_dict, goal, start, dict(approved or {}))

    async def open_url(self, url: str) -> str:
        return await self._run(lambda: self._ensure().open_url(url))

    def _screenshot(self) -> Optional[bytes]:
        try:
            return self._ensure().page.screenshot(type="jpeg", quality=80)
        except Exception:  # noqa: BLE001
            return None

    async def screenshot(self) -> Optional[bytes]:
        return await self._run(self._screenshot)


def make_step_asker(environ: Any = None) -> Any:
    """The same Jev step asker the desktop worker builds, or None (keyword gate alone)."""
    env = os.environ if environ is None else environ
    if (env.get("CC_BUDDY_JEV_STEP") or "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    from functools import partial

    from . import jev
    from .typed_ask import ask_jev_step

    try:
        url, key, model = jev.route_config(env)
    except jev.JevError as e:
        log.info("browser lane: jev is off (%s); the keyword gate decides alone", e)
        return None
    seconds = jev.timeout_from_env(env)
    return partial(ask_jev_step, jev.make_predict(url, key, model, timeout_s=seconds), clock=time.perf_counter)


def make_lane(config: BrowserLaneConfig, environ: Any = None) -> Optional[BrowserLane]:
    """The real lane, or None (with one log line) when it is off or Playwright is missing."""
    if not config.enabled:
        return None
    try:
        import playwright  # noqa: F401 — the import is the check
    except ImportError as e:
        log.warning("browser lane: playwright is not installed (%s); off. `pip install -e \".[browser]\"` and "
                    "`python -m playwright install chromium`", e)
        return None
    lane = BrowserLane(config, step_asker=make_step_asker(environ))
    where = (f"the owner's own Chrome (attach, via {config.chrome_dir}/DevToolsActivePort)" if config.attach
             else f"buddy's own Chromium profile at {config.profile}")
    log.info("browser lane: on — %s%s", where, "" if lane._step_asker is None else "; Jev grounds each step")
    return lane
