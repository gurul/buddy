"""buddy's Telegram Mini App: the apps buddy makes, opened from the chat.

Owner, 2026-09-24: "mini apps are to replace websites, like i want to build a habit tracker, it should do it". A
Mini App is a web page Telegram opens over the chat. Its home lists what buddy built and asks "What should I
build?" (apps_maker.py). It had a Claude chat tab too, removed the same day ("why does chat still exist in
apps"): the apps are the point.

* **Served by the daemon** on 127.0.0.1 only. Telegram needs a public HTTPS address, so a Cloudflare quick tunnel
  (``cloudflared tunnel --url``: no account, no domain) fronts it. Its address changes each start.
* **The way in.** The menu button stays the / commands (owner: "i want both"); a message pinned at the top of the
  chat carries an **Open buddy** button, edited to the new address every start and marked offline on stop.
  ``/apps`` sends the same button, and a finished build sends the app's picture with an Open button.
* **Only the owners.** Every API call from the home page carries Telegram's signed ``initData``; the server
  checks its HMAC with the bot token (Telegram's WebAppData scheme), its age, and that the user is in
  ``CC_BUDDY_TELEGRAM_OWNER``. Without a valid signature nothing is answered.
* **An app is fenced in.** An app is model-written code (plus any library it pins from jsdelivr), so it never
  gets the owner's initData. It is served sandboxed (``APP_CSP``: an opaque origin, no browser storage) and
  opened with an app token (``app_token``: signed with the bot token, bound to that app's slug, 24 hours) that
  answers for its own load and save and nothing else; its Content-Security-Policy lets it connect to those two
  addresses only. The page itself needs the token too: without one, ``/apps/<slug>/`` sends the phone to the
  home page (``/?open=<slug>``), which opens the app with a fresh token when Telegram signed the visit.
* **Claude** is ``claude-opus-5-5`` (``CC_BUDDY_MINIAPP_MODEL``) through the Anthropic SDK with the owner's
  ``ANTHROPIC_API_KEY``. Adaptive thinking is always on for this model; effort
  (``CC_BUDDY_MINIAPP_MAKE_EFFORT``, default high) is the depth control.
* **Spend is tracked, not capped.** Owner, 2026-09-24: "no claude limit, just track spend". Each
  build's cost is computed from its usage at the model's list price and added to a per-day ledger, and a line in
  the chat marks each of ``SPEND_ALERTS_USD`` the day crosses (tracking, not a limit).
  ``CC_BUDDY_MINIAPP_DAILY_USD`` set above 0 turns that total into a daily cap again.
* **Everything buddy spends is on the page.** The header's "$X spent today" is ALL of buddy's spend today (the
  daily meter, spend.py), not only the builds; tapping it opens the Spending view (``/api/spend``, owner only,
  aggregates only): today by provider and feature, the last 30 days, this month, and each provider's own figure
  where buddy can read one (spend_sync.py). docs/stackchan/spending.md.
* **Apps can be changed, undone, renamed and deleted** from the home page's "…" sheet (owner: "allow apps to be
  deleted and mutated easily"). A delete moves the app to buddy's ``apps/.trash`` on the Mac, and Undo on the
  page (or restore_app in the chat) brings it back; nothing is erased.
* **Each app has its own change chat** (owner, 2026-09-24: "a chat box for each app where you keep refining it
  (make the buttons bigger, add categories)"): the home page at ``/?chat=<slug>`` shows what was asked of the
  app and how each build went (``/api/apps/<slug>/history``, owner only), and takes the next change. Inside an
  app, Telegram's Settings item and a small pencil lead there; the app only navigates, it never gets the power
  to change apps or spend.

Off unless ``CC_BUDDY_MINIAPP=1`` with the Telegram door configured and ``ANTHROPIC_API_KEY`` set.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qsl

from . import pricing, spend

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MAKE_EFFORT = "high"          # building an app is real work: think it through
# $ per million tokens: pricing.ANTHROPIC_RATES, grounded on the Claude pricing page (2026-09-24): claude-opus-5-5
# input 4, output 20, cache reads 0.20, a 5-minute cache write 5, a 1-hour one 8. For the optional daily cap a
# model not listed costs as the most expensive listed one, so an unknown model is never under-counted there; the
# daily spend meter (spend.py) records it unpriced instead of guessing.
PRICES: dict[str, dict[str, float]] = {
    name: {"in": r["input"], "out": r["output"], "cache_read": r["cache_read"], "cache_write": r["cache_write_5m"],
           "cache_write_1h": r["cache_write_1h"]}
    for name, r in pricing.ANTHROPIC_RATES.items()
}
_WORST = {"in": 10.0, "out": 50.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0}
INIT_DATA_MAX_AGE_SECS = 24 * 3600    # a Mini App left open all day still works; an old leaked string does not
MAX_BODY_BYTES = 2_000_000
APP_ROUTE_RE = re.compile(r"^/apps/([a-z0-9][a-z0-9-]{0,47})/(index\.html)?$")
API_APP_RE = re.compile(
    r"^/api/apps/([a-z0-9][a-z0-9-]{0,47})/(load|save|report|delete|rename|revert|restore|history)$")
APP_TOKEN_SECS = 24 * 3600            # an app left open all day still saves; as long as initData lives
CLOSE_WAIT_SECS = 2.0                 # shutdown waits this long for open connections (a build's stream) to go
SPEND_ALERTS_USD = (5.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0)
# How an app page is served. The sandbox (no allow-same-origin) gives it an opaque origin: it cannot read the
# home page's storage or call the API as the home page, and browser storage throws (buddy.save is the storage).
# It may run inline code and pinned jsdelivr libraries, and connect to its own load and save only. The same
# sandbox is what app_check serves apps with, so the check sees what the phone sees.
APP_CSP = ("{sandbox}; default-src 'none'; "
           "script-src 'unsafe-inline' 'unsafe-eval' https://telegram.org/js/telegram-web-app.js {origin}/buddy.js "
           "https://cdn.jsdelivr.net/npm/; "
           "style-src 'unsafe-inline' https://cdn.jsdelivr.net/npm/; img-src data: blob: https://cdn.jsdelivr.net/npm/; "
           "font-src data: https://cdn.jsdelivr.net/npm/; media-src data: blob:; worker-src blob:; "
           "connect-src {origin}/api/apps/{slug}/ https://cdn.jsdelivr.net/npm/; "
           "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TUNNEL_START_SECS = 45.0
TUNNEL_RETRY_SECS = 10.0
PAGE_PATH = Path(__file__).with_name("miniapp_page.html")
CAP_LINE = "Today's Claude budget is used up (${cap:.2f}). It resets at midnight."
LEDGER_DAYS = 400                      # how many days of spend the ledger keeps
CORS = {"Access-Control-Allow-Origin": "*"}            # an app page's load and save come from an opaque origin
PROGRESS_SECS = 2.0                    # a build's progress event at least this often (it also keeps the tunnel open)


# ---- config -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MiniAppConfig:
    enabled: bool = False
    token: str = ""
    owner_ids: frozenset[int] = frozenset()
    model: str = DEFAULT_MODEL
    daily_usd: float = 0.0            # 0: no cap, spend is only tracked
    make_effort: str = DEFAULT_MAKE_EFFORT
    ledger_path: Path = field(default_factory=lambda: Path.home() / ".config" / "cc-buddy-bridge" /
                              "miniapp-spend.json")

    def __repr__(self) -> str:       # never the token
        return (f"MiniAppConfig(enabled={self.enabled}, owners={len(self.owner_ids)}, model={self.model!r}, "
                f"make_effort={self.make_effort!r}, daily_usd={self.daily_usd})")


def configured(environ: Any = None) -> MiniAppConfig:
    from . import telegram

    env = os.environ if environ is None else environ
    wanted = (env.get("CC_BUDDY_MINIAPP") or "0").strip().lower() in ("1", "true", "yes", "on")
    if not wanted:
        return MiniAppConfig()
    tg = telegram.configured(env)
    key = bool((env.get("ANTHROPIC_API_KEY") or "").strip())
    if not (tg.enabled and key):
        missing = [n for n, ok in (("the Telegram door", tg.enabled), ("ANTHROPIC_API_KEY", key)) if not ok]
        log.warning("miniapp: asked for (CC_BUDDY_MINIAPP=1) but off: %s not set", " and ".join(missing))
        return MiniAppConfig()
    try:
        cap = float(env.get("CC_BUDDY_MINIAPP_DAILY_USD") or 0)
    except ValueError:
        log.warning("miniapp: CC_BUDDY_MINIAPP_DAILY_USD is not a number; spend is tracked with no cap")
        cap = 0.0
    make_effort = (env.get("CC_BUDDY_MINIAPP_MAKE_EFFORT") or DEFAULT_MAKE_EFFORT).strip().lower()
    if make_effort not in EFFORTS:
        make_effort = DEFAULT_MAKE_EFFORT
    return MiniAppConfig(enabled=True, token=tg.token, owner_ids=tg.owner_ids,
                         model=(env.get("CC_BUDDY_MINIAPP_MODEL") or DEFAULT_MODEL).strip(),
                         daily_usd=max(0.0, cap), make_effort=make_effort)


# ---- who is asking: Telegram's signed initData ----------------------------------------------------

def phone_report(body: dict[str, Any]) -> str:
    """One log line from an app's own report (buddy.js): which Telegram it runs in, whether Telegram's native
    bridge is there (without it the MainButton and BackButton do nothing), and the page's errors. The only way to
    see what an app does on the real phone (owner, 2026-09-24: "the expense splitter doesn't work"). Every field
    is cut short and stripped of line breaks: it is text the page sent."""
    def clip(v: Any, n: int) -> str:
        return " ".join(str(v or "").split())[:n]

    errors = body.get("errors") if isinstance(body.get("errors"), list) else []
    parts = [f"platform={clip(body.get('platform'), 16)}", f"version={clip(body.get('version'), 8)}",
             f"bridge={clip(body.get('bridge'), 12)}", f"main_button={clip(body.get('main_button'), 40)}",
             f"errors={len(errors)}"]
    return ", ".join(parts) + "".join(f" | {clip(e, 240)}" for e in errors[:5])


def check_init_data(init_data: str, token: str, owner_ids: frozenset[int], *, now: Optional[float] = None,
                    max_age: float = INIT_DATA_MAX_AGE_SECS) -> Optional[int]:
    """The owner's user id when ``init_data`` is signed by this bot, fresh, and from an owner; else None.

    Telegram's scheme: secret = HMAC-SHA256(key="WebAppData", msg=bot token); the hash is
    HMAC-SHA256(key=secret, msg=every other field as key=value, sorted by key, joined by newlines)."""
    if not init_data or not token:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = fields.pop("hash", "")
    if not got:
        return None
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return None
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user_id = int(json.loads(fields.get("user", "{}")).get("id", 0))
    except (ValueError, TypeError, AttributeError):
        return None
    clock = time.time() if now is None else now
    if not auth_date or clock - auth_date > max_age or auth_date - clock > 300:
        return None
    return user_id if user_id in owner_ids else None


def _app_key(token: str) -> bytes:
    return hmac.new(b"buddy-app-token", token.encode(), hashlib.sha256).digest()


def app_token(slug: str, token: str, *, now: Optional[float] = None) -> str:
    """The key to one app: ``<expiry>.<signature>``, signed with a key derived from the bot token and bound to
    ``slug``. It opens that app's page and answers its load and save, nothing else, until it expires."""
    exp = int(time.time() if now is None else now) + APP_TOKEN_SECS
    return f"{exp}.{hmac.new(_app_key(token), f'{slug}.{exp}'.encode(), hashlib.sha256).hexdigest()[:40]}"


