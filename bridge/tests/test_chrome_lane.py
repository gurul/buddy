"""chrome_lane.ChromeLaneAgent: plan once in the owner's Chrome, Codex for whatever is left."""

from __future__ import annotations

import asyncio
from typing import Any

from cc_buddy_bridge.agent_contract import AgentEvent
from cc_buddy_bridge.chrome_lane import ChromeLaneAgent


class Planner:
    def __init__(self, result: Any, on_event: Any) -> None:
        self.result, self.on_event, self.running, self.goals = result, on_event, False, []
        self.cancelled = ""

    async def run_in_browser(self, goal: str) -> tuple[str, str]:
        self.goals.append(goal)
        self.on_event(AgentEvent("started", goal))
        self.on_event(AgentEvent("progress", "opened gmail"))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def cancel(self, reason: str = "") -> None:
        self.cancelled = reason


class Codex:
    def __init__(self) -> None:
        self.running, self.goals = False, []

    async def run(self, goal: str) -> str:
        self.goals.append(goal)
        return "codex finished it"


def rig(result: Any):
    events: list[tuple[str, str]] = []
    made: dict[str, Any] = {}

    def make_planner(on_event, ask_user):
        made["planner"] = Planner(result, on_event)
        return made["planner"]

    def make_fallback():
        made["codex"] = Codex()
        return made["codex"]

    async def ask(q: str) -> str:
        return "yes"

    agent = ChromeLaneAgent(make_planner, make_fallback, lambda ev: events.append((ev.kind, ev.text)), ask)
    return agent, made, events


def test_a_finished_plan_answers_and_codex_never_starts() -> None:
    agent, made, events = rig(("You have 3 unread emails.", ""))
    assert asyncio.run(agent.run("how many unread emails do I have")) == "You have 3 unread emails."
    assert "codex" not in made and agent.handed_on is False
    assert events == [("started", "how many unread emails do I have"), ("progress", "opened gmail"),
                      ("final", "You have 3 unread emails.")]                     # the planner's own start is not repeated


def test_part_done_hands_codex_the_goal_and_what_was_done() -> None:
    agent, made, events = rig(("", "\n\n[note] a plan already did: open gmail."))
    assert asyncio.run(agent.run("archive the newsletters")) == "codex finished it"
    assert made["codex"].goals == ["archive the newsletters\n\n[note] a plan already did: open gmail."]
    assert agent.handed_on and any(k == "progress" and "Codex is finishing" in t for k, t in events)


def test_nothing_done_gives_codex_the_whole_task() -> None:
    agent, made, _ = rig(("", ""))
    asyncio.run(agent.run("reply to Sam"))
    assert made["codex"].goals == ["reply to Sam"]


def test_a_broken_lane_never_costs_the_task() -> None:
    agent, made, _ = rig(RuntimeError("Chrome went away"))
    assert asyncio.run(agent.run("check my orders")) == "codex finished it" and made["codex"].goals == ["check my orders"]


def test_the_owners_no_is_the_answer_and_codex_does_not_retry_it() -> None:
    agent, made, _ = rig(("Okay, I stopped before that: click Place order.", ""))
    assert asyncio.run(agent.run("buy the batteries")) == "Okay, I stopped before that: click Place order."
    assert "codex" not in made


def test_a_cancel_stops_without_handing_on() -> None:
    agent, made, events = rig(("", "\n\n[note] something"))

    async def go() -> str:
        agent._cancel_reason = "stopped from Telegram"          # as cancel() does while the lane runs
        return await agent.run("anything")

    assert asyncio.run(go()) == "Stopped." and "codex" not in made and events[-1][0] == "cancelled"


# ---- the daemon's wiring -------------------------------------------------------------------------------

def test_with_the_lane_off_nothing_changes() -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    assert Daemon._chrome_body(SimpleNamespace(_chrome_lane=None), lambda: None, lambda ev: None, None) == {}


def test_with_the_lane_on_web_goals_take_the_chrome_body_and_the_rest_stay_codex() -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    host = SimpleNamespace(_chrome_lane=object(), _telegram=None, _planner_create=lambda req: None,
                           _agent_cfg=SimpleNamespace())
    wiring = Daemon._chrome_body(host, lambda: None, lambda ev: None, None)
    route = wiring["route_body"]
    assert asyncio.run(route("how many unread emails are in my gmail inbox")) == "chrome"
    assert asyncio.run(route("search amazon for AA batteries")) == "chrome"
    assert asyncio.run(route("open spotify and play jazz")) == "codex"
    assert asyncio.run(route("put the calendar on year view")) == "codex"
    assert wiring["make_auto"]().provider == "chrome-lane"


def test_chrome_approval_is_asked_in_the_owners_telegram_chat() -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    asked = []

    async def ask_user(q, chat, title=""):
        asked.append((q, chat, title))
        return "yes"

    host = SimpleNamespace(_telegram=SimpleNamespace(_chat_id=4242, _ask_user=ask_user))
    assert asyncio.run(Daemon._ask_owner_on_phone(host, "Allow?")) == "yes"
    assert asked == [("Allow?", 4242, "Chrome access")]
    try:
        asyncio.run(Daemon._ask_owner_on_phone(SimpleNamespace(_telegram=None), "Allow?"))
        raise AssertionError("expected no way to ask")
    except RuntimeError:
        pass


def test_a_dead_connection_is_replaced_on_the_next_connect() -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.browser_lane import BrowserLane, BrowserLaneConfig

    lane = BrowserLane(BrowserLaneConfig(enabled=True, attach=True))
    events = []
    lane._context, lane._browser = object(), SimpleNamespace(is_connected=lambda: False)
    lane._close = lambda: (events.append("closed"), setattr(lane, "_context", None), setattr(lane, "_browser", None))
    lane._ensure = lambda: (events.append("connected"), setattr(lane, "_context", object()))

    async def prompt():
        events.append("asked")

    asyncio.run(lane.connect(prompt))
    # the dead one closes first; the new connection and the owner's consent then run side by side
    assert events[0] == "closed" and sorted(events[1:]) == ["asked", "connected"]
    events.clear()
    lane._browser = SimpleNamespace(is_connected=lambda: True)
    asyncio.run(lane.connect(prompt))
    assert events == []                                      # alive: no reconnect, no new question


def test_stop_while_waiting_for_chromes_allow_is_immediate() -> None:
    import time as _time

    agent, made, events = rig(("never", ""))

    async def slow_prepare(goal: str) -> None:
        await asyncio.sleep(30)                     # the owner has not answered yet

    agent._prepare = slow_prepare

    async def go() -> tuple[str, float]:
        t = _time.perf_counter()
        task = asyncio.ensure_future(agent.run("check my gmail"))
        await asyncio.sleep(0.05)
        agent.cancel("stopped from Telegram")
        return await task, _time.perf_counter() - t

    said, secs = asyncio.run(go())
    assert said == "Stopped." and secs < 1 and "codex" not in made and events[-1][0] == "cancelled"
