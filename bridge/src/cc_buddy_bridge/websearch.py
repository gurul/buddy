"""Shared web-search configuration for voice, text and deep reasoning.

Perplexity through OpenRouter is the default whenever ``OPENROUTER_API_KEY`` is set (owner, 2026-09-24: "use this
benchmark to wire in good realtime search"). On OpenRouter's search benchmarks Perplexity led BrowseComp, HLE and
WideSearch on quality, value and speed with the model held fixed (BrowseComp, 2026-08-18: 89.0% with Claude Opus 5
against 82.2% on Exa), and it costs $0.005 a search against Exa's $0.007. One OpenRouter chat call carries the
``openrouter:web_search`` server tool on the chosen engine, capped at ``max_uses`` searches; a cheap non-OpenAI model
(owner: OpenAI models are called on OpenAI directly, never through OpenRouter) writes the short answer from the
results, and the brain gets the answer and its sources through the ``web_search`` function tool.

Search engines do not serve a live clock (Exa could not say the time in Seattle, 2026-09-21, and Perplexity cannot
either, 2026-09-24), so the answer step is told the local date and time: a clock or "today" question is answered
from it, not from a page. Without an OpenRouter key, OpenAI's hosted ``web_search`` (on the OpenAI key) is used.
``CC_BUDDY_WEB_SEARCH`` = openrouter-perplexity | openrouter-exa | openai | off picks one explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
ENGINES = ("openrouter-perplexity", "openrouter-exa", "openai", "off")
OPENROUTER_ENGINES = {"openrouter-perplexity": "perplexity", "openrouter-exa": "exa"}   # buddy's name -> OpenRouter's
DEFAULT_ENGINE = "openrouter-perplexity"   # with OPENROUTER_API_KEY; without it, "openai" (hosted, on the OpenAI key)
# Writes the answer from the search results. Measured 2026-09-24 on the same three live questions with Perplexity:
# google/gemini-3.1-flash-lite 3.7 s and $0.0056 a question, all answered; z-ai/glm-5.3-flash 13.2 s and
# deepseek/deepseek-v4-flash-0731 13.1 s, each returning an empty answer to one of the three.
DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_RESULTS = 5
DEFAULT_MAX_USES = 2                       # searches per question: realtime answers, and at most $0.01 in search fees
DEFAULT_TIMEOUT_SECS = 20.0
MAX_ANSWER_CHARS = 1500
MAX_SNIPPET_CHARS = 300
MAX_SOURCES = 8
# Per search, up to 10 results (openrouter.ai/docs server-tools/web-search, 2026-09-24); the model's tokens come on
# top. Used only when OpenRouter's reply carries no usage.cost.
SEARCH_USD = {"perplexity": 0.005, "exa": 0.007}

TOOL_NAME = "web_search"
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function", "name": TOOL_NAME, "strict": True,
    "description": "Search the web for current facts: news, scores, prices, weather, opening hours, anything after "
                   "your training. Returns a short answer and the sources it came from. The current date and time are "
                   "already in your context; a search is not needed for them.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                   "properties": {"query": {"type": "string",
                                            "description": "What to look up, as a search query in plain words."}}},
}
HOSTED_TOOL: dict[str, Any] = {"type": "web_search"}      # OpenAI's own, the default

SYSTEM = ("You are a search engine's answer box. Using only the web results provided, answer the query in at most "
          "four plain sentences with the concrete facts (numbers, names, dates) and say which source each comes "
          "from by its title. If the results do not answer it, say so in one sentence. No markdown, no lists.")
# The clock the answer step reads: web pages are not a live clock, so the current time comes from here.
CLOCK = ("The current local date and time is {now}, time zone {zone}. For a question about the current time or date, "
         "or what is happening today, use this clock, converting to another place's time zone when asked; do not "
         "take the time from a web page.")


@dataclass(frozen=True)
class SearchConfig:
    engine: str = "openai"                    # a bare config (no environment read) never needs an OpenRouter key
    model: str = DEFAULT_MODEL
    max_results: int = DEFAULT_RESULTS
    timeout_secs: float = DEFAULT_TIMEOUT_SECS
    max_uses: int = DEFAULT_MAX_USES


def configured(environ: Any = None) -> SearchConfig:
    """CC_BUDDY_WEB_SEARCH = openrouter-perplexity | openrouter-exa | openai | off. Unset: openrouter-perplexity
    when OPENROUTER_API_KEY is set, else openai. CC_BUDDY_WEB_SEARCH_MODEL, _RESULTS and _MAX_USES tune the call.
    An openai/* answer model is refused (OpenAI models run on the OpenAI key, never through OpenRouter)."""
    env = os.environ if environ is None else environ
    key = (env.get("OPENROUTER_API_KEY") or "").strip()
    raw = (env.get("CC_BUDDY_WEB_SEARCH") or "").strip().lower()
    if raw and raw not in ENGINES:
        log.warning("web search: CC_BUDDY_WEB_SEARCH=%r is not one of %s; using the default", raw, ENGINES)
        raw = ""
    engine = raw or (DEFAULT_ENGINE if key else "openai")
    if engine in OPENROUTER_ENGINES and not key:
        log.warning("web search: asked for %s but OPENROUTER_API_KEY is not set; the hosted search is offered", engine)
        engine = "openai"
    model = (env.get("CC_BUDDY_WEB_SEARCH_MODEL") or DEFAULT_MODEL).strip()
    if model.lower().startswith("openai/"):
        log.warning("web search: %s would run an OpenAI model through OpenRouter; using %s", model, DEFAULT_MODEL)
        model = DEFAULT_MODEL
    try:
        n = max(1, min(10, int(env.get("CC_BUDDY_WEB_SEARCH_RESULTS") or DEFAULT_RESULTS)))
    except ValueError:
        n = DEFAULT_RESULTS
    try:
        uses = max(1, min(5, int(env.get("CC_BUDDY_WEB_SEARCH_MAX_USES") or DEFAULT_MAX_USES)))
    except ValueError:
        uses = DEFAULT_MAX_USES
    return SearchConfig(engine=engine, model=model, max_results=n, max_uses=uses)


def tools_for(config: SearchConfig) -> list[dict[str, Any]]:
    """The tool(s) a brain offers for the web: the function tool, the hosted one, or nothing."""
    if config.engine in OPENROUTER_ENGINES:
        return [WEB_SEARCH_TOOL]
    if config.engine == "openai":
        return [HOSTED_TOOL]
    return []


def _clock(environ: Any = None, now: Optional[datetime] = None) -> str:
    env = os.environ if environ is None else environ
    zone_name = (env.get("CC_BUDDY_TIMEZONE") or "").strip()
    try:
        zone = ZoneInfo(zone_name) if zone_name else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    local = (now or datetime.now().astimezone()).astimezone(zone)
    return CLOCK.format(now=local.strftime("%A %Y-%m-%d %H:%M"), zone=zone.key if zone else local.tzname())


def request(config: SearchConfig, query: str, *, environ: Any = None, now: Optional[datetime] = None) -> dict[str, Any]:
    """The exact OpenRouter body: the cheap answer model, the web search server tool on the engine with its budget,
    the clock and the answer-box rules as the system turn, the query as the user turn."""
    return {
        "model": config.model,
        "tools": [{"type": "openrouter:web_search",
                   "parameters": {"engine": OPENROUTER_ENGINES.get(config.engine, "perplexity"),
                                  "max_results": config.max_results, "max_uses": config.max_uses}}],
        "messages": [{"role": "system", "content": SYSTEM + " " + _clock(environ, now)},
                     {"role": "user", "content": " ".join((query or "").split())[:500]}],
        "max_tokens": 400,
        "temperature": 0,
        "usage": {"include": True},
    }


def parse(payload: Any) -> dict[str, Any]:
    """{"ok", "answer", "sources": [{"title", "url", "snippet"}], "usage"} from a chat completion. A reply
    with no annotations is still an answer; a reply with no text is not."""
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list) or not payload["choices"]:
        raise ValueError("no choices in the reply")
    message = (payload["choices"][0] or {}).get("message") or {}
    answer = " ".join(str(message.get("content") or "").split())[:MAX_ANSWER_CHARS]
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for ann in message.get("annotations") or []:
        cite = ann.get("url_citation") if isinstance(ann, dict) else None
        if not isinstance(cite, dict):
            continue
        url = str(cite.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({"title": " ".join(str(cite.get("title") or "").split())[:120], "url": url[:500],
                        "snippet": " ".join(str(cite.get("content") or "").split())[:MAX_SNIPPET_CHARS]})
        if len(sources) >= MAX_SOURCES:
            break
    if not answer and not sources:
        raise ValueError("the reply had neither an answer nor a source")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    out = {"ok": True, "answer": answer, "sources": sources,
           "usage": {"in": int(usage.get("prompt_tokens") or 0), "out": int(usage.get("completion_tokens") or 0)}}
    if isinstance(usage.get("cost"), (int, float)):
        out["usage"]["cost"] = float(usage["cost"])          # OpenRouter's own figure: searches and tokens together
    return out


def search(query: str, config: Optional[SearchConfig] = None, *, key: str = "",
           opener: Optional[Callable[..., Any]] = None, clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """One search, blocking (callers run it off the loop). Never raises: {"ok": False, "reason": …} instead.
    `key` defaults to OPENROUTER_API_KEY. Logs the shape and the time, never the query."""
    cfg = config or configured()
    api_key = key or (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        return {"ok": False, "reason": "web search is not set up on this computer (no OpenRouter key)"}
    if not (query or "").strip():
        return {"ok": False, "reason": "empty query"}
    send = opener or urllib.request.urlopen
    body = json.dumps(request(cfg, query)).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/gurucharan/buddy", "X-Title": "buddy"})
    t0 = clock()
    try:
        with send(req, timeout=cfg.timeout_secs) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result = parse(payload)
    except urllib.error.HTTPError as e:
        log.warning("web search: HTTP %s from OpenRouter", e.code)
        return {"ok": False, "reason": f"the search service answered HTTP {e.code}"}
    except urllib.error.URLError as e:
        log.warning("web search: OpenRouter unreachable (%s)", type(e.reason).__name__)
        return {"ok": False, "reason": "the search service is unreachable"}
    except (TimeoutError, OSError):
        log.warning("web search: timed out after %.0f s", cfg.timeout_secs)
        return {"ok": False, "reason": "the search timed out"}
    except (ValueError, TypeError) as e:
        log.warning("web search: unreadable reply (%s)", e)
        return {"ok": False, "reason": "the search service sent an unreadable reply"}
    ms = (clock() - t0) * 1000.0
    result["ms"] = round(ms)
    engine = OPENROUTER_ENGINES.get(cfg.engine, "perplexity")
    measured = result["usage"].get("cost")
    result["cost_usd"] = round(measured if measured is not None else SEARCH_USD.get(engine, 0.005), 4)
    log.info("web search: %d sources in %.0f ms via %s on %s, $%.4f", len(result["sources"]), ms, cfg.model, engine,
             result["cost_usd"])
    return result
