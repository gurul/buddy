"""Shared web-search configuration for voice, text and deep reasoning.

OpenAI's hosted ``web_search`` is the default, restoring the pre-Exa behavior.
Exa through OpenRouter remains available only with an explicit
``CC_BUDDY_WEB_SEARCH=openrouter-exa`` setting. Its function tool returns
an answer and source annotations from one OpenRouter completion.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
ENGINES = ("openrouter-exa", "openai", "off")
DEFAULT_ENGINE = "openai"                 # hosted GPT search, independent of other provider credentials
DEFAULT_MODEL = "openai/gpt-5.4-nano"      # the cheapest OpenRouter model that carries the web plugin, 2026-09-21
DEFAULT_RESULTS = 5
DEFAULT_TIMEOUT_SECS = 20.0
MAX_ANSWER_CHARS = 1500
MAX_SNIPPET_CHARS = 300
MAX_SOURCES = 8
EXA_PER_REQUEST_USD = 0.007                # up to 10 results; the model's tokens come on top

TOOL_NAME = "web_search"
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function", "name": TOOL_NAME, "strict": True,
    "description": "Search the web for current facts: news, scores, prices, weather, opening hours, anything after "
                   "your training. Returns a short answer and the sources it came from.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                   "properties": {"query": {"type": "string",
                                            "description": "What to look up, as a search query in plain words."}}},
}
HOSTED_TOOL: dict[str, Any] = {"type": "web_search"}      # OpenAI's own, the default

SYSTEM = ("You are a search engine's answer box. Using only the web results provided, answer the query in at most "
          "four plain sentences with the concrete facts (numbers, names, dates) and say which source each comes "
          "from by its title. If the results do not answer it, say so in one sentence. No markdown, no lists.")


@dataclass(frozen=True)
class SearchConfig:
    engine: str = DEFAULT_ENGINE
    model: str = DEFAULT_MODEL
    max_results: int = DEFAULT_RESULTS
    timeout_secs: float = DEFAULT_TIMEOUT_SECS


def configured(environ: Any = None) -> SearchConfig:
    """CC_BUDDY_WEB_SEARCH = openrouter-exa | openai | off. Unset: openai, even when OPENROUTER_API_KEY is
    set. CC_BUDDY_WEB_SEARCH_MODEL and CC_BUDDY_WEB_SEARCH_RESULTS tune the call."""
    env = os.environ if environ is None else environ
    key = (env.get("OPENROUTER_API_KEY") or "").strip()
    raw = (env.get("CC_BUDDY_WEB_SEARCH") or "").strip().lower()
    if raw and raw not in ENGINES:
        log.warning("web search: CC_BUDDY_WEB_SEARCH=%r is not one of %s; using the default", raw, ENGINES)
        raw = ""
    engine = raw or DEFAULT_ENGINE
    if engine == "openrouter-exa" and not key:
        log.warning("web search: asked for openrouter-exa but OPENROUTER_API_KEY is not set; the hosted search is offered")
        engine = "openai"
    model = (env.get("CC_BUDDY_WEB_SEARCH_MODEL") or DEFAULT_MODEL).strip()
    try:
        n = max(1, min(10, int(env.get("CC_BUDDY_WEB_SEARCH_RESULTS") or DEFAULT_RESULTS)))
    except ValueError:
        n = DEFAULT_RESULTS
    return SearchConfig(engine=engine, model=model, max_results=n)


def tools_for(config: SearchConfig) -> list[dict[str, Any]]:
    """The tool(s) a brain offers for the web: the function tool, the hosted one, or nothing."""
    if config.engine == "openrouter-exa":
        return [WEB_SEARCH_TOOL]
    if config.engine == "openai":
        return [HOSTED_TOOL]
    return []


def request(config: SearchConfig, query: str) -> dict[str, Any]:
    """The exact OpenRouter body: the cheap model, the web plugin on Exa, the query as the user turn."""
    return {
        "model": config.model,
        "plugins": [{"id": "web", "engine": "exa", "max_results": config.max_results}],
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": " ".join((query or "").split())[:500]}],
        "max_tokens": 400,
        "temperature": 0,
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
    return {"ok": True, "answer": answer, "sources": sources,
            "usage": {"in": int(usage.get("prompt_tokens") or 0), "out": int(usage.get("completion_tokens") or 0)}}


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
    result["cost_usd"] = round(EXA_PER_REQUEST_USD, 4)       # the model's few hundred tokens are a rounding error
    log.info("web search: %d sources in %.0f ms via %s (exa)", len(result["sources"]), ms, cfg.model)
    return result
