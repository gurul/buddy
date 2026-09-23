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

Quitting (owner, 2026-09-23) takes the same path: only the explicit word "quit", a graceful quit (the
app's own "save changes?" dialog still appears; never a force quit). "quit all" quits every regular app
except the ones in ``QUIT_ALL_KEEP`` — the terminals Claude Code runs in, Claude, ChatGPT/Codex (the
computer use buddy hands off to), buddy's own app, Finder — plus ``CC_BUDDY_QUIT_ALL_KEEP``. Chosen by
tools/route_eval.py --quit on a blind holdout: rules then Jev, 45 of 45, 0 unsafe.

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


QUIT_ALL_KEEP = frozenset({"finder", "warp", "stable", "terminal", "iterm2", "iterm", "ghostty", "cmux", "claude",
                           "chatgpt", "codex", "stackchannotes", "codex computer use", "skycomputeruseservice"})
QUIT_SETTLE_SECS = 2.0          # the longest wait for apps to go; checked every QUIT_POLL_SECS, done when they are
QUIT_POLL_SECS = 0.25


def quit_keep(environ: Any = None) -> frozenset[str]:
    env = os.environ if environ is None else environ
    extra = {p.strip().casefold() for p in str(env.get("CC_BUDDY_QUIT_ALL_KEEP", "")).split(",") if p.strip()}
    return QUIT_ALL_KEEP | extra


def jev_quit_asker(environ: Any = None) -> Optional[Callable[[str, list[str]], str]]:
    """Jev for quit wording the rules do not know, under the same switch as jev_asker."""
    env = os.environ if environ is None else environ
    if str(env.get("CC_BUDDY_ROUTER_MODEL", "")).strip().lower() != "jev":
        return None
    try:
        from . import jev, typed_ask

        url, key, model = jev.route_config(env)
        return typed_ask.make_jev_quit_asker(jev.make_predict(url, key, model, timeout_s=2.0), time.perf_counter)
    except Exception as e:  # noqa: BLE001
        log.warning("app-reflex: Jev quit is off (%s)", type(e).__name__)
        return None


async def _osascript(*lines: str) -> tuple[bool, str]:
    args: list[str] = []
    for line in lines:
        args += ["-e", line]
    proc = await asyncio.create_subprocess_exec("osascript", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    return proc.returncode == 0, (out if proc.returncode == 0 else err).decode(errors="replace").strip()


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def _running(name: str) -> bool:
    ok, out = await _osascript(f"return application {_quote(name)} is running")
    return ok and out == "true"


async def _gone(names: list[str], settle: float) -> list[str]:
    """Wait until every app in ``names`` has quit or ``settle`` passes; the ones still running."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settle
    left = list(names)
    while left:
        left = [n for n in left if await _running(n)]
        if not left or loop.time() >= deadline:
            break
        await asyncio.sleep(QUIT_POLL_SECS)
    return left


async def quit_app(name: str, settle: float = QUIT_SETTLE_SECS) -> str:
    """Quit one app the way Cmd+Q does, without waiting on it. The sentence for the owner."""
    if not await _running(name):
        return f"{name} isn't open."
    ok, err = await _osascript("ignoring application responses", f"tell application {_quote(name)} to quit",
                               "end ignoring")
    if not ok:
        return f"I couldn't quit {name}: {err[:120]}"
    if await _gone([name], settle):
        return f"{name} didn't quit. It may be asking to save, or it declined; it's still open."
    return f"Quit {name}."


async def quit_all(keep: frozenset[str], settle: float = QUIT_SETTLE_SECS) -> str:
    """Quit every regular app except ``keep`` (process or app names, case-folded)."""
    ok, out = await _osascript('tell application "System Events" to get name of every application process '
                               'whose background only is false')
    if not ok:
        return f"I couldn't list the open apps: {out[:120]}"
    names = [n.strip() for n in out.split(",") if n.strip()]
    targets = [n for n in names if n.casefold() not in keep]
    kept = [("Warp" if n == "stable" else n) for n in names if n.casefold() in keep and n != "Finder"]
    if not targets:
        return "Nothing to quit." + (f" Kept {', '.join(kept)}." if kept else "")
    for n in targets:
        await _osascript("ignoring application responses", f"tell application {_quote(n)} to quit", "end ignoring")
    waiting = await _gone(targets, settle)
    done = [n for n in targets if n not in waiting]
    said = f"Quit {len(done)} app{'s' if len(done) != 1 else ''}" + (f": {', '.join(done)}." if done else ".")
    if waiting:
        said += (f" {', '.join(waiting)} didn't quit (asking to save, or declined); "
                 f"{'it is' if len(waiting) == 1 else 'they are'} still open.")
    if kept:
        said += f" Kept {', '.join(kept)}."
    return said


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
                 on_done: Callable[[], None] = lambda: None, provider: str = "codex",
                 quit_asker: Optional[Callable[[str, list[str]], str]] = None,
                 quitter: Callable[[str], Any] = quit_app,
                 quit_everything: Optional[Callable[[], Any]] = None) -> None:
        self._make_inner, self._inner, self._on_event = make_inner, None, on_event
        self._apps, self._asker, self._opener, self._enabled = apps, asker, opener, enabled
        self._on_done, self.provider = on_done, provider
        self._quit_asker, self._quitter = quit_asker, quitter
        self._quit_everything = quit_everything or (lambda: quit_all(quit_keep()))
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
        return await asyncio.to_thread(task_router.classify, goal, apps=apps, model=self._asker,
                                       quit_model=self._quit_asker)

    async def run(self, goal: str) -> str:
        if self._enabled:
            self._reflex_running = True
            try:
                plan = await self._plan(goal)
                if plan.complete and plan.kind in ("quit", "quit_all"):
                    t0 = time.perf_counter()
                    final = await (self._quitter(plan.app) if plan.kind == "quit" else self._quit_everything())
                    self.reflexed = plan.app or "*"
                    log.info("app-reflex: %s %s in %.2f s (%s)", plan.kind, plan.app or "all",
                             time.perf_counter() - t0, "; ".join(plan.reasons))
                    self._on_event(AgentEvent("started", goal))
                    self._on_event(AgentEvent("final", final))
                    return final
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
