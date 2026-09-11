"""think.py: the slow brain's request shape, config, and answer parsing."""

from __future__ import annotations

import pytest

from cc_buddy_bridge.think import (
    ANSWER_MAX_CHARS,
    INSTRUCTIONS,
    ThinkConfig,
    configured,
    make_thinker,
    parse_answer,
    request,
)


def test_request_reasons_hard_searches_the_web_and_keeps_nothing() -> None:
    body = request(ThinkConfig(model="gpt-6-astra", effort="high"), "  what is\n the  answer ")
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"] == {"effort": "high"}
    assert body["tools"] == [{"type": "web_search"}] and body["tool_choice"] == "auto"
    assert body["store"] is False
    assert body["input"] == "what is the answer"
    assert body["instructions"] == INSTRUCTIONS and "spoken" in INSTRUCTIONS


def test_configured_defaults_to_the_voice_backend_model_at_high_effort() -> None:
    cfg = configured({}, backend_model="gpt-6-astra")
    assert cfg == ThinkConfig(enabled=True, model="gpt-6-astra", effort="high", timeout_secs=90.0)
    cfg = configured({"CC_BUDDY_THINK_MODEL": "gpt-5.5", "CC_BUDDY_THINK_EFFORT": "xhigh",
                      "CC_BUDDY_THINK_TIMEOUT_SECS": "120"}, backend_model="gpt-6-astra")
    assert (cfg.model, cfg.effort, cfg.timeout_secs) == ("gpt-5.5", "xhigh", 120.0)
    # nonsense falls back, and is clamped
    cfg = configured({"CC_BUDDY_THINK_EFFORT": "max", "CC_BUDDY_THINK_TIMEOUT_SECS": "1"}, backend_model="m")
    assert (cfg.effort, cfg.timeout_secs) == ("high", 10.0)
    assert configured({"CC_BUDDY_THINK": "0"}, backend_model="m").enabled is False


def test_make_thinker_is_none_without_a_key_a_model_or_when_off() -> None:
    assert make_thinker(ThinkConfig(model="m"), {}) is None                                  # no key
    assert make_thinker(ThinkConfig(model=""), {"OPENAI_API_KEY": "k"}) is None              # no model
    assert make_thinker(ThinkConfig(enabled=False, model="m"), {"OPENAI_API_KEY": "k"}) is None
    thinker = make_thinker(ThinkConfig(model="m"), {"OPENAI_API_KEY": "k"})
    assert thinker is not None and thinker.config.model == "m"


def test_parse_answer_flattens_and_clips() -> None:
    assert parse_answer("  Forty-two.\n\nBecause.  ") == "Forty-two. Because."
    long = parse_answer("x " * ANSWER_MAX_CHARS)
    assert len(long) == ANSWER_MAX_CHARS and long.endswith("…")
    with pytest.raises(ValueError):
        parse_answer("   ")
