"""websearch.py: one OpenRouter call with the web plugin on Exa, read back as an answer and sources."""

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


def test_configured_picks_openrouter_exa_with_a_key_and_the_hosted_tool_without() -> None:
    cfg = ws.configured({"OPENROUTER_API_KEY": "r"})
    assert cfg == ws.SearchConfig(engine="openrouter-exa", model="openai/gpt-5.4-nano", max_results=5)
    assert ws.tools_for(cfg) == [ws.WEB_SEARCH_TOOL] and ws.WEB_SEARCH_TOOL["name"] == "web_search"
    assert ws.WEB_SEARCH_TOOL["strict"] is True and ws.WEB_SEARCH_TOOL["parameters"]["additionalProperties"] is False
    hosted = ws.configured({})
    assert hosted.engine == "openai" and ws.tools_for(hosted) == [{"type": "web_search"}]
    assert ws.tools_for(ws.configured({"CC_BUDDY_WEB_SEARCH": "off", "OPENROUTER_API_KEY": "r"})) == []
    # asked for exa without a key: the hosted tool, not a dead function
    assert ws.configured({"CC_BUDDY_WEB_SEARCH": "openrouter-exa"}).engine == "openai"
    tuned = ws.configured({"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH_MODEL": "openai/gpt-5.6-luna",
                           "CC_BUDDY_WEB_SEARCH_RESULTS": "40"})
    assert tuned.model == "openai/gpt-5.6-luna" and tuned.max_results == 10          # clamped to Exa's first tier
    assert ws.configured({"OPENROUTER_API_KEY": "r", "CC_BUDDY_WEB_SEARCH": "bing"}).engine == "openrouter-exa"


def test_the_request_is_one_chat_call_with_the_web_plugin_on_exa() -> None:
    body = ws.request(ws.SearchConfig(max_results=3), "  who won\n the game ")
    assert body["model"] == "openai/gpt-5.4-nano"
    assert body["plugins"] == [{"id": "web", "engine": "exa", "max_results": 3}]
    assert body["messages"][-1] == {"role": "user", "content": "who won the game"}
    assert body["messages"][0]["role"] == "system" and body["max_tokens"] == 400


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

    out = ws.search("who won", ws.SearchConfig(), key="r", opener=opener, clock=iter([0.0, 0.42]).__next__)
    assert out["ok"] and out["ms"] == 420 and out["cost_usd"] == 0.007 and len(out["sources"]) == 2
    assert seen[0][0] == ws.URL and seen[0][1]["plugins"][0]["engine"] == "exa" and seen[0][2] == 20.0
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
