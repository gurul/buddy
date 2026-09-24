"""The "Ask Claude" Mini App: the bot's menu button opens a chat with Claude inside Telegram.

Owner, 2026-09-24: "I don't want rest in terminal", then "Miniapp", "opus 5.5 please". A Telegram chat
message caps at 4096 characters and arrives whole; a Mini App is a web page Telegram opens over the chat,
so a long answer streams in as it is written and is never cut.

* **The page** (``miniapp_page.html``) is served by the daemon on 127.0.0.1 only. Telegram needs a public
  HTTPS address, so a Cloudflare quick tunnel (``cloudflared tunnel --url``: no account, no domain) fronts
  it. Its address changes each time it starts, so the daemon points the owners' menu button at the new one
  (setChatMenuButton) every start, and puts the / menu back when it stops.
* **Only the owners.** Every API call carries Telegram's signed ``initData``; the server checks its HMAC
  with the bot token (Telegram's WebAppData scheme), its age, and that the user is in
  ``CC_BUDDY_TELEGRAM_OWNER``. The page itself holds nothing; without a valid signature nothing is answered.
* **Claude** is ``claude-opus-5-5`` (``CC_BUDDY_MINIAPP_MODEL``) through the Anthropic SDK with the owner's
  ``ANTHROPIC_API_KEY``, streamed back as server-sent events. Adaptive thinking is always on for this model;
  effort (``CC_BUDDY_MINIAPP_EFFORT``, default medium) is the cost and depth control.
* **A daily spend cap** (``CC_BUDDY_MINIAPP_DAILY_USD``, default 5): each answer's cost is computed from its
  usage at the model's list price and added to a per-day ledger; at the cap the app says so and asks nothing
  more until tomorrow. The history lives in the page (the phone), sent with each question; the server keeps
  no conversation.

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
from typing import Any, AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import parse_qsl

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_DAILY_USD = 5.0
MAX_TOKENS = 32000                    # one answer's ceiling; streaming, so no HTTP timeout concern
# $ per million tokens, from the Claude API model table (cached 2026-06-24): claude-opus-5-5 input 4, output 20,
# cache reads 0.20; a 5-minute cache write is 1.25x input. A model not listed costs as the most expensive
# listed one, so an unknown model can only make the cap stricter.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
}
_WORST = {"in": 10.0, "out": 50.0, "cache_read": 1.0, "cache_write": 12.5}
INIT_DATA_MAX_AGE_SECS = 24 * 3600    # a Mini App left open all day still works; an old leaked string does not
MAX_BODY_BYTES = 2_000_000
MAX_TURNS = 60                         # the newest turns of the page's history
MAX_HISTORY_CHARS = 400_000
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TUNNEL_START_SECS = 45.0
TUNNEL_RETRY_SECS = 10.0
MENU_TEXT = "Ask Claude"
PAGE_PATH = Path(__file__).with_name("miniapp_page.html")
SYSTEM_PROMPT = (
    "You are Claude, answering the owner of buddy (their personal desk assistant) inside a Telegram Mini App "
    "on their phone. Answers render as Markdown: headings, lists, bold, inline code and fenced code blocks. "
    "Write for a phone screen: lead with the answer, keep paragraphs short, and use a list or a table only "
    "when the content is a list or a table.")
CAP_LINE = "Today's Claude budget is used up (${cap:.2f}). It resets at midnight."


# ---- config -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MiniAppConfig:
    enabled: bool = False
    token: str = ""
    owner_ids: frozenset[int] = frozenset()
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    daily_usd: float = DEFAULT_DAILY_USD
    ledger_path: Path = field(default_factory=lambda: Path.home() / ".config" / "cc-buddy-bridge" /
                              "miniapp-spend.json")

    def __repr__(self) -> str:       # never the token
        return (f"MiniAppConfig(enabled={self.enabled}, owners={len(self.owner_ids)}, model={self.model!r}, "
                f"effort={self.effort!r}, daily_usd={self.daily_usd})")


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
    effort = (env.get("CC_BUDDY_MINIAPP_EFFORT") or DEFAULT_EFFORT).strip().lower()
    if effort not in EFFORTS:
        log.warning("miniapp: CC_BUDDY_MINIAPP_EFFORT=%r is not an effort; using %s", effort, DEFAULT_EFFORT)
        effort = DEFAULT_EFFORT
    try:
        cap = float(env.get("CC_BUDDY_MINIAPP_DAILY_USD") or DEFAULT_DAILY_USD)
    except ValueError:
        cap = DEFAULT_DAILY_USD
    return MiniAppConfig(enabled=True, token=tg.token, owner_ids=tg.owner_ids,
                         model=(env.get("CC_BUDDY_MINIAPP_MODEL") or DEFAULT_MODEL).strip(), effort=effort,
                         daily_usd=max(0.0, cap))


# ---- who is asking: Telegram's signed initData ----------------------------------------------------

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

    return (n("input_tokens") * p["in"] + n("output_tokens") * p["out"]
            + n("cache_read_input_tokens") * p["cache_read"]
            + n("cache_creation_input_tokens") * p["cache_write"]) / 1_000_000


class SpendLedger:
    """Dollars spent today (local date), kept in one small JSON file so a restart does not reset the cap."""

    def __init__(self, path: Path, cap_usd: float, today: Callable[[], str] = lambda: _dt.date.today().isoformat()):
        self.path, self.cap, self._today = Path(path), cap_usd, today

    def _load(self) -> float:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return 0.0
        return float(data.get("usd", 0.0)) if data.get("date") == self._today() else 0.0

    def spent(self) -> float:
        return self._load()

    def left(self) -> float:
        return max(0.0, self.cap - self._load())

    def add(self, usd: float) -> float:
        total = self._load() + max(0.0, usd)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": self._today(), "usd": round(total, 6)}))
        tmp.replace(self.path)
        return total


# ---- the conversation the page sends --------------------------------------------------------------

def clean_history(raw: Any) -> Optional[list[dict[str, str]]]:
    """The page's turns as Messages API input: alternating user/assistant text, ending on the user, the
    newest MAX_TURNS within MAX_HISTORY_CHARS. None when the shape is wrong."""
    if not isinstance(raw, list) or not raw:
        return None
    turns: list[dict[str, str]] = []
    for t in raw:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant") or not isinstance(t.get("content"), str):
            return None
        text = t["content"].strip()
        if not text:
            continue
        if turns and turns[-1]["role"] == t["role"]:
            turns[-1]["content"] += "\n\n" + text      # two in a row (a stopped answer): merge, keep alternation
        else:
            turns.append({"role": t["role"], "content": text})
    if not turns or turns[-1]["role"] != "user":
        return None
    turns = turns[-MAX_TURNS:]
    while sum(len(t["content"]) for t in turns) > MAX_HISTORY_CHARS and len(turns) > 1:
        turns = turns[1:]
    while turns and turns[0]["role"] != "user":
        turns = turns[1:]
    return turns or None


# ---- Claude -------------------------------------------------------------------------------------

@dataclass
class Answer:
    usage: Any = None
    stop_reason: str = ""


StreamFn = Callable[[list[dict[str, str]], Answer], AsyncIterator[str]]


def claude_stream(model: str, effort: str, client: Any = None) -> StreamFn:
    """Text pieces of Claude's answer as they arrive; the final usage and stop reason land in ``answer``."""
    import anthropic

    api = client or anthropic.AsyncAnthropic()

    async def stream(history: list[dict[str, str]], answer: Answer) -> AsyncIterator[str]:
        async with api.messages.stream(model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                                       messages=history, thinking={"type": "adaptive"},
                                       output_config={"effort": effort}) as s:
            async for text in s.text_stream:
                yield text
            final = await s.get_final_message()
        answer.usage, answer.stop_reason = final.usage, str(final.stop_reason or "")

    return stream


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
    def __init__(self, cfg: MiniAppConfig, stream_fn: StreamFn, ledger: SpendLedger,
                 page: Optional[bytes] = None) -> None:
        self.cfg, self._stream, self.ledger = cfg, stream_fn, ledger
        self._page = page
        self._busy: set[int] = set()                      # one answer at a time per owner
        self._server: Optional[asyncio.base_events.Server] = None
        self.port = 0

    def page(self) -> bytes:
        if self._page is None:
            self._page = PAGE_PATH.read_bytes()
        return self._page

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = (lines[0].split(" ", 2) + ["", ""])[:3]
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
        path = path.split("?", 1)[0]
        if method == "GET" and path in ("/", "/index.html"):
            await self._send(writer, 200, self.page(), "text/html; charset=utf-8")
            return
        if method == "GET" and path == "/healthz":
            await self._send(writer, 200, b"ok", "text/plain")
            return
        if method != "POST" or path not in ("/api/chat", "/api/me"):
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
        user = check_init_data(str(body.get("initData") or ""), self.cfg.token, self.cfg.owner_ids)
        if user is None:
            await self._json(writer, 403, {"error": "Only buddy's owner can use this."})
            return
        if path == "/api/me":
            await self._json(writer, 200, {"model": self.cfg.model, "left": round(self.ledger.left(), 4),
                                           "cap": self.cfg.daily_usd})
            return
        history = clean_history(body.get("messages"))
        if history is None:
            await self._json(writer, 400, {"error": "That conversation could not be read."})
            return
        if self.ledger.left() <= 0:
            await self._json(writer, 429, {"error": CAP_LINE.format(cap=self.cfg.daily_usd)})
            return
        if user in self._busy:
            await self._json(writer, 409, {"error": "Still answering the last question."})
            return
        self._busy.add(user)
        try:
            await self._answer(writer, history)
        finally:
            self._busy.discard(user)

    async def _answer(self, writer: asyncio.StreamWriter, history: list[dict[str, str]]) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
                     b"Cache-Control: no-store\r\nX-Accel-Buffering: no\r\nConnection: close\r\n\r\n")
        await writer.drain()

        async def event(obj: dict[str, Any]) -> None:
            writer.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            await writer.drain()

        answer = Answer()
        t0 = time.perf_counter()
        try:
            async for piece in self._stream(history, answer):
                await event({"t": piece})
        except (ConnectionError, asyncio.CancelledError):
            raise                                        # the phone went away: the stream stops with it
        except Exception as e:  # noqa: BLE001 — every failure becomes a readable line on the phone
            log.warning("miniapp: the answer failed (%s)", type(e).__name__)
            await event({"error": _error_line(e)})
            return
        finally:
            if answer.usage is not None:
                usd = cost_usd(self.cfg.model, answer.usage)
                total = self.ledger.add(usd)
                log.info("miniapp: answered in %.1f s, $%.4f (today $%.4f of $%.2f)", time.perf_counter() - t0,
                         usd, total, self.cfg.daily_usd)
        note = ("Claude declined to answer that." if answer.stop_reason == "refusal"
                else "The answer hit its length limit." if answer.stop_reason == "max_tokens" else "")
        await event({"done": True, "note": note, "left": round(self.ledger.left(), 4)})

    async def _send(self, writer: asyncio.StreamWriter, status: int, body: bytes, ctype: str) -> None:
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
                  413: "Payload Too Large", 429: "Too Many Requests"}.get(status, "OK")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _json(self, writer: asyncio.StreamWriter, status: int, obj: dict[str, Any]) -> None:
        await self._send(writer, status, json.dumps(obj).encode(), "application/json")


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


