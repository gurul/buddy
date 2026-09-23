"""auto_browser.py over a fake controller (httpx.MockTransport), browser_router.py's decision, and the wiring."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx

from cc_buddy_bridge import auto_browser as ab
from cc_buddy_bridge import browser_router as br
from cc_buddy_bridge.app_reflex import ReflexFirstAgent

CFG = ab.AutoBrowserConfig(enabled=True, url="http://ab.test", token="tok", provider="openrouter", rounds=3)


def step(status: str, reason: str = "", approval: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"status": status, "decision": {"action": "done" if status == "done" else "click", "reason": reason},
            "execution": {"approval": approval} if approval else None}


class Controller:
    """The REST contract of auto-browser v1.7.0: sessions, agent/run, approvals, healthz."""

    def __init__(self, runs: list[dict[str, Any]], healthy: bool = True) -> None:
        self.runs, self.healthy = list(runs), healthy
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        assert request.headers.get("authorization") == "Bearer tok"
        path = request.url.path
        if path == "/healthz":
            return httpx.Response(200 if self.healthy else 503, json={"ok": self.healthy})
        if path == "/sessions" and request.method == "POST":
            return httpx.Response(200, json={"id": "s1"})
        if path.endswith("/agent/run"):
            return httpx.Response(200, json=self.runs.pop(0))
        if path.startswith("/approvals/"):
            return httpx.Response(200, json={"ok": True})
        if request.method == "DELETE":
            return httpx.Response(200, json={"closed": True})
        return httpx.Response(404)


def run(controller: Controller, answers: tuple[str, ...] = ()) -> tuple[str, list[tuple[str, str]], list[str]]:
    events: list[tuple[str, str]] = []
    asked: list[str] = []
    replies = list(answers)

    async def ask_user(q: str) -> str:
        asked.append(q)
        return replies.pop(0)

    async def go() -> str:
        client = httpx.AsyncClient(transport=httpx.MockTransport(controller))
        agent = ab.AutoBrowserAgent(on_event=lambda ev: events.append((ev.kind, ev.text)), ask_user=ask_user,
                                    config=CFG, client=client)
        try:
            return await agent.run("compare three laptops")
        finally:
            await client.aclose()

    return asyncio.run(go()), events, asked


def test_done_returns_the_summary_and_closes_the_session() -> None:
    c = Controller([{"status": "done", "steps": [step("acted", "opened"), step("done", "The XPS wins on battery.")]}])
    final, events, _ = run(c)
    assert final == "The XPS wins on battery."
    assert events[0] == ("started", "compare three laptops") and events[-1] == ("final", final)
    run_body = next(b for m, p, b in c.calls if p == "/sessions/s1/agent/run")
    assert run_body == {"provider": "openrouter", "goal": "compare three laptops", "max_steps": 20,
                        "workflow_profile": "fast"}
    assert ("DELETE", "/sessions/s1", None) in c.calls


def test_an_approval_is_the_owners_yes_and_the_next_round_executes_it() -> None:
    approval = {"id": "ap1", "kind": "post", "reason": "submit the form",
                "action": {"action": "click", "label": "Submit"}}
    c = Controller([{"status": "approval_required", "steps": [step("approval_required", "submit", approval)]},
                    {"status": "done", "steps": [step("done", "Form sent.")]}])
    final, _, asked = run(c, ("yes",))
    assert final == "Form sent." and "click Submit" in asked[0] and "(post)" in asked[0]
    assert ("POST", "/approvals/ap1/approve", {"comment": "approved by the owner via buddy"}) in c.calls
    second = [b for m, p, b in c.calls if p.endswith("/agent/run")][1]
    assert second["approval_id"] == "ap1"


def test_a_no_rejects_and_stops() -> None:
    approval = {"id": "ap2", "kind": "payment", "action": {"action": "click", "label": "Pay"}}
    c = Controller([{"status": "approval_required", "steps": [step("approval_required", "pay", approval)]}])
    final, _, _ = run(c, ("no",))
    assert final.startswith("Stopped: you declined")
    assert any(p == "/approvals/ap2/reject" for _, p, _ in c.calls)
    assert sum(1 for _, p, _ in c.calls if p.endswith("/agent/run")) == 1


def test_takeover_points_at_novnc_and_rounds_are_bounded() -> None:
    final, _, _ = run(Controller([{"status": "takeover", "steps": [step("takeover", "a CAPTCHA")]}]))
    assert "take over (a CAPTCHA)" in final and "vnc.html" in final
    many = Controller([{"status": "max_steps_reached", "steps": [step("acted", f"page {i}")]} for i in range(3)])
    final, _, _ = run(many)
    assert final == "auto-browser ran out of steps. Last: page 2"
    assert sum(1 for _, p, _ in many.calls if p.endswith("/agent/run")) == CFG.rounds


def test_a_transport_failure_is_reported_not_raised() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    final, events, _ = run(down)  # type: ignore[arg-type]
    assert "could not finish" in final and events[-1][0] == "error"


def test_available_needs_on_and_a_healthy_controller() -> None:
    async def check(cfg: ab.AutoBrowserConfig, healthy: bool) -> bool:
        client = httpx.AsyncClient(transport=httpx.MockTransport(Controller([], healthy)))
        try:
            return await ab.available(cfg, client)
        finally:
            await client.aclose()

    assert asyncio.run(check(CFG, True)) is True
    assert asyncio.run(check(CFG, False)) is False
    assert asyncio.run(check(ab.AutoBrowserConfig(enabled=False), True)) is False


def test_configured_ships_off_and_clamps() -> None:
    assert ab.configured({}).enabled is False
    cfg = ab.configured({"CC_BUDDY_AUTO_BROWSER": "1", "CC_BUDDY_AUTO_BROWSER_STEPS": "99",
                         "CC_BUDDY_AUTO_BROWSER_PROVIDER": "nonsense", "CC_BUDDY_AUTO_BROWSER_TOKEN": "secret"})
    assert cfg.enabled and cfg.max_steps == 20 and cfg.provider == "openrouter" and "secret" not in repr(cfg)


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


def test_the_wrapper_sends_a_routed_task_to_auto_browser_and_keeps_reflexes_first() -> None:
    codex, auto = Body("codex"), Body("auto")
    routed: list[str] = []

    async def route_body(goal: str) -> str:
        routed.append(goal)
        return br.AUTO if "compare" in goal else br.CODEX

    async def opener(app: str) -> tuple[bool, str]:
        return True, ""

    def agent() -> ReflexFirstAgent:
        return ReflexFirstAgent(lambda: codex, lambda ev: None, apps=lambda: ["Spotify"], opener=opener,
                                make_auto=lambda: auto, route_body=route_body)

    assert asyncio.run(agent().run("open Spotify")) == "Opened Spotify."        # the reflex never asks the router
    a = agent()
    assert asyncio.run(a.run("compare three laptops")) == "auto" and a.provider == "auto-browser"
    assert asyncio.run(agent().run("check my gmail")) == "codex"
    assert routed == ["compare three laptops", "check my gmail"]

    async def broken(goal: str) -> str:
        raise RuntimeError

    b = ReflexFirstAgent(lambda: codex, lambda ev: None, apps=lambda: [], make_auto=lambda: auto, route_body=broken)
    assert asyncio.run(b.run("compare laptops")) == "codex"
