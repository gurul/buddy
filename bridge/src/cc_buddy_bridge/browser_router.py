"""Which body carries a task that is not a reflex: Codex (the owner's real Chrome and Mac) or an isolated browser.

The isolated body was auto-browser until 2026-09-23, when it was retired (it needs Docker, which this Mac
does not have, and it has no scored results; research in docs/stackchan/routing.md). buddy's own Playwright
lane (browser_lane.py) is the intended isolated body once it has an evaluation set; until one is wired in
through app_reflex.ReflexFirstAgent(make_auto=..., route_body=...), nothing asks this router and every task
goes to Codex. Jev decides, asked the way TypeSafe documents for
a routing decision (docs.typesafe.ai: State, Choice "Structured instructions and criteria", Intent routing,
Jev 1.13 jaggedness), and the way typed_ask.py asks it everywhere else:

* The state is the request and nothing else — no tool manuals in the state ("context rot").
* The two bodies are the options of one Choice, each described as structure: ``what`` it is, ``for``, and
  ``not_for`` — the facts from each tool's own source and our verified docs, the boundary written into both
  sides so a literal reader cannot confuse them.
* Beside the Choice, absolute Nouls, one judgment each (Jev reads literally; "a Choice is relative, each
  Noul is absolute"): does the request need the owner's own signed-in accounts? does it touch anything
  outside a browser on the Mac? does the owner want it shown on their own screen? is it a job on the
  public web that can be reported back as text?
* Code combines them and owns the safety rule: the isolated body only when the job is public-web, needs none
  of the owner's accounts and nothing outside the browser, AND the Choice picks it with enough weight.
  Every cut-off is fitted with zero unsafe routes allowed (a task that needs the owner's accounts or Mac
  sent to a browser that has neither) — tools/route_eval.py --browser, on a blind holdout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

Predict = Callable[[Any, dict[str, Any]], dict[str, Any]]

CODEX, ISOLATED = "codex", "isolated"
AUTO = ISOLATED                   # the name the fitting and eval code use for the isolated body

# The two bodies, as the Choice's options. Facts only: the isolated browser's as auto-browser's source
# described it and as the Playwright lane is built (its own profile, no owner accounts, reports as text);
# Codex's from docs/codex-computer-use/README.md.
BODIES: dict[str, Any] = {
    "owner_chrome": {
        "what": "an agent that operates the owner's own Mac and the owner's real Chrome browser, already signed "
                "in to the owner's accounts, and shows its work on the owner's screen",
        "for": ["anything in the owner's own accounts: email, calendar, messages, shopping orders, banking, social "
                "media, work tools", "anything in a Mac app or a file on the Mac", "playing music or video for "
                "the owner", "sites that ask to prove you are human"],
        "not_for": ["long research or data collection on public websites that needs no account"],
    },
    "sandbox_browser": {
        "what": "a separate browser in an isolated container with no accounts, no cookies and no access to the "
                "Mac, which works in the background and reports the result as text",
        "for": ["looking something up across several public web pages", "comparing prices, products or options "
                "on public websites", "collecting information from public pages into a summary",
                "filling a public form that needs no account", "testing a website"],
        "not_for": ["anything that needs the owner signed in", "anything outside a web browser",
                    "anything the owner wants to watch or hear on their own screen"],
    },
}
OPTION_TO_BODY = {"owner_chrome": CODEX, "sandbox_browser": ISOLATED}


def questions() -> dict[str, Any]:
    return {
        "body": {"type": "choice",
                 "instructions": "Which of the two should carry out the owner's request?",
                 "criteria": BODIES},
        "needs_accounts": {"type": "noul", "instructions": (
            "Does the request need the owner to be signed in to one of their own accounts or services (their email, "
            "calendar, messages, orders, bank, social media, subscriptions or work tools), or does it refer to "
            "something already open in the owner's browser?")},
        "needs_mac": {"type": "noul", "instructions": (
            "Does the request involve anything outside a web browser: an application on the Mac, a file or folder "
            "on the Mac, the desktop or system settings, or playing sound or video for the owner to watch or hear?")},
        "show_owner": {"type": "noul", "instructions": (
            "Does the owner want something opened, shown or played on their own screen so they can look at it, read "
            "it or watch it themselves, rather than a result reported back to them?")},
        "public_web_job": {"type": "noul", "instructions": (
            "Is the request a job on public websites that anyone could do without an account, such as looking "
            "something up across several pages, comparing options, collecting information, filling a public form "
            "or testing a website, where the result can be reported back as text?")},
    }


@dataclass(frozen=True)
class BodyAnswer:
    p_auto: float                 # the Choice's probability for the sandbox option
    accounts: float
    mac: float
    public: float
    show: float = 0.0
    ms: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class BodyGates:
    public: float = 0.9
    accounts_max: float = 0.1
    mac_max: float = 0.1
    auto: float = 0.9
    show_max: float = 0.5


def ask(predict: Predict, goal: str, clock: Callable[[], float]) -> BodyAnswer:
    t0 = clock()
    try:
        result = predict({"request": " ".join(str(goal).split())}, questions())
        answers = result.get("answers") or {}
    except Exception as e:  # noqa: BLE001 — a model that fails abstains: Codex, as before
        return BodyAnswer(0.0, 1.0, 1.0, 0.0, 1.0, (clock() - t0) * 1000.0, f"{type(e).__name__}: {e}"[:160])

    def noul(key: str) -> float:
        v = (answers.get(key) or {}).get("noul")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    probs = (answers.get("body") or {}).get("probabilities") or {}
    p = probs.get("sandbox_browser")
    return BodyAnswer(p_auto=float(p) if isinstance(p, (int, float)) else 0.0, accounts=noul("needs_accounts"),
                      mac=noul("needs_mac"), public=noul("public_web_job"), show=noul("show_owner"),
                      ms=(clock() - t0) * 1000.0)


def decide(a: BodyAnswer, g: BodyGates) -> str:
    """AUTO only when every condition holds; anything else, including an error, is CODEX."""
    if a.error:
        return CODEX
    if a.accounts > g.accounts_max or a.mac > g.mac_max or a.show > g.show_max:
        return CODEX
    if a.public >= g.public and a.p_auto >= g.auto:
        return AUTO
    return CODEX


GRID = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
MAX_GRID = (0.05, 0.1, 0.2, 0.3, 0.5)


def fit(answers: list[BodyAnswer], truths: list[str]) -> BodyGates:
    """The cut-offs that route the most AUTO-labelled tasks there with ZERO CODEX-labelled tasks sent to
    the isolated browser. Ties go to the stricter setting."""
    best: Optional[tuple[float, ...]] = None
    chosen = BodyGates(1.01, 0.0, 0.0, 1.01, 0.0)
    for public in GRID:
        for auto in GRID:
            for acc in MAX_GRID:
                for mac in MAX_GRID:
                    for show in MAX_GRID:
                        g = BodyGates(public, acc, mac, auto, show)
                        said = [decide(a, g) for a in answers]
                        if any(s == AUTO and t != AUTO for s, t in zip(said, truths, strict=True)):
                            continue
                        right = sum(1 for s, t in zip(said, truths, strict=True) if s == AUTO == t)
                        key = (right, public, auto, -acc, -mac, -show)
                        if best is None or key > best:
                            best, chosen = key, g
    return chosen


# Fitted 2026-09-23 by tools/route_eval.py --browser on the 42 requests of browser_tuning.json (zero unsafe
# allowed); scored once on the blind browser_holdout.json. See docs/stackchan/routing.md.
GATES = BodyGates(public=0.7, accounts_max=0.3, mac_max=0.1, auto=0.95, show_max=0.3)


def make_router(predict: Predict, clock: Callable[[], float], gates: BodyGates = GATES) -> Callable[[str], str]:
    def route(goal: str) -> str:
        return decide(ask(predict, goal, clock), gates)
    return route


def jev_router(environ: Optional[Mapping[str, str]] = None) -> Optional[Callable[[str], str]]:
    """Jev over CC_BUDDY_JEV_ROUTE, or None (no key: every task stays with Codex)."""
    import os
    import time

    from . import jev

    try:
        url, key, model = jev.route_config(os.environ if environ is None else environ)
    except Exception:  # noqa: BLE001
        return None
    return make_router(jev.make_predict(url, key, model, timeout_s=2.0), time.perf_counter)