def check_app_token(tok: str, slug: str, token: str, *, now: Optional[float] = None) -> bool:
    exp, _, sig = str(tok or "").partition(".")
    if not token or not exp.isdigit() or len(sig) != 40:
        return False
    want = hmac.new(_app_key(token), f"{slug}.{exp}".encode(), hashlib.sha256).hexdigest()[:40]
    clock = time.time() if now is None else now
    return hmac.compare_digest(want, sig) and clock <= int(exp) <= clock + APP_TOKEN_SECS + 300


def app_csp(origin: str, slug: str) -> str:
    from .app_check import SANDBOX

    return APP_CSP.format(sandbox=SANDBOX, origin=origin.rstrip("/"), slug=slug)


def sign_init_data(fields: dict[str, str], token: str) -> str:
    """Build a signed initData string (what Telegram hands the page). For tests and the live check."""
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


# ---- what it costs: a per-day ledger --------------------------------------------------------------

def cost_usd(model: str, usage: Any) -> float:
    p = PRICES.get(model, _WORST)

    def n(name: str) -> int:
        v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        return int(v or 0)

    # A cache write costs by how long it lives: 5 minutes at 1.25x input, 1 hour at 2x (the maker's prompt is
    # cached for an hour). usage.cache_creation splits the writes; without it every write is a 5-minute one.
    split = getattr(usage, "cache_creation", None) if not isinstance(usage, dict) else usage.get("cache_creation")
    def part(name: str) -> int:
        v = getattr(split, name, None) if not isinstance(split, dict) else split.get(name)
        return int(v or 0)
    hour = part("ephemeral_1h_input_tokens") if split is not None else 0
    writes = n("cache_creation_input_tokens")
    return (n("input_tokens") * p["in"] + n("output_tokens") * p["out"]
            + n("cache_read_input_tokens") * p["cache_read"]
            + max(0, writes - hour) * p["cache_write"] + hour * p["cache_write_1h"]) / 1_000_000


