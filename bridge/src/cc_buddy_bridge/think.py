"""Think: the slow brain behind the voice, for questions that need real reasoning.

The Live model (gpt-live-1) is the receptionist: it keeps the conversation
going and delegates. Its Responses backend runs at low effort so every
delegation answers fast. A hard question — maths, code, logic, a plan, a
comparison — deserves more than that, so the backend's ``think_hard`` tool
hands it here: one Responses call at high effort, with web search available,
that may take tens of seconds while the voice keeps the owner company.

Web search uses OpenAI's hosted tool by default. Explicitly selecting Exa in
websearch.py enables function-call rounds: the model asks, this module searches
off the loop, the sources go back, and the model answers.

Two halves, as in scene.py: a pure core (``request`` builds the exact body,
``parse_answer`` reads it, ``parse_calls`` finds the searches) that runs in
tests, and ``OpenAIThinker``, the only part that touches the network.
``store=False`` on every call: the question and the answer are not kept on
OpenAI's side, so each round resends the items with the reasoning's
``encrypted_content`` (the same shape as telegram.py's turn).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from . import system_context, websearch

log = logging.getLogger(__name__)

DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT_SECS = 90.0
MAX_OUTPUT_TOKENS = 1200
ANSWER_MAX_CHARS = 700          # spoken, then paged 4 lines at a time: keep it short
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
MAX_SEARCH_ROUNDS = 3           # a think may search, read, search again; then it answers with what it has

INSTRUCTIONS = (
    "You are the slow, careful brain of buddy, a small desk robot that talks with its owner by voice. "
    "Work the question out properly, searching the web when the answer depends on current facts. "
    "Then answer in at most three short spoken sentences: the result first, then only the reasoning "
    "the owner needs to trust it. No lists, no markdown, no URLs, no citations. If you cannot answer, "
    "say what is missing in one sentence."
)

Thinker = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ThinkConfig:
    enabled: bool = True
    model: str = ""                 # "" → the voice backend's model
    effort: str = DEFAULT_EFFORT
    timeout_secs: float = DEFAULT_TIMEOUT_SECS
    search: websearch.SearchConfig = field(default_factory=websearch.SearchConfig)


def configured(environ: Any = None, backend_model: str = "") -> ThinkConfig:
    """``CC_BUDDY_THINK=0`` turns it off; ``CC_BUDDY_THINK_MODEL`` / ``_EFFORT`` tune it."""
    env = os.environ if environ is None else environ
    enabled = (env.get("CC_BUDDY_THINK") or "1").strip().lower() not in ("0", "false", "no", "off")
    model = (env.get("CC_BUDDY_THINK_MODEL") or "").strip() or backend_model
    effort = (env.get("CC_BUDDY_THINK_EFFORT") or DEFAULT_EFFORT).strip().lower()
    if effort not in EFFORTS:
        log.warning("think: CC_BUDDY_THINK_EFFORT=%r is not a reasoning effort; using %s", effort, DEFAULT_EFFORT)
        effort = DEFAULT_EFFORT
    timeout = DEFAULT_TIMEOUT_SECS
    raw = (env.get("CC_BUDDY_THINK_TIMEOUT_SECS") or "").strip()
    if raw:
        try:
            timeout = min(300.0, max(10.0, float(raw)))
        except ValueError:
            log.warning("think: CC_BUDDY_THINK_TIMEOUT_SECS=%r is not a number; using %s", raw, timeout)
    return ThinkConfig(enabled=enabled, model=model, effort=effort, timeout_secs=timeout,
                       search=websearch.configured(env))


def question_items(question: str) -> list[dict[str, Any]]:
    return [{"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": " ".join(question.split())}]}]


def request(config: ThinkConfig, items: list[dict[str, Any]]) -> dict[str, Any]:
    """The exact Responses body for one round (tests check store=False, the effort and the search tool).
    `items` is the question, then, on a later round, the carried reasoning, the calls and their outputs."""
    return {
        "model": config.model,
        "instructions": INSTRUCTIONS + system_context.context(),
        "input": items,
        "reasoning": {"effort": config.effort},
        "tools": websearch.tools_for(config.search),
        "tool_choice": "auto",
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }


def parse_calls(output: Any) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """→ (web_search calls, the answer text, the items to carry into the next round)."""
    calls: list[dict[str, Any]] = []
    texts: list[str] = []
    carry: list[dict[str, Any]] = []
    for item in output if isinstance(output, list) else []:
        item = item if isinstance(item, dict) else getattr(item, "model_dump", lambda **k: {})(exclude_none=True)
        kind = item.get("type")
        if kind == "function_call" and item.get("name") == websearch.TOOL_NAME:
            try:
                args = json.loads(item.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"call_id": item.get("call_id", ""), "query": str((args or {}).get("query") or "")})
            carry.append(item)
        elif kind == "reasoning":
            carry.append(item)
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text" and part.get("text"):
                    texts.append(part["text"])
    return calls, "\n".join(texts), carry


def parse_answer(text: str) -> str:
    answer = " ".join((text or "").split())
    if not answer:
        raise ValueError("empty answer")
    if len(answer) > ANSWER_MAX_CHARS:
        answer = answer[: ANSWER_MAX_CHARS - 1].rstrip() + "…"
    return answer


class OpenAIThinker:
    """One high-effort Responses round per question, no retries: the voice is waiting, and a second attempt
    would double the wait. A round that only searched is followed by another, at most MAX_SEARCH_ROUNDS."""

    def __init__(self, config: ThinkConfig, api_key: Optional[str] = None,
                 create: Optional[Callable[[dict[str, Any]], Awaitable[Any]]] = None,
                 search: Optional[Callable[[str], dict[str, Any]]] = None) -> None:
        self.config = config
        if create is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, timeout=config.timeout_secs, max_retries=0)

            async def create(req: dict[str, Any]) -> Any:
                r = await client.responses.create(**req)
                return r.model_dump(exclude_none=True)
        self._create = create
        self._search = search or (lambda q: websearch.search(q, config.search))

    async def __call__(self, question: str) -> dict[str, Any]:
        items = question_items(question)
        text = ""
        for _round in range(MAX_SEARCH_ROUNDS + 1):
            resp = await self._create(request(self.config, items))
            body = resp if isinstance(resp, dict) else resp.model_dump(exclude_none=True)
            calls, text, carry = parse_calls(body.get("output"))
            if not calls or _round == MAX_SEARCH_ROUNDS:
                break
            items = items + carry
            for call in calls:
                result = await asyncio.to_thread(self._search, call["query"])
                items.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)})
        if not text.strip():
            raise RuntimeError(f"empty answer (status={body.get('status')}, incomplete={body.get('incomplete_details')})")
        return {"ok": True, "answer": parse_answer(text)}


def make_thinker(config: ThinkConfig, environ: Any = None) -> Optional[Thinker]:
    """The real thinker, or None (with one log line) when it cannot run."""
    env = os.environ if environ is None else environ
    if not config.enabled:
        log.info("think: off (CC_BUDDY_THINK=0) — hard questions get the fast backend only")
        return None
    if not config.model:
        log.warning("think: no model configured — hard questions get the fast backend only")
        return None
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        log.warning("think: OPENAI_API_KEY not set — hard questions get the fast backend only")
        return None
    try:
        thinker = OpenAIThinker(config, api_key=key)
    except ImportError as e:
        log.warning("think: openai SDK not importable (%s) — no deep reasoning", e)
        return None
    log.info("think: hard questions go to %s at %s effort (up to %.0f s); web search %s",
             config.model, config.effort, config.timeout_secs, config.search.engine)
    return thinker
