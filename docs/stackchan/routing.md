# Routing: who carries a request

buddy has three engines that can decide something, and a fourth tier that is no model at all.
They are not interchangeable, and the first version of this work went wrong by treating two of
them as if they were. This page says what each one is, how it has to be asked, what was
measured, and how a request finds its way to the cheapest one that can do it safely.

- [The engines, as individuals](#the-engines-as-individuals)
- [Ask each one in its own idiom](#ask-each-one-in-its-own-idiom)
- [The tiers a request passes through](#the-tiers-a-request-passes-through)
- [What was measured](#what-was-measured)
- [Head moves](#head-moves)
- [Knobs](#knobs)
- [How the evidence was kept honest](#how-the-evidence-was-kept-honest)
- [Borrowed, and from where](#borrowed-and-from-where)

## The engines, as individuals

| | **astra** (`gpt-6-astra`) | **jev** (TypeSafe System One, `jev.py`) | **laya** (`laya-mlx`, local) | **code** |
|---|---|---|---|---|
| What it is | a vision and reasoning model that writes and runs Python on the desktop | a hosted typed-decision model: answers `choice`, `score` and `noul` questions about a text state with probabilities | a 322 M-parameter mmBERT **encoder** with decision heads; nothing is generated | rules in `task_router.py`, `lane_router.py`, `fast_lane.py` |
| Time per decision | 3.4 s median a turn here (2.8 s before the first token) | 226–244 ms p50, 292–341 ms p95 measured here; 368 / 833 ms inside matrixorigin/Astra | 8–12 ms | microseconds |
| Sees the screen | yes | no: "Text only" | no | the Accessibility tree |
| Writes text or code | yes | no ("not trained to generate text") | no | no |
| Options per question | n/a | up to 255 | degrades past about 20; "77 options receive only ~3 to 4 tokens per label" | n/a |
| State it can read | 1.05 M tokens | 64 k a request, 32 k of state; suffers "context rot" in a large irrelevant one | 1024 tokens here, a 256-token head for the whole question | n/a |
| Confidence you can trust | n/a | calibrated; thresholds are per question type | "over-confident as shipped"; this checkpoint "ships with no fitted temperatures at all" | n/a |
| Leaves the Mac | screenshots and the prompt | the state you send | nothing | nothing |
| Cost | $10 in / $50 out per M tokens | $0.042 per M input tokens, output free | $0 | $0 |
| Stated weak spots | asks clarifying questions; no latency promise | "quite literal"; counting, maths, dates, indirection; "DONE is never independent evidence of success" | "near chance on typed-decisions zero-shot … a fast base to specialise, not a zero-shot decision engine"; ordinal `score` is its weakest primitive | knows only the wording it was written for |

Sources: OpenAI's [computer-use guide](https://developers.openai.com/api/docs/guides/tools-computer-use)
and [model page](https://developers.openai.com/api/docs/models/gpt-6-astra); TypeSafe's
[docs](https://docs.typesafe.ai/llms.txt), [models page](https://docs.typesafe.ai/models) and
[privacy policy](https://typesafe.ai/legal/privacy-policy) (no numeric retention period is stated for
non-enterprise accounts); [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast);
[laya-mlx](https://github.com/mizorewww/laya-mlx) and upstream [laya](https://github.com/NandhaKishorM/laya)
"Honest limits". Pulled 2026-09-21; the latencies in the first column of each pair are this project's own.

## Ask each one in its own idiom

laya and Jev share a wire format — `predict(state, questions)` — and that is all they share.
`typed_ask.py` holds one asker per model, and they have nothing in common but the return type.

**laya ranks; it does not judge.** A `choice` is a ranking of the options against each other,
and with short options it is crisp: asked "left / right / up / down / center" it answers at
p = 1.00 in 9 ms. Its absolute yes/no (`noul`) is its weak primitive — it says about 0.1 for a plain
yes. So for laya: few options, a word or two each, the utterance as the whole state, a gate asked as a
choice rather than a yes/no, several small questions in one call, and **no threshold borrowed from
anywhere** — each cut-off is fitted on a calibration set. What laya cannot do zero-shot is tell
"move your head" from "scroll the screen", and its own authors say the honest way to use it is to
fine-tune it.

**Jev judges; its choice alone is not a gate.** TypeSafe: "A Choice is relative … each Noul is
absolute and can be low for all of them." Its `noul` is excellent (0.94 for a head command, 0.04 for
"scroll down a bit"), and its `choice` happily answers "down" for the scroll. So every Jev decision
here is an **absolute noul gate plus the relative choices, in one request** — "adding questions
barely changes the response time". It reads literally, so each instruction says in plain words what
counts *and what does not*. The state is small and structured. An app is chosen from the list that
is really installed, with an explicit "none". It never produces text: a search query is still
extracted by code.

What asking properly was worth, same model, same utterances:

| | asked one relative question | asked in its own idiom |
|---|---|---|
| Jev, head pose | 86.4 %, 1 false move in 44 | **97.0 %**, 0 wrong poses, 0 false moves (test half, unseen by its cut-offs) |
| Jev, request class | 80.7 %, **8 unsafe** reflexes in 100 | **97.6 %**, **0 unsafe** in 110 unseen |
| laya, head pose | 16.7 % | 12.1 % — the framing was not its problem; zero-shot gating is |

## The tiers a request passes through

`task_router.classify()` returns a plan: the tiers to try, in order. Each declines unless it is sure,
and the last is always the planner unless a reflex fully answers the request.

```
code gates      consequential (send, order, delete, quit …)   → planner only: only it has ask_user
                text to type or content to create             → planner only
                a question, or "tell me …" without a search    → planner only (needs eyes)
                a launch of Mail, Messages, Notes, Terminal …  → planner only if anything follows the launch
reflex (code)   "Open Spotify"            → open_app('Spotify')          1.6 s live   (8–32 s with the planner)
                "Search Google for …"     → open_url(search?q=…)         2.6 s live   (9–25 s)
                "Open Safari and go to …" → the launch, then the planner starts from there with a [note]
jev (optional)  wording the rules do not know ("Can you get Safari up on the screen?") → a bare launch only,
                asked natively, behind the code gates                     2.5 s live
lane (code)     lane_router.py: one labelled control accounts for every word → clicked    1.2–1.5 s live
astra           everything else; it may hand the lane exact labels: delegate(steps=[…])
```

A web search is harmless whatever its query says, so "google how to reset a Casio watch" is a reflex;
"search for earbuds and buy the cheapest pair" is the planner's, because the risky word starts a
second clause. "Search Spotify for jazz", "search for the invoice in Mail" and "look up Dana in my
contacts" are searches inside an app or your own data, not the web.

A reflex is confirmed by the helper's own sentence ("opened Spotify (frontmost after 0.4 s)"). If it
does not confirm within 4 s — Preview with no document never takes focus — the planner runs as if
nothing had been tried.

## What was measured

Request classification, scored on `tests/fixtures/routes/` by `tools/route_eval.py`. A reflex that
fires on a request labelled planner-only counts as **unsafe**, and the ship bar — precision ≥ 95 % on
≥ 15 fired reflexes, zero unsafe — was fixed before the first run.

| Set | Rules | Decision |
|---|---|---|
| holdout 1, 90 requests, unseen | 92.7 %, 1 unsafe | disabled |
| holdout 2, 100 requests, unseen | 89.1 %, 2 unsafe | disabled |
| **holdout 3, 110 requests, unseen, rules frozen before it was written** | **96.2 % on 52 fired, 0 unsafe** | **enabled** |

On holdout 3, counting only complete reflexes: rules alone 34 fired, 34 right, coverage 74 %; Jev
native alone 42 fired, 41 right, 0 unsafe; **rules then Jev native 45 fired, 44 right, 0 unsafe,
coverage 96 %**. Jev's one miss opened the Maps app for "Open Google Maps". laya asked one relative
question: 70 %, 2 unsafe, 10 fired.

The lane's router is measured separately (`tools/fastlane_eval.py --router`, see
[voice.md](voice.md#lane-first-the-router-before-the-planner)): 23 of 23 on holdout, 1.16 s and
1.54 s live against 13.1 s.

## Head moves

"hey buddy, look left" takes about 3.2 s today (voice → backend → `move_head`; daemon log,
2026-09-20 16:42). `tools/head_eval.py` scores three cheaper deciders on 110 utterances written by an
author who saw no code — 66 pose commands, 44 non-commands, 34 of those built to tempt a keyword
matcher ("scroll down a bit", "look up the weather", "you were right"):

| Decider | Right pose | Wrong pose | False moves | Latency | Leaves the Mac |
|---|---|---|---|---|---|
| **Jev, native** (test half) | **97.0 %**, messy speech 12 of 12 | 0 | 0 of 22 | 244 ms | the utterance |
| keyword rule, untuned | 71.2 %, messy speech 55 % | 1 | 4 of 44 | 0 ms | nothing |
| laya, native (test half) | 12.1 % | 3 | 0 of 22 | 10 ms | nothing |
| laya, told which are commands | direction 81.8 %, exact pose 62.1 % | — | — | 10 ms | nothing |

Jev does this job now, behind `CC_BUDDY_HEAD_MODEL=jev` (`voice_agent._fast_head_move`). Only a turn that
mentions a direction or the head is sent to it (`HEAD_WORDS`: a filter, not a judge — "scroll down"
passes it and Jev says no). A sure pose turns the head at once through the same `Head.move` the backend
uses, with the backend's own numbers ("left" 70°, "a bit" 15°, `head.POSE_MOVES`). The ordinary routing
still runs and sees that the head has moved, and if the voice delegated the same turn, the backend's
`move_head` is answered "already done" instead of turning the head a second time — a relative nudge must
never run twice. `none`, an abstention, a timeout (1.5 s) or any error leaves the turn to the backend
exactly as before. laya is not ready, zero-shot: it ranks
directions well and sizes badly ("a bit" against "all the way" is an ordinal judgement), and it cannot
gate. The labelled utterances are the seed of the fine-tuning set its authors say it needs.

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `CC_BUDDY_REFLEXES` | `1` | launch and web-search reflexes before the planner; `0` turns them off. The default is `tools/route_eval.py`'s decision, and `--check-default` asserts it |
| `CC_BUDDY_HEAD_MODEL` | `off` | `jev`: a spoken head pose ("look left", "a bit lower") is chosen by Jev in about a quarter of a second instead of ~3.2 s through the backend, which remains the fallback. The words of a turn that mentions a direction go to TypeSafe / OpenRouter |
| `CC_BUDDY_ROUTER_MODEL` | `off` | `jev`: ask Jev, natively, about a request the rules did not recognise; it may only add a bare launch. The request's words go to TypeSafe / OpenRouter, so it is your switch |
| `CC_BUDDY_LANE_FIRST` | `1` | the lane's router ([voice.md](voice.md#lane-first-the-router-before-the-planner)) |
| `CC_BUDDY_FAST_LANE_DECIDE` | `keyword` | who picks a lane step under the planner: `keyword`, or `model` |
| `CC_BUDDY_DECIDER` | `laya` | the lane's model in `model` mode: `laya` or `jev` |

Every routing decision is in the task's run log (`{"route": …}`, `{"reflex": …}`, `{"lane_first": …}`),
so a wrong route can be read after the fact.

## How the evidence was kept honest

- A holdout is written by an author who has seen neither the code nor any other set, and is run
  **once**. The moment it is read it becomes a tuning set (`holdout1.json`, `holdout2.json`), and a new
  one decides. It took three.
- The bar is fixed before the run, in the tool. A sub-policy noticed after the fact (bare launches were
  16 of 16 on holdout 2) was not trusted until an unseen set agreed (19 of 19).
- A model's cut-offs are fitted on data it is not scored on: half of the head utterances for the other
  half; the 270 read requests for the 110 unread ones.
- Known soft spots: the requests are text a voice model would write, not audio; the head utterances
  are authored, not recorded; Jev is not deterministic (the other session saw 85.3 % and 86.6 % on two
  runs of the same set), so a gap under three points is noise; and holdout 3 has now been read too.

## Borrowed, and from where

- **Bands, and abstention means the planner** — [matrixorigin/Astra](https://github.com/matrixorigin/Astra),
  a self-hosted agent runtime (not the OpenAI model) whose admission classifier is the closest prior art:
  "An unknown critical answer does not authorize execution", and it "never silently reuses the main agent
  model". Assessed as solid: 616 commits, CI, committed evaluation evidence including its failures.
- **Thresholds by consequence; a noul beside every choice** — TypeSafe's intent-routing and
  confidence-routing recipes.
- **The typed model picks only from options code built, and never writes text** —
  [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast).
- **Reflexes under everything, a slow planner that never blocks action** —
  [rmalde/minecraft-agent](https://github.com/rmalde/minecraft-agent), assessed as *thin but real*: the three
  tiers are implemented and its async-planner test passes, but it is one commit by one author, and every
  headline number rests on evidence its README says is "excluded from Git", on a fixed seed in Peaceful
  mode. The ideas stand because they are ordinary control practice with other sources behind them; none of
  its numbers or timer constants were used.