class SpendLedger:
    """Dollars spent per day (local date), kept in one small JSON file (``{"days": {"2026-09-24": 1.83}}``) so
    a restart loses nothing. It only records: ``cap`` is None unless the owner set one (a cap above 0), and
    ``over()`` is the only thing that can refuse work."""

    def __init__(self, path: Path, cap_usd: Optional[float] = None,
                 today: Callable[[], str] = lambda: _dt.date.today().isoformat()):
        self.path, self._today = Path(path), today
        self.cap: Optional[float] = cap_usd if cap_usd and cap_usd > 0 else None
        # called with (the mark crossed, today's total) when today's spend passes one of SPEND_ALERTS_USD
        self.on_cross: Optional[Callable[[float, float], Any]] = None

    def days(self) -> dict[str, float]:
        """Every recorded day's total, by date. A file from the capped version (``{"date", "usd"}``) is read
        as its one day."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        if "days" not in data and "date" in data:
            data = {"days": {str(data["date"]): data.get("usd", 0.0)}}
        raw = data.get("days")
        out: dict[str, float] = {}
        for day, usd in (raw.items() if isinstance(raw, dict) else ()):
            try:
                out[str(day)] = float(usd)
            except (TypeError, ValueError):
                continue
        return out

    def spent(self) -> float:
        return self.days().get(self._today(), 0.0)

    def left(self) -> Optional[float]:
        """What the cap leaves today, or None when there is no cap."""
        return None if self.cap is None else max(0.0, self.cap - self.spent())

    def over(self) -> bool:
        return self.cap is not None and self.spent() >= self.cap

    def add(self, usd: float) -> float:
        """Record ``usd`` against today; today's new total."""
        days = self.days()
        today = self._today()
        before = days.get(today, 0.0)
        days[today] = round(before + max(0.0, usd), 6)
        keep = dict(sorted(days.items())[-LEDGER_DAYS:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"days": keep}))
        tmp.replace(self.path)
        crossed = [m for m in SPEND_ALERTS_USD if before < m <= keep[today]]
        if crossed and self.on_cross is not None:
            try:
                self.on_cross(crossed[-1], keep[today])
            except Exception as e:  # noqa: BLE001 — a note that fails never loses the record
                log.warning("miniapp: the spend note failed (%s)", type(e).__name__)
        return keep[today]


def _error_line(e: Exception) -> str:
    try:
        import anthropic
    except ImportError:                                  # pragma: no cover — the SDK is a dependency
        return "Claude could not be reached."
    if isinstance(e, anthropic.AuthenticationError):
        return "The Claude API key was refused. Check ANTHROPIC_API_KEY on the Mac."
    if isinstance(e, anthropic.RateLimitError):
        return "Claude is rate-limited right now. Try again in a minute."
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude returned an error ({e.status_code})."
    if isinstance(e, anthropic.APIConnectionError):
        return "Claude could not be reached. Check the Mac's internet connection."
    return "Something went wrong answering that."


# ---- the HTTP server (127.0.0.1; the tunnel is its only public door) ------------------------------

