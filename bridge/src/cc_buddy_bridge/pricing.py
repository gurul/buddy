"""Per-message cost estimation for Claude API usage.

Pure-function module: takes a ``model`` id + ``usage`` dict (as it appears in
~/.claude/projects/*.jsonl assistant records) and returns a USD estimate.

Rates as of 2026-05, USD per million tokens. Two cache-write tiers exist
because the 1-hour extended cache costs ~2× the 5-minute default cache.
Cache reads are ~10% of the input rate. Override rates by editing this
file or fork — there's intentionally no separate config layer to manage.

This is an *estimate*, not a billing source of truth. We don't account for
service_tier discounts, batch pricing, or per-request volume tiers. Good
enough for a heads-up "$N.NN today" in the statusline.
"""

from __future__ import annotations

import re

# input / output / cache_write_5m / cache_write_1h / cache_read
_RATES: dict[str, dict[str, float]] = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.50},
    "sonnet": {"input":  3.0, "output": 15.0, "cache_write_5m":  3.75, "cache_write_1h":  6.0, "cache_read": 0.30},
    "haiku":  {"input":  1.0, "output":  5.0, "cache_write_5m":  1.25, "cache_write_1h":  2.0, "cache_read": 0.10},
}

# Unknown models bill at Sonnet rates — the middle of the road. We log the
# unknown model id once per process at daemon startup so a missing entry is
# surfaced rather than silently mispriced; see jsonl_tailer.py.
_DEFAULT_FAMILY = "sonnet"


def family_of(model_id: str) -> str:
    """Resolve a Claude model id ('claude-opus-4-7', 'claude-haiku-4-5-...') to a rate family."""
    if not model_id:
        return _DEFAULT_FAMILY
    m = model_id.lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    return _DEFAULT_FAMILY


def estimate_cost(model_id: str, usage: dict) -> float:
    """USD cost for a single message's usage object.

    Prefers the per-TTL cache breakdown (``usage.cache_creation.ephemeral_*_input_tokens``)
    because 1-hour cache writes cost 2× the 5-minute rate. Falls back to the
    flat ``cache_creation_input_tokens`` total at the 5-minute rate when the
    breakdown isn't present (older transcripts).
    """
    family = family_of(model_id)
    r = _RATES.get(family, _RATES[_DEFAULT_FAMILY])

    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)

    cache_breakdown = usage.get("cache_creation") or {}
    cache_write_5m = int(cache_breakdown.get("ephemeral_5m_input_tokens") or 0)
    cache_write_1h = int(cache_breakdown.get("ephemeral_1h_input_tokens") or 0)
    if not cache_write_5m and not cache_write_1h:
        cache_write_5m = int(usage.get("cache_creation_input_tokens") or 0)

    total = (
        inp * r["input"]
        + out * r["output"]
        + cache_read * r["cache_read"]
        + cache_write_5m * r["cache_write_5m"]
        + cache_write_1h * r["cache_write_1h"]
    )
    return total / 1_000_000.0


# ---- OpenAI Responses and Jev: the bill per computer task ------------------------------------------------
#
# The Jev Engineering article (0xmovez, 2026-09-18): "Track the bill per completed task. A cheap decision
# that sends a worker down the wrong branch can cost more than the decision itself." Until 2026-09-21 the
# run log had tokens per turn and no money, and nothing at all for Jev. These rates are USD per million
# tokens, grounded the day they were written:
#   developers.openai.com/api/docs/pricing, 2026-09-21 (standard tier; long-context variants cost double)
#   gpt-6-sol and gpt-6-luna: developers.openai.com/api/docs/pricing, 2026-09-24 (standard tier)
#   docs.typesafe.ai/models, 2026-09-21: jev-1.13 $0.042 per million input tokens, output free
# An unknown model prices to None on purpose: a wrong figure in a bill is worse than a blank one.

OPENAI_RATES: dict[str, dict[str, float]] = {
    "gpt-6-astra":   {"input": 10.0, "cached": 1.0,  "output": 50.0},
    "gpt-6-sol":     {"input": 2.0,  "cached": 0.20, "output": 10.0},
    "gpt-6-luna":    {"input": 0.10, "cached": 0.01, "output": 0.50},
    "gpt-5.6-sol":   {"input": 4.0,  "cached": 0.40, "output": 20.0},
    "gpt-5.6-terra": {"input": 2.0,  "cached": 0.20, "output": 12.0},
    "gpt-5.6-luna":  {"input": 0.20, "cached": 0.02, "output": 1.20},
    "gpt-5.4-nano":  {"input": 0.20, "cached": 0.02, "output": 1.25},
    "gpt-5-mini":    {"input": 0.25, "cached": 0.025, "output": 2.00},
}
JEV_INPUT_PER_M = 0.042


def openai_rates(model_id: str) -> dict[str, float] | None:
    """The rate row for a Responses model id, or None. A pinned date suffix or an OpenRouter prefix is
    tolerated ("openai/gpt-5.4-nano", "gpt-6-astra-2026-08-01"); a different model is not guessed."""
    m = (model_id or "").lower().split("/")[-1]
    for name in sorted(OPENAI_RATES, key=len, reverse=True):
        if re.fullmatch(re.escape(name) + r"(?:-\d{4}-\d{2}-\d{2})?(?::[a-z0-9_-]+)?", m):
            return OPENAI_RATES[name]
    return None


def responses_tokens(usage: dict) -> dict[str, int]:
    """{"in", "cached", "out"} from a Responses API usage object; cached tokens are a subset of "in"."""
    details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    return {"in": int(usage.get("input_tokens") or 0), "cached": int(details.get("cached_tokens") or 0),
            "out": int(usage.get("output_tokens") or 0)}


def estimate_openai_cost(model_id: str, usage: dict) -> float | None:
    """USD for one Responses API usage object at the table's rates, or None for a model not in it."""
    r = openai_rates(model_id)
    if r is None:
        return None
    t = responses_tokens(usage)
    uncached = max(0, t["in"] - t["cached"])
    return (uncached * r["input"] + t["cached"] * r["cached"] + t["out"] * r["output"]) / 1_000_000.0


def estimate_jev_cost(input_tokens: int) -> float:
    """USD for Jev input tokens; output is free."""
    return max(0, int(input_tokens or 0)) * JEV_INPUT_PER_M / 1_000_000.0

