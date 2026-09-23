"""auto-browser (github.com/LvcidPsyche/auto-browser, MIT) as a second body for web jobs, beside Codex.

What it is, from its source (read 2026-09-23 at commit aa99c42, v1.7.0): a Docker compose stack — a
``controller`` FastAPI on 127.0.0.1:8000 and a ``browser-node`` running Playwright Chromium, headed on a
virtual display inside the container, watchable and takeover-able through noVNC on 127.0.0.1:6080. It has
its own browser and none of the owner's logins; its built-in goal loop is ``POST /sessions/{id}/agent/run``
(``provider``, ``goal``, ``max_steps`` ≤ 20) and answers ``done | acted | takeover | approval_required |
error | max_steps_reached``. Navigation is limited to its ``ALLOWED_HOSTS``; actions the model labels
post / payment / account_change / destructive (and uploads) wait in an approval queue.

Codex's browser use is the opposite body: the owner's real Chrome with their logins, and the rest of the
Mac. ``browser_router.py`` has Jev choose between them per task; this module is only the adapter.

``AutoBrowserAgent`` keeps the run/steer/cancel/status contract the voice and the Telegram door already use
(codex_computer.CodexComputerAgent's), so it drops in behind app_reflex with no change to either door:

* one fresh session per task, closed when the task ends;
* up to ``rounds`` agent/run calls of ``max_steps`` each (the controller caps one call at 20 steps);
* an ``approval_required`` answer becomes a yes/no for the owner through ``ask_user`` — yes approves that
  one queued action and the next round executes it (``approval_id``); anything else rejects it and stops;
* ``takeover`` (a login, a CAPTCHA, the loop guard) stops with the noVNC address to finish by hand.

Ships OFF: ``CC_BUDDY_AUTO_BROWSER=1`` turns it on, and even then a task only goes here when the controller
answers ``/healthz`` (``available``). Docker, the stack and its model key are the owner's to set up
(docs/stackchan/routing.md).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import httpx

from . import consent
from .agent_contract import AgentEvent

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_NOVNC = "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale"
PROVIDERS = ("openai", "claude", "gemini", "openrouter", "xai", "deepseek", "minimax", "openai_compatible")
MAX_STEPS_PER_CALL = 20                 # AgentRunRequest.max_steps le=20
HEALTH_TIMEOUT_SECS = 0.5
RUN_TIMEOUT_SECS = 600.0                # one agent/run call: up to 20 model steps


@dataclass(frozen=True)
class AutoBrowserConfig:
    enabled: bool = False
    url: str = DEFAULT_URL
    token: str = ""
    provider: str = "openrouter"
    model: str = ""
    max_steps: int = MAX_STEPS_PER_CALL
    rounds: int = 3
    profile: str = "fast"                # "governed": every non-read action waits for approval
    novnc: str = DEFAULT_NOVNC

    def __repr__(self) -> str:           # never the token
        return f"AutoBrowserConfig(enabled={self.enabled}, url={self.url!r}, provider={self.provider!r})"


def configured(environ: Any = None) -> AutoBrowserConfig:
    env = os.environ if environ is None else environ

    def text(name: str, default: str) -> str:
        return str(env.get(name, "") or "").strip() or default

    def number(name: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(str(env.get(name, "") or default))))
        except ValueError:
            return default

    provider = text("CC_BUDDY_AUTO_BROWSER_PROVIDER", "openrouter").lower()
    profile = text("CC_BUDDY_AUTO_BROWSER_PROFILE", "fast").lower()
    return AutoBrowserConfig(
        enabled=text("CC_BUDDY_AUTO_BROWSER", "0").lower() in ("1", "true", "yes", "on"),
        url=text("CC_BUDDY_AUTO_BROWSER_URL", DEFAULT_URL).rstrip("/"),
        token=text("CC_BUDDY_AUTO_BROWSER_TOKEN", ""),
        provider=provider if provider in PROVIDERS else "openrouter",
        model=text("CC_BUDDY_AUTO_BROWSER_MODEL", ""),
        max_steps=number("CC_BUDDY_AUTO_BROWSER_STEPS", MAX_STEPS_PER_CALL, 1, MAX_STEPS_PER_CALL),
        rounds=number("CC_BUDDY_AUTO_BROWSER_ROUNDS", 3, 1, 10),
        profile=profile if profile in ("fast", "governed") else "fast",
        novnc=text("CC_BUDDY_AUTO_BROWSER_NOVNC", DEFAULT_NOVNC),
    )


def _headers(cfg: AutoBrowserConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.token}"} if cfg.token else {}


async def available(cfg: AutoBrowserConfig, client: Optional[httpx.AsyncClient] = None) -> bool:
    """The controller answers /healthz now. Off, unreachable or slow: False, never an exception."""
    if not cfg.enabled:
        return False
    own = client is None
    client = client or httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECS)
    try:
        r = await client.get(f"{cfg.url}/healthz", headers=_headers(cfg), timeout=HEALTH_TIMEOUT_SECS)
        return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False
    finally:
        if own:
            await client.aclose()


def _describe(approval: dict[str, Any]) -> str:
    """One line a person can say yes or no to, from an ApprovalRecord."""
    action = approval.get("action") or {}
    what = str(action.get("action") or "act")
    target = action.get("text") or action.get("label") or action.get("url") or action.get("selector") or ""
    reason = str(approval.get("reason") or action.get("reason") or "").strip()
    kind = str(approval.get("kind") or "").replace("_", " ")
    line = f"{what} {str(target)[:80]}".strip()
    return f"auto-browser wants to {line}" + (f" ({kind})" if kind else "") + (f": {reason[:200]}" if reason else "")


def _summary(result: dict[str, Any]) -> str:
    steps = result.get("steps") or []
    last = steps[-1] if steps else {}
    decision = last.get("decision") or {}
    return str(decision.get("reason") or last.get("error") or "").strip()


class AutoBrowserAgent:
    """The run/steer/cancel/status contract, over auto-browser's REST API."""

    provider = "auto-browser"

    def __init__(self, *, on_event: Callable[[AgentEvent], None], ask_user: Callable[[str], Awaitable[str]],
                 config: Optional[AutoBrowserConfig] = None, client: Optional[httpx.AsyncClient] = None) -> None:
        self.on_event, self.ask_user = on_event, ask_user
        self.config = config or configured()
        self._client = client
        self.running = False
        self.goal = self.final = self.last_commentary = ""
        self.session_id: Optional[str] = None
        self.browser_used = True                          # it is a browser task by construction
        self.browser_screenshot = None                    # Telegram's task picture; not fetched from the stack
        self._runner: Optional[asyncio.Task] = None
        self._cancelled = False

    def _emit(self, kind: str, text: str = "") -> None:
        self.on_event(AgentEvent(kind, text))

    def status(self) -> dict[str, Any]:
        return dict(running=self.running, provider=self.provider, goal=self.goal, last=self.last_commentary,
                    final=self.final, session_id=self.session_id)

    def steer(self, text: str) -> bool:
        return False                                      # the goal loop takes no mid-run input

    def cancel(self, reason: str = "") -> None:
        self._cancelled = True
        if self._runner is not None:
            self._runner.cancel()

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        r = await client.post(f"{self.config.url}{path}", json=body, headers=_headers(self.config), timeout=timeout)
        r.raise_for_status()
        return r.json()

    async def run(self, goal: str) -> str:
        if self.running:
            raise RuntimeError("An auto-browser task is already running.")
        self._runner = asyncio.current_task()
        self.running, self.goal, self.final = True, goal, ""
        self._emit("started", goal)
        client = self._client or httpx.AsyncClient()
        try:
            if self._cancelled:
                raise asyncio.CancelledError
            session = await self._post(client, "/sessions", {"name": "buddy"}, 60.0)
            self.session_id = str(session.get("id") or session.get("session_id") or "")
            if not self.session_id:
                raise RuntimeError("auto-browser did not return a session id")
            self.final = await self._rounds(client, goal)
            self._emit("final", self.final)
        except asyncio.CancelledError:
            self.final = "auto-browser task stopped."
            self._emit("cancelled", self.final)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            log.warning("auto-browser: task failed (%s)", type(e).__name__)
            self.final = f"auto-browser could not finish the task ({type(e).__name__})."
            self._emit("error", self.final)
        finally:
            if self.session_id:
                try:
                    await client.delete(f"{self.config.url}/sessions/{self.session_id}",
                                        headers=_headers(self.config), timeout=10.0)
                except (httpx.HTTPError, OSError):
                    pass
            if self._client is None:
                await client.aclose()
            self.running, self._runner = False, None
        return self.final

    async def _rounds(self, client: httpx.AsyncClient, goal: str) -> str:
        cfg = self.config
        approval_id: Optional[str] = None
        summary = ""
        for _ in range(cfg.rounds):
            body: dict[str, Any] = {"provider": cfg.provider, "goal": goal, "max_steps": cfg.max_steps,
                                    "workflow_profile": cfg.profile}
            if cfg.model:
                body["provider_model"] = cfg.model
            if approval_id:
                body["approval_id"] = approval_id
            result = await self._post(client, f"/sessions/{self.session_id}/agent/run", body, RUN_TIMEOUT_SECS)
            status = str(result.get("status") or "")
            summary = _summary(result) or summary
            if summary:
                self.last_commentary = summary
                self._emit("progress", summary)
            approval_id = None
            if status == "done":
                return summary or "auto-browser finished."
            if status == "approval_required":
                steps = result.get("steps") or []
                approval = ((steps[-1].get("execution") or {}).get("approval") or {}) if steps else {}
                aid = str(approval.get("id") or "")
                if not aid:
                    return "auto-browser stopped at an approval it did not describe; nothing was approved."
                question = _describe(approval)
                self._emit("ask", question)
                answer = await self.ask_user(question + "\nyes / no?")
                if consent.approves(answer):
                    await self._post(client, f"/approvals/{aid}/approve", {"comment": "approved by the owner via buddy"}, 30.0)
                    approval_id = aid
                    continue
                await self._post(client, f"/approvals/{aid}/reject", {"comment": "declined by the owner via buddy"}, 30.0)
                return "Stopped: you declined that step, so auto-browser did not do it."
            if status == "takeover":
                return (f"auto-browser needs you to take over ({summary or 'a login or a check it cannot pass'}). "
                        f"On the Mac, open {cfg.novnc}")
            if status == "error":
                return f"auto-browser hit an error: {summary or 'no detail'}"
            # acted / max_steps_reached: another round continues the same session
        return f"auto-browser ran out of steps. Last: {summary or 'no summary'}"