class MiniAppServer:
    def __init__(self, cfg: MiniAppConfig, ledger: SpendLedger,
                 page: Optional[bytes] = None, store: Any = None, maker: Any = None,
                 changing: Optional[set[str]] = None,
                 notify: Optional[Callable[[int, dict[str, Any]], Awaitable[Any]]] = None) -> None:
        self.cfg, self.ledger = cfg, ledger
        self._page = page
        self.store, self.maker = store, maker             # apps_maker.AppStore / AppMaker; None: no apps
        self._building: set[int] = set()                  # one build at a time per owner
        # The apps being changed right now, by slug. MiniApp shares this set with the chat door
        # (apps_maker.ChatMaker.building), so neither door deletes, renames or undoes an app under a build.
        self.changing: set[str] = changing if changing is not None else set()
        # The build running per owner, for a page opened again mid-build (/api/apps "building"), and where a
        # build's result goes when the page that started it went away (a locked phone): the chat.
        self.builds: dict[int, dict[str, Any]] = {}
        self.notify = notify
        self.public_url = ""                              # the tunnel's address: the origin app pages are bound to
        self._conns: set[asyncio.Task] = set()
        self._server: Optional[asyncio.base_events.Server] = None
        self.port = 0

    def page(self) -> bytes:
        if self._page is None:
            self._page = PAGE_PATH.read_bytes()
        return self._page

    def spend(self) -> dict[str, Any]:
        """What the page shows in its header: ALL of buddy's spend today (spend.py's meter: chat, voice, search,
        builds, everything), the app builder's own share, and its cap (None: none)."""
        return {"spent_today": round(spend.today_total(), 4), "apps_today": round(self.ledger.spent(), 4),
                "cap": self.ledger.cap}

    def spending(self) -> dict[str, Any]:
        """The Spending view: buddy's own meter (today split by provider, feature and model, yesterday, the last
        30 days, this month) and each provider's own figure where one can be read (spend_sync.py). Aggregates
        only: no prompt, no answer, no key ever leaves in it."""
        from . import spend_sync

        return {**spend.summary(), "providers": spend_sync.reported(), "cap": self.ledger.cap,
                "apps_today": round(self.ledger.spent(), 4)}

    def open_url(self, slug: str) -> str:
        """An app's page with a fresh app token, relative to the home page."""
        return f"apps/{slug}/?t={app_token(slug, self.cfg.token)}"

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        """Stop serving. Open connections are cut, and the wait for them is bounded: a build's stream stays
        open for minutes, and a daemon restart must not wait for it (on Python 3.12+ wait_closed waits for every
        handler) until launchd kills it before the pinned button says offline."""
        if self._server is not None:
            self._server.close()
            for task in list(self._conns):
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), CLOSE_WAIT_SECS)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            if task is not None:
                self._conns.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    def _origin(self, headers: dict[str, str]) -> str:
        """This server's public origin: the tunnel's, else (local use, tests) what the Host header names."""
        if self.public_url:
            return self.public_url.rstrip("/")
        host = headers.get("host", "")
        return f"http://{host}" if re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", host) else "http://127.0.0.1"

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = (lines[0].split(" ", 2) + ["", ""])[:3]
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
        path, _, query = target.partition("?")
        if method == "GET" and path in ("/", "/index.html"):
            await self._send(writer, 200, self.page(), "text/html; charset=utf-8")
            return
        if method == "GET" and path == "/healthz":
            await self._send(writer, 200, b"ok", "text/plain")
            return
        if method == "GET" and path == "/buddy.js" and self.store is not None:
            from .apps_maker import BUDDY_JS

            await self._send(writer, 200, BUDDY_JS.encode(), "application/javascript; charset=utf-8")
            return
        app_route = APP_ROUTE_RE.match(path)
        if method == "GET" and app_route and self.store is not None:
            await self._app_page(writer, app_route.group(1), query, headers)
            return
        api_app = API_APP_RE.match(path)
        app_io = api_app is not None and api_app.group(2) in ("load", "save", "report")
        if method == "OPTIONS" and app_io:
            # A sandboxed app's buddy.js sends a CORS-simple request (no preflight), but answer one anyway.
            await self._send(writer, 204, b"", "text/plain", CORS | {"Access-Control-Allow-Methods": "POST",
                                                                     "Access-Control-Allow-Headers": "content-type",
                                                                     "Access-Control-Max-Age": "600"})
            return
        known = path in ("/api/me", "/api/spend", "/api/apps", "/api/make") or api_app is not None
        if method != "POST" or not known:
            await self._send(writer, 404, b"not found", "text/plain")
            return
        size = int(headers.get("content-length") or 0)
        if size <= 0 or size > MAX_BODY_BYTES:
            await self._send(writer, 413, b"too large", "text/plain")
            return
        try:
            body = json.loads(await asyncio.wait_for(reader.readexactly(size), 30))
        except (ValueError, asyncio.TimeoutError):
            await self._send(writer, 400, b"bad request", "text/plain")
            return
        if not isinstance(body, dict):
            await self._send(writer, 400, b"bad request", "text/plain")
            return
        if "t" in body:
            # An app's own load or save, with the app token its page was opened with; that token opens nothing else.
            if app_io and self.store is not None and check_app_token(str(body.get("t")), api_app.group(1),
                                                                    self.cfg.token):
                await self._json(writer, *self._app_io(api_app.group(1), api_app.group(2), body), extra=CORS)
            else:
                await self._json(writer, 403, {"error": "This app can only reach its own data."}, extra=CORS)
            return
        origin = headers.get("origin", "")
        if origin == "null" or (origin and self.public_url and origin != self._origin(headers)):
            # A sandboxed app page (Origin: null) or another site: only the home page may call the owner API.
            await self._json(writer, 403, {"error": "Only buddy's home page can do that."})
            return
        user = check_init_data(str(body.get("initData") or ""), self.cfg.token, self.cfg.owner_ids)
        if user is None:
            await self._json(writer, 403, {"error": "Only buddy's owner can use this."})
            return
        if path == "/api/me":
            await self._json(writer, 200, {"model": self.cfg.model, "apps": self.store is not None, **self.spend()})
            return
        if path == "/api/spend":
            await self._json(writer, 200, await asyncio.to_thread(self.spending))    # ~30 small file reads
            return
        if path == "/api/apps" or api_app is not None or path == "/api/make":
            await self._apps_api(writer, path, api_app, body, user)
            return
    async def _app_page(self, writer: asyncio.StreamWriter, slug: str, query: str, headers: dict[str, str]) -> None:
        """One app's page, for its app token only, served sandboxed (APP_CSP). Without a valid token (an Open
        button from before tokens, a link someone saw) the answer is the same for every slug, so it says
        nothing about which apps exist: the home page, which opens the app when Telegram signed the visit."""
        from .apps_maker import serve_app_html

        tok = dict(parse_qsl(query)).get("t", "")
        html = self.store.html(slug) if check_app_token(tok, slug, self.cfg.token) else None
        if html is None:
            log.info("apps: %s asked for without a valid app token; sent to the home page", slug)
            await self._send(writer, 302, b"", "text/plain", {"Location": f"/?open={slug}"})
            return
        log.info("apps: %s opened", slug)
        await self._send(writer, 200, serve_app_html(html).encode(), "text/html; charset=utf-8",
                         {"Content-Security-Policy": app_csp(self._origin(headers), slug),
                          "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})

    def _building_now(self, user: int) -> list[dict[str, Any]]:
        """The builds running now: this owner's own from the page, and apps the chat door is changing."""
        out = []
        mine = self.builds.get(user)
        if mine is not None:
            out.append({"app": mine["app"], "request": mine["request"], "stage": mine["stage"],
                        "secs": round(time.time() - mine["started"])})
        for slug in sorted(self.changing):
            if (mine is None or slug != mine["app"]) and self.store is not None and self.store.info(slug) is not None:
                out.append({"app": slug, "request": "", "stage": "", "secs": None})
        return out

    async def _apps_api(self, writer: asyncio.StreamWriter, path: str, api_app: Any, body: dict[str, Any],
                        user: int) -> None:
        if self.store is None:
            await self._json(writer, 404, {"error": "Apps are off."})
            return
        if path == "/api/apps":
            await self._json(writer, 200, {"apps": [{**a.as_dict(), "open": self.open_url(a.slug)}
                                                    for a in self.store.list()],
                                           "building": self._building_now(user)})
            return
        if api_app is not None and api_app.group(2) == "history":
            await self._json(writer, *self._history(api_app.group(1), user))
            return
        if api_app is not None:
            await self._json(writer, *self._app_action(api_app.group(1), api_app.group(2), body))
            return
        # /api/make: {request} makes a new app; {app: slug, request} changes that one. Progress as SSE.
        request_text = str(body.get("request") or "").strip()[:4000]
        if not request_text or self.maker is None:
            await self._json(writer, 400, {"error": "Say what the app should do."})
            return
        slug = str(body.get("app") or "")
        if slug and self.store.info(slug) is None:
            await self._json(writer, 404, {"error": "There is no such app.", "gone": True})
            return
        if self.ledger.over():
            await self._json(writer, 429, {"error": CAP_LINE.format(cap=self.ledger.cap)})
            return
        if user in self._building:
            await self._json(writer, 409, {"error": "Still working on the last one.",
                                           "building": self._building_now(user)})
            return
        if slug and slug in self.changing:
            await self._json(writer, 409, {"error": "That app is being changed right now."})
            return
        self._building.add(user)
        self.builds[user] = {"app": slug, "request": request_text[:80], "started": time.time(), "stage": "thinking"}
        if slug:
            self.changing.add(slug)
        try:
            await self._make(writer, request_text, slug, user)
        finally:
            self._building.discard(user)
            self.builds.pop(user, None)
            self.changing.discard(slug)

    def _history(self, slug: str, user: int) -> tuple[int, dict[str, Any]]:
        """One app's change chat: the app (with a fresh Open address), what was asked of it and how each build
        went (AppStore.thread: when, the request, version, the check's line, the journeys, what may still be
        wrong, cost), and the change running on it now, if any (``stage`` and ``secs`` only for this owner's
        own build from the page; a change from the chat door has neither)."""
        assert self.store is not None
        info = self.store.info(slug)
        if info is None:
            return 404, {"error": "There is no such app.", "gone": True}
        try:
            thread = self.store.thread(slug)
        except KeyError:
            return 404, {"error": "There is no such app.", "gone": True}
        running = next((b for b in self._building_now(user) if b["app"] == slug), None)
        return 200, {"app": {**info.as_dict(), "open": self.open_url(slug)}, "thread": thread,
                     "building": running, **self.spend()}

    def _app_io(self, slug: str, action: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        assert self.store is not None
        try:
            if action == "report":
                log.info("apps: %s on the phone: %s", slug, phone_report(body))
                return 200, {"ok": True}
            if action == "load":
                data = self.store.load_data(slug)
                log.info("apps: %s loaded (%s)", slug, "no data yet" if data is None else "has data")
                return 200, {"data": data}
            size = self.store.save_data(slug, body.get("data"))
            log.info("apps: %s saved %d bytes", slug, size)
            return 200, {"ok": True, "bytes": size}
        except KeyError:
            return 404, {"error": "There is no such app."}
        except ValueError:
            return 413, {"error": "That is too much data for one app (1 MB)."}

    def _app_action(self, slug: str, action: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """One app's load, save, delete, rename, revert or restore. Delete, rename and revert wait while the app
        is being changed: the build would otherwise save over a rename or bring a deleted app back."""
        assert self.store is not None
        if action in ("load", "save"):
            return self._app_io(slug, action, body)
        if action in ("delete", "rename", "revert") and slug in self.changing:
            return 409, {"error": "That app is being changed right now. Try again when it is done."}
        try:
            if action == "delete":
                info = self.store.info(slug)
                self.store.delete(slug)
                log.info("apps: %s moved to the trash from the Mini App", slug)
                return 200, {"ok": True, "deleted": info.title if info else slug}
            if action == "restore":
                try:
                    info = self.store.restore(slug)
                except FileExistsError:
                    return 409, {"error": "A newer app has that address now, so this one can't come back under "
                                          "it. Rename or delete the newer one first."}
                log.info("apps: %s restored from the trash from the Mini App", slug)
                return 200, {"ok": True, "app": info.as_dict()}
            if action == "rename":
                try:
                    info = self.store.rename(slug, str(body.get("title") or ""))
                except ValueError:
                    return 400, {"error": "An app needs a name."}
                return 200, {"ok": True, "app": info.as_dict()}
            now = self.store.info(slug)
            if now is None:
                raise KeyError(slug)
            # Undo is a pop, so a second tap sent before the first answered must not pop a second version: the
            # page says how many versions it saw, and a count that no longer matches is refused.
            seen = body.get("versions")
            if isinstance(seen, int) and not isinstance(seen, bool) and seen != now.versions:
                return 409, {"error": "That was already undone.", "app": now.as_dict()}
            try:
                info = self.store.revert(slug)
            except KeyError:
                return 409, {"error": "There is no earlier version to go back to."}
            log.info("apps: %s undone from the Mini App", slug)
            return 200, {"ok": True, "app": info.as_dict()}
        except KeyError:
            return 404, {"error": "There is no such app."}

    async def _make(self, writer: asyncio.StreamWriter, request_text: str, slug: str, user: int = 0) -> None:
        """Build or change one app, streaming ``{"s": stage, "p": n}`` (apps_maker's progress: thinking, writing
        with the characters so far, testing, fixing with the number of problems, saving) at least every
        PROGRESS_SECS and at once on a new stage, then one done or error event. A phone that goes away does not
        stop the build: it finishes and saves, and its result goes to the chat instead (``notify``)."""
        assert self.maker is not None
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
                     b"Cache-Control: no-store\r\nX-Accel-Buffering: no\r\nConnection: close\r\n\r\n")
        await writer.drain()
        state = {"s": "thinking", "p": 0}
        wake = asyncio.Event()
        done = False
        gone = False                                      # the page stopped listening (locked phone, closed app)

        async def ticker() -> None:
            nonlocal gone
            try:
                while not done:
                    wake.clear()
                    writer.write(b"data: " + json.dumps(state).encode() + b"\n\n")
                    await writer.drain()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(wake.wait(), PROGRESS_SECS)
            except (ConnectionError, OSError):
                gone = True

        def on_progress(stage: str, n: int) -> None:
            new_stage = stage != state["s"]
            state["s"], state["p"] = stage, int(n)
            if user in self.builds:
                self.builds[user]["stage"] = stage
            if new_stage:
                wake.set()

        tick = asyncio.create_task(ticker())
        reason = ""
        made = None
        try:
            made = await (self.maker.edit(slug, request_text, on_progress) if slug
                          else self.maker.make(request_text, on_progress))
        except Exception as e:  # noqa: BLE001 — a failure is a line on the phone
            log.warning("apps: making failed (%s)", type(e).__name__)
            reason = _error_line(e)
        finally:
            done = True
            wake.set()
            with contextlib.suppress(Exception):
                await tick
        if made is not None and made.ok and made.app is not None:
            from .apps_maker import owner_issues

            tested = made.check is not None and not made.check.skipped
            check = made.check.summary() if made.check is not None else "not tested"
            log.info("apps: %s phone check %s", made.app.slug, check)
            out: dict[str, Any] = {"done": True, "app": made.app.as_dict(), "url": self.open_url(made.app.slug),
                                   "check": check, "tested": tested, "rounds": made.rounds,
                                   "problems": len(made.issues),
                                   "issues": owner_issues(made.check, made.issues[:2]),
                                   "changed": bool(slug), "usd": round(made.usd, 4), **self.spend()}
        else:
            out = {"error": f"I couldn't {'change' if slug else 'build'} it: {made.reason}." if made is not None
                   else reason, "changed": bool(slug), **self.spend()}
            if slug:
                out["slug"] = slug                    # a change that failed still leads to its change chat
        if not gone:
            try:
                writer.write(b"data: " + json.dumps(out).encode() + b"\n\n")
                await writer.drain()
                return
            except (ConnectionError, OSError):
                pass
        if self.notify is not None and user:
            with contextlib.suppress(Exception):
                await self.notify(user, out)

    async def _send(self, writer: asyncio.StreamWriter, status: int, body: bytes, ctype: str,
                    extra: Optional[dict[str, str]] = None) -> None:
        reason = {200: "OK", 204: "No Content", 302: "Found", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found", 409: "Conflict", 413: "Payload Too Large",
                  429: "Too Many Requests"}.get(status, "OK")
        more = "".join(f"{k}: {v}\r\n" for k, v in (extra or {}).items())
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     f"Cache-Control: no-store\r\n{more}Connection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _json(self, writer: asyncio.StreamWriter, status: int, obj: dict[str, Any],
                    extra: Optional[dict[str, str]] = None) -> None:
        await self._send(writer, status, json.dumps(obj).encode(), "application/json", extra)


# ---- the public door: a Cloudflare quick tunnel -----------------------------------------------------

def cloudflared_bin() -> Optional[str]:
    return (os.environ.get("CC_BUDDY_CLOUDFLARED") or shutil.which("cloudflared")
            or next((p for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared") if Path(p).exists()),
                    None))


class QuickTunnel:
    """``cloudflared tunnel --url http://127.0.0.1:<port>``: prints its trycloudflare.com address on stderr."""

    def __init__(self, binary: str) -> None:
        self.binary = binary
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.url = ""
        self._drain: Optional[asyncio.Task] = None

    async def start(self, port: int, timeout: float = TUNNEL_START_SECS) -> str:
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        assert self.proc.stderr is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = await asyncio.wait_for(self.proc.stderr.readline(), max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if not line:
                break
            m = TUNNEL_URL_RE.search(line.decode(errors="replace"))
            if m:
                self.url = m.group(0)
                self._drain = asyncio.create_task(self._drain_stderr(), name="miniapp-tunnel-log")
                return self.url
        await self.stop()
        raise RuntimeError("cloudflared gave no address")

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while await self.proc.stderr.readline():
            pass                                          # keep the pipe from filling; the lines are noise

    async def wait(self) -> int:
        assert self.proc is not None
        return await self.proc.wait()

    async def stop(self) -> None:
        if self._drain is not None:
            self._drain.cancel()
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()


# ---- the way in: a pinned Open button (the menu button stays the / commands) -------------------------
#
# Owner, 2026-09-24: "menu disappeared, all that's left is ask claude, i want both". Telegram's menu button is
# either the / command list or one Mini App, never both, so it stays the commands, and the Mini App is one tap
# away in a message pinned at the top of the chat (plus /apps, and the Open button a build sends). The tunnel's
# address changes every start, so the pinned message's button is edited to the new one each time.

Bot = Callable[[str, dict], Awaitable[Any]]
PIN_TEXT = "buddy: your apps."
PIN_OFFLINE_TEXT = "buddy's apps are offline (the Mac is asleep or restarting). This button comes back by itself."
OPEN_TEXT = "Open buddy"


def bot_caller(token: str) -> Bot:
    """One Bot API call as ``await bot(method, data)`` -> Telegram's JSON reply (``{"ok": ...}``)."""
    async def call(method: str, data: dict) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=20) as c:
            return (await c.post(f"https://api.telegram.org/bot{token}/{method}", json=data)).json()

    return call


def open_button(url: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": OPEN_TEXT, "web_app": {"url": url}}]]}


class Pins:
    """The pinned Open message per owner chat: {chat id: message id} in a small JSON file."""

    def __init__(self, path: Path, bot: Bot) -> None:
        self.path, self._bot = Path(path), bot

    def _load(self) -> dict[str, int]:
        try:
            return {str(k): int(v) for k, v in json.loads(self.path.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, pins: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(pins))

    async def _ok(self, method: str, data: dict) -> Any:
        try:
            reply = await self._bot(method, data)
        except Exception as e:  # noqa: BLE001 — a failed call is logged, never fatal
            log.warning("miniapp: %s failed (%s)", method, type(e).__name__)
            return None
        if isinstance(reply, dict) and (reply.get("ok") or "not modified" in str(reply.get("description", ""))):
            return reply
        log.info("miniapp: %s refused (%s)", method,
                 str(reply.get("description") if isinstance(reply, dict) else reply)[:120])
        return None

    async def show(self, chat_id: int, url: str) -> None:
        """The / menu on the menu button, and the pinned message's button pointing at ``url``."""
        await self._ok("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "commands"}})
        pins = self._load()
        old = pins.get(str(chat_id))
        if old and await self._ok("editMessageText", {"chat_id": chat_id, "message_id": old, "text": PIN_TEXT,
                                                      "reply_markup": open_button(url)}):
            return
        sent = await self._ok("sendMessage", {"chat_id": chat_id, "text": PIN_TEXT, "disable_notification": True,
                                              "reply_markup": open_button(url)})
        if not sent:
            return
        mid = int(sent["result"]["message_id"])
        await self._ok("pinChatMessage", {"chat_id": chat_id, "message_id": mid, "disable_notification": True})
        pins[str(chat_id)] = mid
        self._save(pins)

    async def offline(self, chat_id: int) -> None:
        """The daemon is stopping: the pinned button would lead nowhere, so it says so instead."""
        old = self._load().get(str(chat_id))
        if old:
            await self._ok("editMessageText", {"chat_id": chat_id, "message_id": old, "text": PIN_OFFLINE_TEXT})


# ---- the whole thing, for the daemon's life ---------------------------------------------------------

_DEFAULT: Any = object()


class MiniApp:
    """The server, the tunnel and the pinned button, plus the app maker both doors share.

    ``check`` is the phone check each build goes through (app_check.check_app; None turns it off) and
    ``context`` the owner-context line each build prompt carries (apps_maker.owner_context: date, time zone,
    units). ``changing`` is the set of app slugs being changed right now, shared with the chat door."""

    def __init__(self, cfg: MiniAppConfig, *, tunnel_factory: Optional[Callable[[], Any]] = None,
                 bot: Optional[Bot] = None, pins_path: Optional[Path] = None,
                 generate: Any = None, apps_root: Optional[Path] = None,
                 check: Any = _DEFAULT, context: Optional[Callable[[], str]] = None) -> None:
        from . import app_check, apps_maker

        self.cfg = cfg
        self.ledger = SpendLedger(cfg.ledger_path, cfg.daily_usd)
        self.store = apps_maker.AppStore(apps_root or apps_maker.DEFAULT_ROOT)
        self.maker = apps_maker.AppMaker(
            self.store, generate or apps_maker.claude_generate(cfg.model, cfg.make_effort),
            cost=lambda usage: self._meter_build(usage), spend=self.ledger.add,
            context=context or apps_maker.owner_context,
            check=app_check.check_app if check is _DEFAULT else check)
        self.changing: set[str] = set()
        self.server = MiniAppServer(cfg, self.ledger,
                                    store=self.store, maker=self.maker, changing=self.changing,
                                    notify=self._build_result)
        binary = cloudflared_bin()
        self._tunnel_factory = tunnel_factory or ((lambda: QuickTunnel(binary)) if binary else None)
        self._bot = bot or bot_caller(cfg.token)
        self.pins = Pins(pins_path or cfg.ledger_path.with_name("miniapp-pin.json"), self._bot)
        self.url = ""
        self._notes: set[asyncio.Task] = set()
        self.ledger.on_cross = self._spend_note

    def _meter_build(self, usage: Any) -> float:
        """One build round's cost: into the daily spend meter (spend.py, feature "app builder", unpriced for a
        model pricing.py does not know), and returned at the cap's never-under-counted price for this ledger."""
        spend.record_anthropic(spend.APPS, self.cfg.model, usage)
        return cost_usd(self.cfg.model, usage)

    def app_url(self, slug: str) -> str:
        """An Open button's address for one app right now ("" while the tunnel is down). It goes through the
        home page, which gets Telegram's signed initData and opens the app with an app token: the app never
        sees the initData, which answers for the whole API."""
        return f"{self.url}/?open={slug}" if self.url else ""

    def chat_url(self, slug: str) -> str:
        """A Change button's address: the app's change chat on the home page ("" while the tunnel is down)."""
        return f"{self.url}/?chat={slug}" if self.url else ""

    async def _say(self, text: str, buttons: Optional[list[tuple[str, str]]] = None) -> None:
        for chat in sorted(self.cfg.owner_ids):           # a private chat's id is the owner's user id
            data: dict[str, Any] = {"chat_id": chat, "text": text}
            if buttons:                                   # one per row, as telegram.send_web_apps lays them out
                data["reply_markup"] = {"inline_keyboard": [[{"text": label, "web_app": {"url": url}}]
                                                            for label, url in buttons]}
            await self.pins._ok("sendMessage", data)

    async def _build_result(self, user: int, out: dict[str, Any]) -> None:
        """A build from the page finished after the page went away (a locked phone): its result comes to the
        chat, as a build from the chat does, so it is never lost."""
        if out.get("done"):
            app = out.get("app") or {}
            title = str(app.get("title") or "Your app")
            text = f"{app.get('icon') or ''} {title} is {'updated' if out.get('changed') else 'ready'}.".strip()
            if out.get("issues"):
                text += f"\nIt may still have a problem: {str(out['issues'][0])[:240]}"
            slug = str(app.get("slug") or "")
            url = self.app_url(slug)
            buttons = [(f"Open {title}", url)] if url else []
            if url and out.get("changed"):
                buttons.append(("Change", self.chat_url(slug)))       # back to its change chat, to keep refining
            await self._say(text, buttons)
        else:
            slug = str(out.get("slug") or "")
            chat = self.chat_url(slug) if slug and out.get("changed") and self.store is not None \
                and self.store.info(slug) is not None else ""
            await self._say(str(out.get("error") or "The app build did not finish."), [("Change", chat)] if chat else None)

    def _spend_note(self, mark: float, total: float) -> None:
        """Today's Claude spend passed ``mark``: a line in the chat. Tracking, not a limit (owner: "no claude
        limit, just track spend"), so a runaway (a leaked key, a loop) is seen the same day."""
        try:
            task = asyncio.get_running_loop().create_task(self._say(
                f"Claude has cost ${total:.2f} today (chat and app builds), past ${mark:.0f}. Nothing is capped; "
                "this is only so you know."))
        except RuntimeError:
            return
        self._notes.add(task)
        task.add_done_callback(self._notes.discard)

    async def _point_pins(self, url: str) -> None:
        for chat in sorted(self.cfg.owner_ids):           # a private chat's id is the owner's user id
            await (self.pins.show(chat, url) if url else self.pins.offline(chat))

    async def run(self) -> None:
        if self._tunnel_factory is None:
            log.warning("miniapp: cloudflared is not installed (brew install cloudflared); the Mini App is off")
            return
        port = await self.server.start()
        log.info("miniapp: serving on 127.0.0.1:%d (%s, build effort %s; %s)", port, self.cfg.model, self.cfg.make_effort,
                 f"a ${self.ledger.cap:.2f} daily cap" if self.ledger.cap else "spend tracked, no cap")
        tunnel = None
        try:
            while True:
                tunnel = self._tunnel_factory()
                try:
                    self.url = await tunnel.start(port)
                except Exception as e:  # noqa: BLE001 — no address this time: try again shortly
                    log.warning("miniapp: the tunnel did not start (%s); retrying in %.0f s", e, TUNNEL_RETRY_SECS)
                    await asyncio.sleep(TUNNEL_RETRY_SECS)
                    continue
                self.server.public_url = self.url
                log.info("miniapp: live at %s; the pinned Open button points there", self.url)
                await self._point_pins(self.url + "/")
                code = await tunnel.wait()
                log.warning("miniapp: the tunnel exited (%s); starting a new one", code)
                self.url = ""
                await asyncio.sleep(TUNNEL_RETRY_SECS)
        finally:
            if tunnel is not None:
                await tunnel.stop()
            await self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.shield(self._point_pins(""))   # the pinned button says offline, not a dead link
