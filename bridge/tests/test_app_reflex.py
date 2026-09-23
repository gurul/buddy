"""app_reflex.py: a bare app launch is `open -a`, everything else is the wrapped agent's."""

from __future__ import annotations

import asyncio
from typing import Any

from cc_buddy_bridge import app_reflex
from cc_buddy_bridge.app_reflex import ReflexFirstAgent

APPS = ("Spotify", "Notes", "Safari", "Warp")


class Inner:
    provider = "codex"

    def __init__(self) -> None:
        self.running = False
        self.goals: list[str] = []
        self.cancelled = ""

    async def run(self, goal: str) -> str:
        self.goals.append(goal)
        return "codex did it"

    def cancel(self, reason: str = "") -> None:
        self.cancelled = reason

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "provider": self.provider}


def rig(*, ok: bool = True, asker: Any = None, enabled: bool = True):
    events: list[tuple[str, str]] = []
    opened: list[str] = []

    async def opener(app: str) -> tuple[bool, str]:
        opened.append(app)
        return ok, "" if ok else "Unable to find application"

    inner = Inner()
    agent = ReflexFirstAgent(lambda: inner, lambda ev: events.append((ev.kind, ev.text)), apps=lambda: APPS,
                             asker=asker, opener=opener, enabled=enabled,
                             on_done=lambda: events.append(("done", "")))
    return agent, inner, opened, events


def test_bare_launch_is_opened_without_the_inner_agent() -> None:
    agent, inner, opened, events = rig()
    assert asyncio.run(agent.run("open Spotify")) == "Opened Spotify."
    assert opened == ["Spotify"] and inner.goals == [] and agent.reflexed == "Spotify"
    assert events == [("started", "open Spotify"), ("final", "Opened Spotify.")]


def test_launch_then_more_goes_to_the_inner_agent_whole() -> None:
    agent, inner, opened, _ = rig()
    assert asyncio.run(agent.run("open Spotify and play my discover weekly")) == "codex did it"
    assert opened == [] and inner.goals == ["open Spotify and play my discover weekly"]


def test_consequential_and_uninstalled_are_the_inner_agents() -> None:
    agent, inner, opened, _ = rig()
    asyncio.run(agent.run("force quit Spotify"))
    asyncio.run(agent.run("open Photoshop"))
    assert opened == [] and inner.goals == ["force quit Spotify", "open Photoshop"]


def test_failed_open_falls_back_to_the_inner_agent() -> None:
    agent, inner, opened, _ = rig(ok=False)
    assert asyncio.run(agent.run("open Notes")) == "codex did it"
    assert opened == ["Notes"] and inner.goals == ["open Notes"]


def test_jev_answers_wording_the_rules_do_not_know() -> None:
    asked: list[str] = []

    def asker(goal: str, apps: list[str]) -> str:
        asked.append(goal)
        return "Notes"

    agent, inner, opened, _ = rig(asker=asker)
    assert asyncio.run(agent.run("Notes, please.")) == "Opened Notes."
    assert asked and opened == ["Notes"] and inner.goals == []


def test_jev_naming_an_uninstalled_app_is_ignored_and_errors_abstain() -> None:
    agent, inner, opened, _ = rig(asker=lambda goal, apps: "Photoshop")
    asyncio.run(agent.run("Notes, please."))

    def boom(goal: str, apps: list[str]) -> str:
        raise TimeoutError

    agent2, inner2, opened2, _ = rig(asker=boom)
    asyncio.run(agent2.run("Notes, please."))
    assert opened == opened2 == [] and inner.goals == inner2.goals == ["Notes, please."]


def test_disabled_passes_everything_through() -> None:
    agent, inner, opened, _ = rig(enabled=False)
    asyncio.run(agent.run("open Spotify"))
    assert opened == [] and inner.goals == ["open Spotify"]


def test_the_inner_agent_is_built_only_when_the_reflex_declines() -> None:
    built: list[int] = []
    inner = Inner()

    def make() -> Inner:
        built.append(1)
        return inner

    agent = ReflexFirstAgent(make, lambda ev: None, apps=lambda: APPS, opener=lambda app: _ok())
    asyncio.run(agent.run("open Spotify"))
    assert built == []                                  # a launch never spends the warm Codex agent
    asyncio.run(agent.run("open Spotify and play jazz"))
    assert built == [1]


async def _ok() -> tuple[bool, str]:
    return True, ""


def test_on_done_follows_a_handoff_only() -> None:
    agent, _, _, events = rig()
    asyncio.run(agent.run("open Spotify"))
    assert ("done", "") not in events
    asyncio.run(agent.run("play jazz on spotify"))
    assert events[-1] == ("done", "")


def test_cancel_before_the_handoff_never_starts_it_and_after_is_forwarded() -> None:
    agent, inner, _, events = rig()
    agent.cancel(reason="stop")
    assert asyncio.run(agent.run("play jazz on spotify")) == "Stopped." and inner.goals == []
    agent2, inner2, _, _ = rig()
    asyncio.run(agent2.run("play jazz on spotify"))
    agent2.cancel(reason="stop")
    assert inner2.cancelled == "stop" and agent2.provider == "codex" and agent2.running is False
    assert agent2.status() is not None


def test_switches() -> None:
    assert app_reflex.reflexes_on({"CC_BUDDY_REFLEXES": "0"}) is False
    assert app_reflex.reflexes_on({}) is True
    assert app_reflex.jev_asker({"CC_BUDDY_ROUTER_MODEL": "off"}) is None
    assert app_reflex.jev_asker({"CC_BUDDY_ROUTER_MODEL": "jev", "CC_BUDDY_JEV_ROUTE": "openrouter"}) is None  # no key


def test_agent_event_has_one_definition() -> None:
    from cc_buddy_bridge import agent_contract, computer_agent

    assert computer_agent.AgentEvent is agent_contract.AgentEvent
