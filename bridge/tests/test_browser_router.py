"""browser_router.py (Jev picks the body for a task the reflex declined) and the wrapper's second-body seam.

The isolated body these route to was auto-browser until 2026-09-23 (retired: it needs Docker and has no
scored results); buddy's own Playwright lane is the intended body once it has an evaluation set. The router
and its blind-tested gates are body-agnostic, and so is the seam in app_reflex.ReflexFirstAgent.
"""

from __future__ import annotations

import asyncio
from typing import Any

from cc_buddy_bridge import browser_router as br
from cc_buddy_bridge.app_reflex import ReflexFirstAgent

# ---- the router ---------------------------------------------------------------------------------

def answer(p_auto: float, accounts: float, mac: float, public: float, show: float) -> dict[str, Any]:
    return {"answers": {"body": {"probabilities": {"sandbox_browser": p_auto, "owner_chrome": 1 - p_auto}},
                        "needs_accounts": {"noul": accounts}, "needs_mac": {"noul": mac},
                        "public_web_job": {"noul": public}, "show_owner": {"noul": show}}}


def test_router_needs_every_condition() -> None:
    def route(a: dict[str, Any]) -> str:
        return br.make_router(lambda state, qs: a, lambda: 0.0)("anything")

    assert route(answer(0.99, 0.05, 0.02, 0.93, 0.1)) == br.AUTO
    assert route(answer(0.99, 0.6, 0.02, 0.93, 0.1)) == br.CODEX        # needs the owner's accounts
    assert route(answer(0.99, 0.05, 0.9, 0.93, 0.1)) == br.CODEX        # needs the Mac
    assert route(answer(0.99, 0.05, 0.02, 0.93, 0.8)) == br.CODEX       # wants it on their screen
    assert route(answer(0.81, 0.05, 0.02, 0.93, 0.1)) == br.CODEX       # the Choice is not sure enough

    def boom(state: Any, qs: Any) -> Any:
        raise TimeoutError

    assert br.make_router(boom, lambda: 0.0)("x") == br.CODEX


def test_router_state_is_the_request_only_and_the_bodies_are_structured() -> None:
    seen: dict[str, Any] = {}

    def predict(state: Any, qs: dict[str, Any]) -> dict[str, Any]:
        seen.update(state=state, qs=qs)
        return answer(0.0, 1.0, 1.0, 0.0, 1.0)

    br.ask(predict, "  compare   laptops ", lambda: 0.0)
    assert seen["state"] == {"request": "compare laptops"}
    for option in seen["qs"]["body"]["criteria"].values():
        assert set(option) == {"what", "for", "not_for"}
    assert {k for k, q in seen["qs"].items() if q["type"] == "noul"} == {"needs_accounts", "needs_mac", "show_owner",
                                                                          "public_web_job"}


def test_fit_never_allows_an_unsafe_route() -> None:
    answers = [br.BodyAnswer(0.99, 0.05, 0.02, 0.95, 0.1), br.BodyAnswer(0.97, 0.25, 0.02, 0.9, 0.1)]
    g = br.fit(answers, [br.AUTO, br.CODEX])
    assert br.decide(answers[0], g) == br.AUTO and br.decide(answers[1], g) == br.CODEX


# ---- the wiring ---------------------------------------------------------------------------------

class Body:
    def __init__(self, name: str) -> None:
        self.name, self.running, self.goals = name, False, []

    async def run(self, goal: str) -> str:
        self.goals.append(goal)
        return self.name


def test_the_wrapper_sends_a_routed_task_to_the_second_body_and_keeps_reflexes_first() -> None:
    codex, auto = Body("codex"), Body("auto")
    routed: list[str] = []

    async def route_body(goal: str) -> str:
        routed.append(goal)
        return br.ISOLATED if "compare" in goal else br.CODEX

    async def opener(app: str) -> tuple[bool, str]:
        return True, ""

    def agent() -> ReflexFirstAgent:
        return ReflexFirstAgent(lambda: codex, lambda ev: None, apps=lambda: ["Spotify"], opener=opener,
                                make_auto=lambda: auto, route_body=route_body)

    assert asyncio.run(agent().run("open Spotify")) == "Opened Spotify."        # the reflex never asks the router
    a = agent()
    assert asyncio.run(a.run("compare three laptops")) == "auto" and a.provider == "isolated-browser"
    assert asyncio.run(agent().run("check my gmail")) == "codex"
    assert routed == ["compare three laptops", "check my gmail"]

    async def broken(goal: str) -> str:
        raise RuntimeError

    b = ReflexFirstAgent(lambda: codex, lambda ev: None, apps=lambda: [], make_auto=lambda: auto, route_body=broken)
    assert asyncio.run(b.run("compare laptops")) == "codex"
