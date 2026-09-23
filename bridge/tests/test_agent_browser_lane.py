"""ComputerAgent with the browser lane (CC_BUDDY_BROWSER_LANE): a web goal goes to buddy's own browser, is
planned once, and never touches the Mac reflexes; a Mac goal never touches the browser."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from test_agent_plan_once import PlanWorker, _plan_response
from test_computer_agent import FakeClient, _agent

from cc_buddy_bridge import plan_contract as pc
from cc_buddy_bridge.computer_agent import AgentConfig

WEB_PLAN = {"needs_eyes": False, "why": "", "final_say": "Here is the top headline.", "success": None, "steps": [
    {"kind": "open_url", "target": "https://news.google.com", "label_hint": "", "text": "", "key": "",
     "expect": {"kind": "title_contains", "value": "Google News"}, "consequential": False},
    {"kind": "click", "target": "the Top stories link", "label_hint": "Top stories", "text": "", "key": "",
     "expect": None, "consequential": False}]}
WEB_DONE = {"status": "complete", "next_index": 2, "reason": "", "confirm": "", "sentence": "Here is the top headline.",
            "ledger": [{"index": 1, "step": "open url https://news.google.com", "effect": "confirmed"},
                       {"index": 2, "step": "click the Top stories link", "effect": "confirmed"}]}


class FakeLane:
    def __init__(self, results: list[dict[str, Any]], fail: bool = False) -> None:
        self.results, self.runs, self.opened, self.outlines = list(results), [], [], 0
        self.fail = fail

    async def outline(self) -> dict[str, Any]:
        self.outlines += 1
        if self.fail:
            raise RuntimeError("the browser died")
        return {"app": "browser", "lines": ["link: Top stories", "button: Search"], "title": "Google News", "url": ""}

    async def run_plan(self, plan: dict, request: str, start: int = 0, approved=None) -> dict:
        self.runs.append({"plan": plan, "start": start, "approved": dict(approved or {})})
        return self.results.pop(0)

    async def open_url(self, url: str) -> str:
        self.opened.append(url)
        return f"opened {url}"


def _cfg(tmp: Path, **kw) -> AgentConfig:
    kw = {"lane_first": True, "plan_exec": True, "reflexes": True, "verify": False, **kw}
    return AgentConfig(runs_dir=tmp / "runs", **kw)


def test_a_web_goal_is_planned_once_against_the_browser_and_the_mac_is_never_touched(tmp_path: Path) -> None:
    client = FakeClient([_plan_response(WEB_PLAN)])           # a second request would raise
    worker, lane = PlanWorker([]), FakeLane([WEB_DONE])
    a, events = _agent(client, worker, tmp_path, config=_cfg(tmp_path), browser=lane)
    said = asyncio.run(a.run("open google news and show me the top headline"))
    assert said == "Here is the top headline." and client.calls == 1
    assert lane.outlines == 1 and len(lane.runs) == 1 and lane.runs[0]["plan"]["app"] == "browser"
    assert worker.executed == [] and worker.runs == [] and worker.observed == 0      # the Mac lane did nothing
    shown = client.requests[0]["input"][0]["content"][0]["text"]
    assert "Frontmost app: browser" in shown and "link: Top stories" in shown and "Installed apps" not in shown
    assert client.requests[0]["instructions"] == pc.PLAN_INSTRUCTIONS
    assert [e.kind for e in events][-1] == "final"


def test_a_mac_goal_never_touches_the_browser(tmp_path: Path) -> None:
    from test_agent_plan_once import DONE, PLAN

    client = FakeClient([_plan_response(PLAN)])
    worker, lane = PlanWorker([DONE]), FakeLane([])
    a, _ = _agent(client, worker, tmp_path, config=_cfg(tmp_path, reflexes=False), browser=lane)
    assert asyncio.run(a.run("put the calendar on the year view")) == "Calendar is on the year view."
    assert lane.outlines == 0 and lane.runs == [] and lane.opened == [] and len(worker.runs) == 1


def test_a_web_search_is_one_navigation_with_no_model_call(tmp_path: Path) -> None:
    client = FakeClient([])                                     # any model call raises
    worker, lane = PlanWorker([]), FakeLane([])
    a, _ = _agent(client, worker, tmp_path, config=_cfg(tmp_path), browser=lane)
    said = asyncio.run(a.run("search the web for ramen near me"))
    assert lane.opened == ["https://www.google.com/search?q=ramen+near+me"] and client.calls == 0
    assert "ramen near me" in said.lower() or "search" in said.lower()
    assert worker.executed == []                                # the Mac reflex did not open Safari as well


def test_a_browser_that_fails_hands_the_goal_to_todays_loop(tmp_path: Path) -> None:
    from test_computer_agent import _message, _response

    client = FakeClient([_response("r1", _message("The page is open."))])
    worker, lane = PlanWorker([]), FakeLane([], fail=True)
    a, _ = _agent(client, worker, tmp_path, config=_cfg(tmp_path), browser=lane)
    said = asyncio.run(a.run("open github and show my pull requests"))
    assert said == "The page is open." and lane.outlines == 1 and worker.observed == 1
    log = (tmp_path / "runs").glob("*.jsonl")
    text = "".join(p.read_text() for p in log)
    assert '"browser": {"error": "RuntimeError: the browser died"' in text


def test_a_partial_browser_plan_tells_the_loop_what_was_done(tmp_path: Path) -> None:
    from test_computer_agent import _message, _response

    partial = {"status": "partial", "next_index": 1, "reason": "no_match", "confirm": "", "sentence": "",
               "ledger": [{"index": 1, "step": "open url https://news.google.com", "effect": "confirmed"},
                          {"index": 2, "step": "click the Top stories link", "effect": "refused"}]}
    client = FakeClient([_plan_response(WEB_PLAN), _response("r1", _message("Done."))])
    worker, lane = PlanWorker([]), FakeLane([partial])
    a, _ = _agent(client, worker, tmp_path, config=_cfg(tmp_path), browser=lane)
    assert asyncio.run(a.run("open google news and show me the top headline")) == "Done."
    second = json.dumps(client.requests[1])
    assert "a plan already did, in order: open url https://news.google.com" in second and "do not repeat" in second


# ---- run_in_browser: only the browser's turn, for chrome_lane.py -----------------------------------------

WEB_PARTIAL = {"status": "partial", "next_index": 1, "reason": "the button was not there", "confirm": "", "sentence": "",
               "ledger": [{"index": 1, "step": "open url https://news.google.com", "effect": "confirmed"}]}
WEB_ASK = {"status": "needs_human", "next_index": 1, "reason": "", "confirm": "click Place order", "sentence": "",
           "ledger": [{"index": 1, "step": "open url https://news.google.com", "effect": "confirmed"}]}


def _browser_agent(tmp_path: Path, results: list[dict], answers: list[str] = (), fail: bool = False):
    client = FakeClient([_plan_response(WEB_PLAN)])
    lane = FakeLane(results, fail=fail)
    a, events = _agent(client, PlanWorker([]), tmp_path, config=_cfg(tmp_path), browser=lane)
    replies = list(answers)

    async def ask(q: str) -> str:
        return replies.pop(0)

    a.ask_user = ask
    return a, lane, events


def test_run_in_browser_finishes_with_the_answer(tmp_path: Path) -> None:
    a, lane, _ = _browser_agent(tmp_path, [WEB_DONE])
    assert asyncio.run(a.run_in_browser("open google news and show me the top headline")) == ("Here is the top headline.", "")
    assert lane.outlines == 1 and a.running is False


def test_run_in_browser_hands_on_what_it_did_when_it_cannot_finish(tmp_path: Path) -> None:
    a, _, _ = _browser_agent(tmp_path, [WEB_PARTIAL])
    answer, note = asyncio.run(a.run_in_browser("open google news and click Top stories"))
    assert answer == "" and "open url https://news.google.com" in note and "do not repeat" in note


def test_run_in_browser_with_a_broken_browser_hands_everything_on(tmp_path: Path) -> None:
    a, _, _ = _browser_agent(tmp_path, [], fail=True)
    assert asyncio.run(a.run_in_browser("open google news")) == ("", "")


def test_a_sensitive_step_needs_a_clear_yes(tmp_path: Path) -> None:
    a, lane, _ = _browser_agent(tmp_path, [WEB_ASK], answers=["yikes, no"])
    answer, _ = asyncio.run(a.run_in_browser("buy the thing"))
    assert answer == "Okay, I stopped before that: click Place order." and len(lane.runs) == 1
    a, lane, _ = _browser_agent(tmp_path, [WEB_ASK, WEB_DONE], answers=["yes"])
    answer, _ = asyncio.run(a.run_in_browser("buy the thing"))
    assert answer == "Here is the top headline." and lane.runs[1]["approved"] == {"1": "click Place order"}


def test_run_in_browser_without_a_browser_does_nothing(tmp_path: Path) -> None:
    a, _ = _agent(FakeClient([]), PlanWorker([]), tmp_path, config=_cfg(tmp_path))
    assert asyncio.run(a.run_in_browser("anything")) == ("", "")
