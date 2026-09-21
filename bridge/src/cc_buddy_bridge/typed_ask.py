"""Asking laya and Jev in their own idiom.

Both speak the same wire format — `predict(state, questions)` with `choice`, `score` and `noul`
questions — and for a day this project treated that as meaning they are the same thing: one
sixteen-option question, the same long option texts, the same 0.80 cut-off. Scored that way
(2026-09-21, 110 head-move utterances) laya looked useless (16.7%) and Jev merely good. They are
different machines, and each has a documented shape:

LAYA (laya-multilingual via laya-mlx: an mmBERT-base ENCODER with decision heads; nothing is
generated; 8–10 ms; nothing leaves the Mac)
  - The whole question — instructions plus every option — lives in a 256-token head, each option
    capped at 48 tokens and ALL of them cut once they overflow. Upstream: "77 options receive only
    ~3 to 4 tokens per label"; it degrades past about twenty. So: few options, one or two words each.
  - A choice is a RANKING of the options against each other, and that is what it is good at: asked
    "left / right / up / down / center" it answers at p = 1.00. Its absolute `noul` is its weak
    primitive — upstream: "over-confident as shipped", and the multilingual checkpoint "ships with no
    fitted temperatures at all"; here it says ≈ 0.1 for a plain yes. So a gate is asked as a choice
    too ("head / screen / search / object / talk"), never as a yes/no.
  - It reads a short English sentence well and a long state badly (~320 tokens of state upstream).
    The state is the utterance and nothing else.
  - Its probabilities are not calibrated, so no threshold is carried over from anywhere: each
    question's cut-off is fitted on a calibration half and reported on the other half.
  - It decomposes: three 3–7-way questions in ONE predict call cost the same ~10 ms.

JEV (TypeSafe's hosted System One, jev.py: 200–400 ms, the state leaves the Mac)
  - It is a LITERAL reader (TypeSafe: "It can be quite literal in its understanding"), weak at
    indirection, counting and dates, and it suffers "context rot" in a large irrelevant state. So the
    instruction says exactly what counts and what does not, in plain words, and the state is small
    and structured.
  - "A Choice is relative … each Noul is absolute and can be low for all of them." Its noul is
    excellent (0.94 for a head command, 0.04 for "scroll down a bit") and its choice alone is NOT a
    gate (it answers "down" for the scroll). So every Jev decision here is an absolute noul gate PLUS
    the relative choices, in one request — "every question is evaluated in parallel … adding
    questions barely changes the response time".
  - Thresholds are per question type ("Don't carry a threshold tuned on a Noul over to a Choice")
    and per consequence (TypeSafe's confidence-routing recipe), so they are fitted per question too.

The two askers below share nothing but the return type. That is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

Predict = Callable[[Any, dict[str, Any]], dict[str, Any]]

POSES = ("left", "right", "up", "down", "center", "far_left", "far_right", "bit_left", "bit_right", "bit_up",
         "bit_down", "desk", "ceiling")
DIRECTIONS = ("left", "right", "up", "down", "center", "desk", "ceiling")


@dataclass(frozen=True)
class HeadAnswer:
    """What a model said about one utterance, before any threshold: the caller gates it."""

    gate: float                      # how sure it is that this IS a head-pose request, 0..1 (model-specific meaning)
    direction: str                   # one of DIRECTIONS, or "none"
    p_direction: float
    size: str                        # little | normal | far
    p_size: float
    ms: float = 0.0
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def pose(self) -> str:
        """The pose label the direction and the size make ("bit_left", "far_right", "desk", …)."""
        d = self.direction
        if d not in DIRECTIONS:
            return "none"
        if d in ("center", "desk", "ceiling"):
            return d
        if self.size == "little":
            return f"bit_{d}"
        if self.size == "far" and d in ("left", "right"):
            return f"far_{d}"
        return d


@dataclass(frozen=True)
class Gates:
    """The cut-offs for one model, fitted on a calibration set (fit_gates), never borrowed."""

    gate: float
    direction: float

    def decide(self, a: HeadAnswer) -> str:
        if a.error or a.gate < self.gate or a.p_direction < self.direction:
            return "none"
        return a.pose()


def _choice(answer: Any) -> tuple[str, float, dict[str, float]]:
    if not isinstance(answer, dict):
        return "", 0.0, {}
    probs = answer.get("probabilities") if isinstance(answer.get("probabilities"), dict) else {}
    key = answer.get("choice") if isinstance(answer.get("choice"), str) else ""
    p = probs.get(key)
    return key, float(p) if isinstance(p, (int, float)) else 0.0, {k: float(v) for k, v in probs.items()
                                                                   if isinstance(v, (int, float))}


def _noul(answer: Any) -> float:
    v = answer.get("noul") if isinstance(answer, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


# ---- laya: short relative choices, the utterance as the whole state --------------------------------

LAYA_HEAD_QUESTIONS: dict[str, Any] = {
    # The gate is a CHOICE (laya ranks well, and answers a bare yes/no near zero). Five rivals, a word or two each.
    "kind": {"type": "choice", "instructions": "what is the owner asking for?",
             "criteria": {"head": "move the robot's head", "screen": "control the computer screen",
                          "search": "look something up", "object": "look at or find an object",
                          "talk": "just talking"}},
    "direction": {"type": "choice", "instructions": "which way should the robot's head go?",
                  "criteria": {"left": "left", "right": "right", "up": "up", "down": "down",
                               "center": "straight ahead at the owner", "desk": "down at the desk",
                               "ceiling": "straight up at the ceiling"}},
    "size": {"type": "choice", "instructions": "how far should it move?",
             "criteria": {"little": "a little", "normal": "normally", "far": "all the way"}},
}


def ask_laya_head(predict: Predict, text: str, clock: Callable[[], float]) -> HeadAnswer:
    t0 = clock()
    try:
        result = predict({"owner said": " ".join(text.split())}, LAYA_HEAD_QUESTIONS)
        answers = result.get("answers") or {}
    except Exception as e:  # noqa: BLE001 — a model that fails is an abstention, never a twitch
        return HeadAnswer(0.0, "none", 0.0, "normal", 0.0, (clock() - t0) * 1000.0, f"{type(e).__name__}: {e}"[:160])
    _kind, _p, kinds = _choice(answers.get("kind"))
    direction, p_dir, _ = _choice(answers.get("direction"))
    size, p_size, _ = _choice(answers.get("size"))
    return HeadAnswer(gate=kinds.get("head", 0.0), direction=direction or "none", p_direction=p_dir,
                      size=size or "normal", p_size=p_size, ms=(clock() - t0) * 1000.0, raw=dict(answers))


# ---- jev: one literal absolute gate, plus the choices, in one request -------------------------------

JEV_HEAD_QUESTIONS: dict[str, Any] = {
    "is_head": {"type": "noul", "instructions": (
        "Is the owner asking the ROBOT to turn, tilt or point its own head or eyes in a direction, or to face the "
        "owner? Answer no when the words are about the computer (scroll, volume, window, tab, cursor, swipe, page), "
        "when 'look up' is followed by a topic to search for, when the owner wants the robot to look AT, find or "
        "describe an object, to look around the room, or to stop, and when it is ordinary conversation that merely "
        "contains a direction word.")},
    "direction": {"type": "choice", "instructions": "Which way does the owner want the robot's head to end up pointing?",
                  "criteria": {"left": "to the robot's left", "right": "to the robot's right", "up": "tilted upward",
                               "down": "tilted downward, but not specifically at the desk or table",
                               "center": "straight ahead, facing the owner again",
                               "desk": "down at the desk or the table in front of it",
                               "ceiling": "straight up at the ceiling", "none": "no direction is requested"}},
    "size": {"type": "choice", "instructions": "How large a movement is asked for?",
             "criteria": {"little": "a small nudge: a bit, a little, slightly, a touch, a hair",
                          "normal": "an ordinary turn with no size mentioned",
                          "far": "as far as it goes: all the way, fully, as far as you can, hard left or right"}},
}
JEV_STATE_NOTE = ("A transcript of speech to a small desk robot whose head can pan left and right and tilt up and down. "
                  "Transcripts contain filler, false starts and leftover words from the sentence before; judge the "
                  "request the owner ends on.")


def ask_jev_head(predict: Predict, text: str, clock: Callable[[], float]) -> HeadAnswer:
    t0 = clock()
    try:
        result = predict({"about": JEV_STATE_NOTE, "owner_said": " ".join(text.split())}, JEV_HEAD_QUESTIONS)
        answers = result.get("answers") or {}
    except Exception as e:  # noqa: BLE001
        return HeadAnswer(0.0, "none", 0.0, "normal", 0.0, (clock() - t0) * 1000.0, f"{type(e).__name__}: {e}"[:160])
    direction, p_dir, _ = _choice(answers.get("direction"))
    size, p_size, _ = _choice(answers.get("size"))
    return HeadAnswer(gate=_noul(answers.get("is_head")), direction=direction or "none", p_direction=p_dir,
                      size=size or "normal", p_size=p_size, ms=(clock() - t0) * 1000.0, raw=dict(answers))


# Fitted 2026-09-21 by fit_gates on the 55 calibration utterances of tests/fixtures/routes/head_moves.json
# (zero false moves allowed) and scored on the other 55: 32 of 33 poses right, 0 wrong, 0 false moves in 22,
# messy speech 12 of 12, 244 ms p50. Refit with tools/head_eval.py --model jev when the model's version moves.
JEV_HEAD_GATES = Gates(gate=0.30, direction=0.70)


def make_jev_head_asker(predict: Predict, clock: Callable[[], float]) -> Callable[[str], str]:
    """`asker(utterance) -> a pose label from POSES, or "none"`: Jev's native answer under the shipped gates."""
    def asker(text: str) -> str:
        return JEV_HEAD_GATES.decide(ask_jev_head(predict, text, clock))
    return asker


