"""Think: the slow brain behind the voice, for questions that need real reasoning.

The Live model (gpt-live-1) is the receptionist: it keeps the conversation
going and delegates. Its Responses backend runs at low effort so every
delegation answers fast. A hard question — maths, code, logic, a plan, a
comparison — deserves more than that, so the backend's ``think_hard`` tool
hands it here: one Responses call at high effort, with web search available,
that may take tens of seconds while the voice keeps the owner company.

Two halves, as in scene.py: a pure core (``request`` builds the exact body,
``parse_answer`` reads it) that runs in tests, and ``OpenAIThinker``, the only
part that touches the network. ``store=False`` on every call: the question
and the answer are not kept on OpenAI's side.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT_SECS = 90.0
MAX_OUTPUT_TOKENS = 1200
ANSWER_MAX_CHARS = 700          # spoken, then paged 4 lines at a time: keep it short
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")

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
    return ThinkConfig(enabled=enabled, model=model, effort=effort, timeout_secs=timeout)


def request(config: ThinkConfig, question: str) -> dict[str, Any]:
    """The exact Responses body (tests check store=False, the effort and web search)."""
    return {
        "model": config.model,
        "instructions": INSTRUCTIONS,
        "input": " ".join(question.split()),
        "reasoning": {"effort": config.effort},
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }


def parse_answer(text: str) -> str:
    answer = " ".join((text or "").split())
    if not answer:
        raise ValueError("empty answer")
    if len(answer) > ANSWER_MAX_CHARS:
        answer = answer[: ANSWER_MAX_CHARS - 1].rstrip() + "…"
    return answer


class OpenAIThinker:
    """One high-effort Responses call per question, no retries: the voice is
    waiting, and a second attempt would double the wait."""

    def __init__(self, config: ThinkConfig, api_key: Optional[str] = None) -> None:
        from openai import AsyncOpenAI

        self.config = config
        self._client = AsyncOpenAI(api_key=api_key, timeout=config.timeout_secs, max_retries=0)

    async def __call__(self, question: str) -> dict[str, Any]:
        resp = await self._client.responses.create(**request(self.config, question))
        text = resp.output_text or ""
        if not text.strip():
            raise RuntimeError(f"empty answer (status={resp.status}, incomplete={resp.incomplete_details})")
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
    log.info("think: hard questions go to %s at %s effort (up to %.0f s)",
             config.model, config.effort, config.timeout_secs)
    return thinker