# ---- the menu button ------------------------------------------------------------------------------

async def set_menu_button(token: str, chat_id: int, url: str = "", *, post: Any = None) -> bool:
    """Point one owner's menu button at the Mini App (``url``), or back to the / commands (no url)."""
    import httpx

    button: dict[str, Any] = ({"type": "web_app", "text": MENU_TEXT, "web_app": {"url": url}} if url
                              else {"type": "commands"})
    try:
        if post is not None:
            data = await post("setChatMenuButton", {"chat_id": chat_id, "menu_button": button})
        else:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"https://api.telegram.org/bot{token}/setChatMenuButton",
                                 json={"chat_id": chat_id, "menu_button": button})
                data = r.json()
    except Exception as e:  # noqa: BLE001 — a menu that did not change is logged, never fatal
        log.warning("miniapp: setChatMenuButton failed (%s)", type(e).__name__)
        return False
    ok = bool(isinstance(data, dict) and data.get("ok"))
    if not ok:
        log.warning("miniapp: setChatMenuButton refused (%s)", str(data.get("description") if isinstance(data, dict) else data)[:120])
    return ok


# ---- the whole thing, for the daemon's life ---------------------------------------------------------

class MiniApp:
    def __init__(self, cfg: MiniAppConfig, *, stream_fn: Optional[StreamFn] = None,
                 tunnel_factory: Optional[Callable[[], Any]] = None,
                 menu: Callable[..., Awaitable[bool]] = set_menu_button) -> None:
        self.cfg = cfg
        self.server = MiniAppServer(cfg, stream_fn or claude_stream(cfg.model, cfg.effort),
                                    SpendLedger(cfg.ledger_path, cfg.daily_usd))
        binary = cloudflared_bin()
        self._tunnel_factory = tunnel_factory or ((lambda: QuickTunnel(binary)) if binary else None)
        self._menu = menu
        self.url = ""

    async def _point_menus(self, url: str) -> None:
        for chat in sorted(self.cfg.owner_ids):           # a private chat's id is the owner's user id
            await self._menu(self.cfg.token, chat, url)

    async def run(self) -> None:
        if self._tunnel_factory is None:
            log.warning("miniapp: cloudflared is not installed (brew install cloudflared); the Mini App is off")
            return
        port = await self.server.start()
        log.info("miniapp: serving on 127.0.0.1:%d (%s, effort %s, $%.2f a day)", port, self.cfg.model,
                 self.cfg.effort, self.cfg.daily_usd)
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
                log.info("miniapp: live at %s; menu button \"%s\" points there", self.url, MENU_TEXT)
                await self._point_menus(self.url)
                code = await tunnel.wait()
                log.warning("miniapp: the tunnel exited (%s); starting a new one", code)
                self.url = ""
                await asyncio.sleep(TUNNEL_RETRY_SECS)
        finally:
            if tunnel is not None:
                await tunnel.stop()
            await self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.shield(self._point_menus(""))  # the / menu again, not a dead Mini App link