# ---- fitting the cut-offs ---------------------------------------------------------------------------

GATE_GRID = (0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
DIRECTION_GRID = (0.0, 0.34, 0.5, 0.6, 0.7, 0.8, 0.9)


def fit_gates(answers: list[HeadAnswer], labels: list[str], max_false_moves: int = 0) -> Gates:
    """The cut-offs that get the most poses right on a calibration set while moving the head on at
    most `max_false_moves` utterances labelled "none" — a twitch at ordinary speech costs more than
    a command handed to the slower path. Ties go to the stricter gate."""
    best: Optional[tuple[int, float, float]] = None
    chosen = Gates(1.0, 1.0)
    for g in GATE_GRID:
        for d in DIRECTION_GRID:
            gates = Gates(g, d)
            said = [gates.decide(a) for a in answers]
            false_moves = sum(1 for s, want in zip(said, labels, strict=True) if want == "none" and s != "none")
            if false_moves > max_false_moves:
                continue
            right = sum(1 for s, want in zip(said, labels, strict=True) if want != "none" and s == want)
            key = (right, g, d)
            if best is None or key > best:
                best, chosen = key, gates
    return chosen


def describe(questions: Mapping[str, Any]) -> str:
    """How much a protocol asks: option counts and the longest option, for the docs and the eval header."""
    parts = []
    for name, q in questions.items():
        crit = q.get("criteria") or {}
        longest = max((len(str(v).split()) for v in crit.values()), default=0)
        parts.append(f"{name}: {q['type']}" + (f" of {len(crit)} (≤{longest} words each)" if crit else ""))
    return "; ".join(parts)


# ---- jev on a whole request: absolute gates in parallel, the app picked from the real list -----------
#
# Asked one relative question ("which kind of request is this?") Jev fired 8 unsafe reflexes on 100
# requests: "Open Mail and reply to the latest email" is, relatively, more a launch than anything else
# on the menu. That is the choice primitive doing its job. What the decision needs is absolute: is
# this NOTHING BUT a launch? is anything risky asked for? So each property is its own noul, all in one
# request (they are evaluated in parallel against the same state), and the application is a choice over
# the apps that are really installed — up to 255 options is within its documented range — with an
# explicit "none". The query of a web search is still extracted by code: Jev does not generate text.

def jev_request_questions(apps: list[str]) -> dict[str, Any]:
    app_options = {f"app{i}": name for i, name in enumerate(apps)}
    app_options["none"] = "no installed application is to be opened"
    return {
        "launch_only": {"type": "noul", "instructions": (
            "Is the request asking ONLY to open, launch, start, bring up, show or switch to one application, and "
            "nothing else? Politeness and filler such as 'on my Mac' or 'for me' do not count as something else. "
            "Answer no if anything is to be done after the application opens; if the thing to open is a website, "
            "folder, document, tab, window, settings page, playlist, show or other content rather than the "
            "application itself; if two applications are named; if it asks to close or quit; or if it is a question "
            "about whether an application is open or running.")},
        "app": {"type": "choice", "instructions": "Which installed application does the request ask to open?",
                "criteria": app_options},
        "web_search": {"type": "noul", "instructions": (
            "Is the request an explicit WEB search with a concrete query, where showing the results page is the whole "
            "job? Wording such as: search for, search up, google, look up, search the web for, pull up … on Google. "
            "Answer no if the search is inside an application or the owner's own data (search Spotify for …, search for "
            "… in Mail, in my contacts, my photos); if there is no concrete query; or if the owner also wants to be "
            "told, read or given a summary of the results.")},
        "risky_action": {"type": "noul", "instructions": (
            "Does the request ask the robot to DO something consequential, or to create or send content: send, reply, "
            "text, message or email someone, post, order, buy, pay, book, delete, empty, remove, quit, kill, close, "
            "shut down, restart, install, uninstall, log in or out, change a password, transfer, submit, share, upload, "
            "reset, turn something off, type or write text, take notes, or create a reminder, event, note, task, "
            "contact, playlist, folder or document? A web search whose query merely mentions such a word is NOT risky.")},
    }


@dataclass(frozen=True)
class RequestAnswer:
    launch_only: float
    app: str                        # an installed app's name, or ""
    p_app: float
    web_search: float
    risky: float
    ms: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class RequestGates:
    launch_only: float = 0.8
    app: float = 0.8
    web_search: float = 0.8
    risky_max: float = 0.2          # at or above this the request is the planner's, whatever else is true


def ask_jev_request(predict: Predict, goal: str, apps: list[str], clock: Callable[[], float]) -> RequestAnswer:
    t0 = clock()
    try:
        result = predict({"about": "A spoken request to a desk robot that can operate the owner's Mac.",
                          "request": " ".join(goal.split())}, jev_request_questions(apps))
        answers = result.get("answers") or {}
    except Exception as e:  # noqa: BLE001 — a model that fails abstains: the planner takes the request
        return RequestAnswer(0.0, "", 0.0, 0.0, 1.0, (clock() - t0) * 1000.0, f"{type(e).__name__}: {e}"[:160])
    key, p_app, _ = _choice(answers.get("app"))
    name = apps[int(key[3:])] if key.startswith("app") and key[3:].isdigit() and int(key[3:]) < len(apps) else ""
    return RequestAnswer(launch_only=_noul(answers.get("launch_only")), app=name, p_app=p_app,
                         web_search=_noul(answers.get("web_search")), risky=_noul(answers.get("risky_action")),
                         ms=(clock() - t0) * 1000.0)


def decide_request(a: RequestAnswer, g: RequestGates) -> str:
    """launch | search | other, from the absolute answers. Risk outranks everything."""
    if a.error or a.risky >= g.risky_max:
        return "other"
    if a.launch_only >= g.launch_only and a.app and a.p_app >= g.app:
        return "launch"
    if a.web_search >= g.web_search:
        return "search"
    return "other"


# Fitted 2026-09-21 by fit_request_gates on the 270 requests that had been read (the tuning set and
# holdouts 1 and 2), zero unsafe and zero wrong allowed; then scored once on holdout 3 (110 unseen):
# alone 42 fired, 41 right (97.6%), 0 unsafe; after the rules, 45 fired, 44 right, 0 unsafe, coverage of
# the bare reflexes 74% → 96%. Refit with tools/route_eval.py --model jev --native when the model's
# version changes: TypeSafe says an alias's answers "can change without a change on your side".
JEV_REQUEST_GATES = RequestGates(launch_only=0.7, app=0.8, web_search=0.5, risky_max=0.5)


def make_jev_request_asker(predict: Predict, clock: Callable[[], float]) -> Callable[[str, list[str]], str]:
    """`asker(goal, apps) -> installed app to launch, or ""`: Jev's native answer under the shipped gates.
    Only a BARE LAUNCH comes back: a search's query is code's to extract, and code already declined."""
    def asker(goal: str, apps: list[str]) -> str:
        a = ask_jev_request(predict, goal, apps, clock)
        return a.app if decide_request(a, JEV_REQUEST_GATES) == "launch" else ""
    return asker


REQUEST_GRID = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
RISK_GRID = (0.05, 0.1, 0.2, 0.3, 0.5)


def fit_request_gates(answers: list[RequestAnswer], truths: list[str], planner_only: list[bool],
                      apps_right: list[bool]) -> RequestGates:
    """Cut-offs that fire the most correct reflexes on a tuning set with ZERO reflexes on a request
    labelled planner-only and zero wrong-app launches. Ties go to the stricter setting."""
    best: Optional[tuple[int, float, float, float]] = None
    chosen = RequestGates(0.95, 0.95, 0.95, 0.05)
    for lo in REQUEST_GRID:
        for ws in REQUEST_GRID:
            for risk in RISK_GRID:
                g = RequestGates(launch_only=lo, app=0.8, web_search=ws, risky_max=risk)
                said = [decide_request(a, g) for a in answers]
                unsafe = sum(1 for s, po in zip(said, planner_only, strict=True) if s != "other" and po)
                wrong = sum(1 for s, t, ok in zip(said, truths, apps_right, strict=True)
                            if s != "other" and (s != t or (s == "launch" and not ok)))
                if unsafe or wrong > 0:
                    continue
                right = sum(1 for s, t in zip(said, truths, strict=True) if s != "other" and s == t)
                key = (right, lo, ws, -risk)
                if best is None or key > best:
                    best, chosen = key, g
    return chosen
