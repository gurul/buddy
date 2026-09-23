"""Open an app without Codex: task_router's launch reflex in front of the Codex computer agent.

Since computer use moved to Codex (codex_computer.py), every request — "open Spotify" included — starts
a Codex turn: a process, a thread, a model, tens of seconds. A quarter of the owner's requests are
nothing but a launch (task_router.py's own count), and those need no model at all: ``open -a Spotify``.

``ReflexFirstAgent`` wraps whichever agent the daemon builds and keeps its run/steer/cancel/status
contract, so the voice and the Telegram door both get it with no change of their own. The wrapped agent
is built only when the reflex declines (``make_inner``), so an app launch never spends the warm Codex
agent codex_warm.py keeps ready; ``on_done`` tells it to warm the next one after a Codex task. Before handing
the goal over it asks ``task_router.classify``:

* the rules first (code, microseconds): "open Spotify", "pull up Notes for me";
* Jev only for wording the rules do not know ("Notes, please."), with the same gates
  task_router ships (typed_ask.make_jev_request_asker), via ``CC_BUDDY_ROUTER_MODEL=jev`` and the
  route in ``CC_BUDDY_JEV_ROUTE`` (OpenRouter on this Mac). Jev can only name an installed app.

Only a request the plan says is COMPLETE — a bare launch of an installed app, nothing after it — is
opened here. Anything else, a failed ``open``, a Jev error, or ``CC_BUDDY_REFLEXES=0``: the wrapped
agent runs the goal exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, Iterable, Optional

from . import task_router
from .computer_agent import AgentEvent

log = logging.getLogger(__name__)


def reflexes_on(environ: Any = None) -> bool:
    env = os.environ if environ is None else environ
    raw = str(env.get("CC_BUDDY_REFLEXES", "")).strip().lower()
    return task_router.REFLEX_DEFAULT if not raw else raw not in ("0", "false", "no", "off")


def jev_asker(environ: Any = None) -> Optional[Callable[[str, list[str]], str]]:
    """Jev for unfamiliar wording, or None when CC_BUDDY_ROUTER_MODEL is not jev or no key is set."""
    env = os.environ if environ is None else environ
    if str(env.get("CC_BUDDY_ROUTER_MODEL", "")).strip().lower() != "jev":
        return None
    try:
        from . import jev, typed_ask

        url, key, model = jev.route_config(env)
        return typed_ask.make_jev_request_asker(jev.make_predict(url, key, model, timeout_s=2.0), time.perf_counter)
    except Exception as e:  # noqa: BLE001 — no Jev is "rules only", never a failure
        log.warning("app-reflex: Jev is off (%s)", type(e).__name__)
        return None


async def open_app(app: str) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec("open", "-a", app, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    return proc.returncode == 0, err.decode(errors="replace").strip()[:200]


class ReflexFirstAgent:
    """The wrapped agent's contract, with a bare app launch answered by ``open -a``."""

    def __init__(self, make_inner: Callable[[], Any], on_event: Callable[[AgentEvent], None], *,
                 apps: Callable[[], Iterable[str]] = task_router.installed_apps,
                 asker: Optional[Callable[[str, list[str]], str]] = None,
                 opener: Callable[[str], Any] = open_app, enabled: bool = True,
                 on_done: Callable[[], None] = lambda: None, provider: str = "codex") -> None:
        self._make_inner, self._inner, self._on_event = make_inner, None, on_event
        self._apps, self._asker, self._opener, self._enabled = apps, asker, opener, enabled
        self._on_done, self.provider = on_done, provider
        self._reflex_running = False
        self._cancel_reason: Optional[str] = None
        self.reflexed: Optional[str] = None               # the app opened here, for the log and the tests

    @property
    def running(self) -> bool:
        return self._reflex_running or bool(getattr(self._inner, "running", False))

    def cancel(self, reason: str = "") -> None:
        if self._inner is None:
            self._cancel_reason = reason                  # before the handoff: the wrapped agent never starts
        else:
            self._inner.cancel(reason=reason)

    def steer(self, text: str) -> bool:
        return self._inner is not None and self._inner.steer(text)

    def status(self) -> dict[str, Any]:
        if self._inner is None:
            return {"running": self.running, "provider": self.provider, "goal": "", "reflex": self.reflexed}
        return self._inner.status()

    def __getattr__(self, name: str) -> Any:              # browser_used, browser_screenshot, final …
        if name.startswith("_") or self.__dict__.get("_inner") is None:
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def _plan(self, goal: str) -> task_router.Plan:
        apps = tuple(await asyncio.to_thread(lambda: tuple(self._apps())))
        # classify is pure unless Jev is asked; Jev is a blocking HTTP call, so off the loop.
        return await asyncio.to_thread(task_router.classify, goal, apps=apps, model=self._asker)

    async def run(self, goal: str) -> str:
        if self._enabled:
            self._reflex_running = True
            try:
                plan = await self._plan(goal)
                if plan.kind == "launch" and plan.complete and plan.app:
                    t0 = time.perf_counter()
                    ok, err = await self._opener(plan.app)
                    if ok:
                        self.reflexed = plan.app
                        log.info("app-reflex: opened %s in %.2f s (%s)", plan.app, time.perf_counter() - t0,
                                 "; ".join(plan.reasons))
                        final = f"Opened {plan.app}."
                        self._on_event(AgentEvent("started", goal))
                        self._on_event(AgentEvent("final", final))
                        return final
                    log.warning("app-reflex: open -a %s failed (%s); Codex takes it", plan.app, err)
            except Exception as e:  # noqa: BLE001 — the reflex never costs the request
                log.warning("app-reflex: skipped (%s)", type(e).__name__)
            finally:
                self._reflex_running = False
        if self._cancel_reason is not None:
            self._on_event(AgentEvent("cancelled", "Stopped."))
            return "Stopped."
        self._inner = self._make_inner()
        try:
            return await self._inner.run(goal)
        finally:
            self._on_done()
