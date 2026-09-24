"""websearch.py: one OpenRouter call with the web search server tool (Perplexity by default), read back as an answer
and sources."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from cc_buddy_bridge import websearch as ws


class Reply:
    def __init__(self, body: object) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *a) -> None:
        return None


PAYLOAD = {
    "choices": [{"message": {
        "role": "assistant",
        "content": "  Warriors beat the Lakers 112 to 104 last night,\n per ESPN.  ",
        "annotations": [
            {"type": "url_citation", "url_citation": {"url": "https://espn.com/x", "title": "Warriors 112, Lakers 104",
                                                      "content": "Curry scored 31 " + "z" * 400}},
            {"type": "url_citation", "url_citation": {"url": "https://espn.com/x", "title": "dup", "content": ""}},
            {"type": "url_citation", "url_citation": {"url": "https://nba.com/y", "title": "Box score"}},
            {"type": "other"},
        ]}}],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 60},
}


def test_configured_uses_perplexity_with_an_openrouter_key_and_hosted_search_without() -> None:
    cfg = ws.configured({"OPENROUTER_API_KEY": "r"})
    assert cfg == ws.SearchConfig(engine="openrouter-perplexity", model="google/gemini-3.1-flash-lite",
                                  max_results=5, max_uses=2)
    assert ws.tools_for(cfg) == [ws.WEB_SEARCH_TOOL]
    for env in ({}, {"CC_BUDDY_WEB_SEARCH": "openrouter-perplexity"}, {"CC_BUDDY_WEB_SEARCH": "openrouter-exa"},
                {"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH": "openai"}):
        cfg = ws.configured(env)
        assert cfg.engine == "openai" and ws.tools_for(cfg) == [{"type": "web_search"}]
    assert ws.configured({"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH": "bing"}).engine == "openrouter-perplexity"
    assert ws.configured({"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH": "openrouter-exa"}).engine == "openrouter-exa"
    assert ws.SearchConfig().engine == "openai"                  # a bare config never needs an OpenRouter key
    assert ws.WEB_SEARCH_TOOL["strict"] is True
    assert ws.WEB_SEARCH_TOOL["parameters"]["additionalProperties"] is False
    assert ws.tools_for(ws.configured({"CC_BUDDY_WEB_SEARCH": "off", "OPENROUTER_API_KEY": "r"})) == []
    tuned = ws.configured({"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH_MODEL": "z-ai/glm-5.3-flash",
                           "CC_BUDDY_WEB_SEARCH_RESULTS": "40", "CC_BUDDY_WEB_SEARCH_MAX_USES": "9"})
    assert (tuned.model, tuned.max_results, tuned.max_uses) == ("z-ai/glm-5.3-flash", 10, 5)


def test_an_openai_answer_model_is_never_sent_through_openrouter() -> None:
    # owner, 2026-09-24: OpenAI models run on the OpenAI key directly, not through OpenRouter
    cfg = ws.configured({"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH_MODEL": "openai/gpt-6-luna"})
    assert cfg.model == ws.DEFAULT_MODEL and not cfg.model.startswith("openai/")


def test_the_request_is_one_chat_call_with_the_search_server_tool_and_the_clock() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 9, 24, 18, 36, tzinfo=timezone.utc)
    body = ws.request(ws.SearchConfig(engine="openrouter-perplexity", max_results=3, max_uses=2), "  who won\n the game ",
                      environ={"CC_BUDDY_TIMEZONE": "America/Los_Angeles"}, now=now)
    assert body["model"] == "google/gemini-3.1-flash-lite" and "plugins" not in body
    assert body["tools"] == [{"type": "openrouter:web_search",
                              "parameters": {"engine": "perplexity", "max_results": 3, "max_uses": 2}}]
    assert body["messages"][-1] == {"role": "user", "content": "who won the game"}
    system = body["messages"][0]["content"]
    assert body["messages"][0]["role"] == "system" and body["max_tokens"] == 400 and body["usage"] == {"include": True}
    # the answer step reads the local clock, converted to the owner's zone: pages are not a live clock
    assert "Thursday 2026-09-24 11:36" in system and "America/Los_Angeles" in system
    exa = ws.request(ws.SearchConfig(engine="openrouter-exa"), "x", now=now)
    assert exa["tools"][0]["parameters"]["engine"] == "exa"


def test_parse_reads_the_answer_and_the_citations_once_each() -> None:
    out = ws.parse(PAYLOAD)
    assert out["ok"] and out["answer"] == "Warriors beat the Lakers 112 to 104 last night, per ESPN."
    assert [s["url"] for s in out["sources"]] == ["https://espn.com/x", "https://nba.com/y"]   # deduplicated
    assert out["sources"][0]["title"] == "Warriors 112, Lakers 104"
    assert len(out["sources"][0]["snippet"]) == ws.MAX_SNIPPET_CHARS and out["sources"][1]["snippet"] == ""
    assert out["usage"] == {"in": 1200, "out": 60}
    # no annotations is still an answer; no answer and no source is not
    assert ws.parse({"choices": [{"message": {"content": "It is 3pm."}}]})["sources"] == []
    with pytest.raises(ValueError):
        ws.parse({"choices": [{"message": {"content": "   "}}]})
    with pytest.raises(ValueError):
        ws.parse({"error": "x"})


def test_search_posts_once_and_never_raises() -> None:
    seen: list[tuple[str, dict, float]] = []

    def opener(req, timeout):
        seen.append((req.full_url, json.loads(req.data), timeout))
        assert req.get_header("Authorization") == "Bearer r"
        return Reply(PAYLOAD)

    cfg = ws.SearchConfig(engine="openrouter-perplexity")
    out = ws.search("who won", cfg, key="r", opener=opener, clock=iter([0.0, 0.42]).__next__)
    # no usage.cost in the reply: the engine's list price per search
    assert out["ok"] and out["ms"] == 420 and out["cost_usd"] == 0.005 and len(out["sources"]) == 2
    assert seen[0][0] == ws.URL and seen[0][1]["tools"][0]["parameters"]["engine"] == "perplexity" and seen[0][2] == 20.0

    def priced(req, timeout):
        return Reply({**PAYLOAD, "usage": {**PAYLOAD["usage"], "cost": 0.0056}})

    assert ws.search("who won", cfg, key="r", opener=priced)["cost_usd"] == 0.0056        # OpenRouter's own figure
    # failures are one reason each
    assert ws.search("x", key="") == {"ok": False, "reason": "web search is not set up on this computer (no OpenRouter key)"}
    assert ws.search("   ", key="r") == {"ok": False, "reason": "empty query"}

    def http_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b""))

    assert ws.search("x", key="r", opener=http_error) == {"ok": False, "reason": "the search service answered HTTP 429"}

    def unreachable(req, timeout):
        raise urllib.error.URLError(OSError("no route"))

    assert ws.search("x", key="r", opener=unreachable)["reason"] == "the search service is unreachable"

    def garbage(req, timeout):
        return Reply({"choices": []})

    assert ws.search("x", key="r", opener=garbage)["reason"] == "the search service sent an unreadable reply"

    def slow(req, timeout):
        raise TimeoutError()

    assert ws.search("x", key="r", opener=slow)["reason"] == "the search timed out"
