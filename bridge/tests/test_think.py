"""think.py: the slow brain's request shape, config, and answer parsing."""

from __future__ import annotations

import asyncio
import json

import pytest

from cc_buddy_bridge import websearch
from cc_buddy_bridge.think import (
    ANSWER_MAX_CHARS,
    INSTRUCTIONS,
    OpenAIThinker,
    ThinkConfig,
    configured,
    make_thinker,
    parse_answer,
    question_items,
    request,
)


def test_request_reasons_hard_searches_the_web_and_keeps_nothing() -> None:
    cfg = ThinkConfig(model="gpt-6-astra", effort="high", search=websearch.SearchConfig(engine="openrouter-exa"))
    body = request(cfg, question_items("  what is\n the  answer "))
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"] == {"effort": "high"}
    assert body["tools"] == [websearch.WEB_SEARCH_TOOL] and body["tool_choice"] == "auto"   # Exa through OpenRouter
    assert body["store"] is False and body["include"] == ["reasoning.encrypted_content"]
    assert body["input"] == [{"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": "what is the answer"}]}]
    assert body["instructions"] == INSTRUCTIONS and "spoken" in INSTRUCTIONS
    # without an OpenRouter key the hosted search is offered, so nothing goes dark
    hosted = request(ThinkConfig(model="m", search=websearch.SearchConfig(engine="openai")), question_items("q"))
    assert hosted["tools"] == [{"type": "web_search"}]


def test_a_think_that_searches_gets_the_sources_and_answers_in_a_later_round() -> None:
    requests: list[dict] = []
    searched: list[str] = []
    replies = iter([
        {"status": "completed", "output": [
            {"type": "reasoning", "id": "rs1", "encrypted_content": "abc"},
            {"type": "function_call", "name": "web_search", "call_id": "c1", "arguments": "{\"query\": \"lakers score\"}"}]},
        {"status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Lakers lost 104 to 112."}]}]},
    ])

    async def create(req: dict) -> dict:
        requests.append(req)
        return next(replies)

    def search(query: str) -> dict:
        searched.append(query)
        return {"ok": True, "answer": "Warriors 112, Lakers 104", "sources": [{"url": "https://espn.com"}]}

    cfg = ThinkConfig(model="m", search=websearch.SearchConfig(engine="openrouter-exa"))
    out = asyncio.run(OpenAIThinker(cfg, create=create, search=search)("who won the lakers game"))
    assert out == {"ok": True, "answer": "Lakers lost 104 to 112."} and searched == ["lakers score"]
    second = requests[1]["input"]
    assert [i["type"] for i in second] == ["message", "reasoning", "function_call", "function_call_output"]
    assert json.loads(second[-1]["output"])["sources"] == [{"url": "https://espn.com"}] and second[-1]["call_id"] == "c1"
    # a model that only ever searches is cut off after MAX_SEARCH_ROUNDS and the empty answer is an error
    forever = {"status": "completed", "output": [
        {"type": "function_call", "name": "web_search", "call_id": "c", "arguments": "{\"query\": \"x\"}"}]}

    async def loop(req: dict) -> dict:
        return forever

    with pytest.raises(RuntimeError):
        asyncio.run(OpenAIThinker(cfg, create=loop, search=search)("q"))


def test_configured_defaults_to_the_voice_backend_model_at_high_effort() -> None:
    cfg = configured({}, backend_model="gpt-6-astra")
    assert cfg == ThinkConfig(enabled=True, model="gpt-6-astra", effort="high", timeout_secs=90.0,
                              search=websearch.SearchConfig(engine="openai"))
    assert configured({"OPENROUTER_API_KEY": "r"}, backend_model="m").search.engine == "openrouter-exa"
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
