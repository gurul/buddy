# Routing: who carries a request

**Current daemon route (2026-09-22):** voice and Telegram `start_task` use
`codex_computer.py` and Codex's installed Computer Use plugin. This page documents
the retained legacy worker and its experiments; the daemon does not select it or
fall back to it. See [the Codex integration](../codex-computer-use/README.md).

buddy has three engines that can decide something, and a fourth tier that is no model at all.
They are not interchangeable, and the first version of this work went wrong by treating two of
them as if they were. This page says what each one is, how it has to be asked, what was
measured, and how a request finds its way to the cheapest one that can do it safely.

- [The engines, as individuals](#the-engines-as-individuals)
- [Ask each one in its own idiom](#ask-each-one-in-its-own-idiom)
- [The tiers a request passes through](#the-tiers-a-request-passes-through)
- [What was measured](#what-was-measured)
- [Head moves](#head-moves)
- [Plan once, execute with Jev](#plan-once-execute-with-jev)
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

## Opening an app in front of Codex

Computer use now goes to Codex (`codex_computer.py`), and a Codex turn costs seconds
before it does anything. A bare "open Spotify" doesn't need that, so
`app_reflex.ReflexFirstAgent` wraps the agent the daemon builds (voice and Telegram
alike):

1. **The rules** (`task_router.classify`, code, microseconds) take the wording they
   know.
2. **Jev** (`CC_BUDDY_ROUTER_MODEL=jev`, over `CC_BUDDY_JEV_ROUTE`) is asked only
   when the rules find no reflex and no gate has fired. It can only name an
   installed app, under the shipped `JEV_REQUEST_GATES`.
3. **A complete bare launch** runs `open -a <App>`. Anything else goes to Codex
   unchanged, and so does a failed `open` or a Jev error. The Codex agent is
   only built at that handoff, so an app launch never uses up the warm one
   (below). `CC_BUDDY_REFLEXES=0` turns this off.

Chosen by `tools/route_eval.py --model {jev,laya} --native` on 2026-09-23. Each
model was asked in its own idiom (`typed_ask.py`), with cut-offs fitted on the 270
seen requests (zero unsafe, zero wrong allowed). The table scores complete
reflexes on the 110-request holdout nobody tuned on:

| Arm | Fired / right | Coverage | Unsafe | Latency |
|---|---|---|---|---|
| **Rules, then Jev** (shipped) | 44 / 44 | 95.7% | 0 | µs when the rules hit, ~0.2 s otherwise |
| Jev alone | 42 / 42 | 91.3% | 0 | 207 ms p50, 298 ms p95 |
| Rules, then laya | 35 / 35 | 76.1% | 0 | µs, or 10 ms |
| Rules alone (pure code) | 34 / 34 | 73.9% | 0 | µs |
| laya alone | 21 / 9 | 19.6% | 1 | 10 ms p50 |

laya gets a 256-token head, so the app is a choice over a code shortlist of close
names. Even at the strictest cut-offs it fired on "Open Calendar and switch to
week view." Pure code is the fastest arm but misses a quarter of launches
("Get me Preview.", "Take me to Safari."). Jev closes most of that gap without
one unsafe fire, and costs ~0.2 s only on the requests the rules didn't
recognise.

**Quitting** follows the same path, with the owner's rule (2026-09-23): only the
explicit word **quit** counts. "close", "exit", "kill" and "force quit" stay with
Codex.

- **"quit <App>"** is a graceful quit, like ⌘Q, sent without waiting. An app
  with unsaved work shows its own save dialog. The reply says whether it quit,
  checked every 0.25 s for up to 2 s.
- **"quit all"** quits every regular app except those in `QUIT_ALL_KEEP`, plus
  `CC_BUDDY_QUIT_ALL_KEEP`. The built-in list keeps the terminals Claude Code
  runs in, Claude, ChatGPT/Codex, buddy's own app, and Finder.
- **Refused:** a question, a second clause, two apps, a tab or window, or an app
  that isn't installed. All of these go to Codex.

Jev is asked only when a request says "quit" (and not "force") and the rules
didn't take it. It answers two absolute yes/no questions ("only quitting one
app?", "quitting all apps?") and picks the app from the installed list. Its
cut-offs are fitted on `quit_tuning.json` with zero wrong quits allowed.
`tools/route_eval.py --quit` scores it on `holdout_quit.json`, 90 requests
written blind by an author who saw neither the code nor the rules. The bar is at
least 15 fired and 100% precision, because a wrong app quit is never
acceptable:

| Arm | Quits right | Coverage | Unsafe | Latency |
|---|---|---|---|---|
| **Rules, then Jev** (shipped) | 45 / 45 | 95.7% | 0 | µs, or ~0.2 s when asked |
| Jev alone, behind the "quit" gate | 42 / 42 | 89.4% | 0 | 220 ms p50 |
| Rules alone | 39 / 39 | 83.0% | 0 | µs |

Jev has to sit behind the "quit" gate. Asked about "close Mail" or "exit
Slack", it reads each as quitting one app (0.82 and 0.96). By meaning that's
right, but the owner's rule is about the word, and a literal reader cannot know
that. Fitted without the gate, no cut-off was clean. The earlier holdouts'
"Quit Spotify." and "Quit Safari." were relabelled from planner-only to quit
under the new rule; "Close Safari." stays planner-only.

**Codex warm-up** (`codex_warm.py`). Before a cold Codex run uses the goal, it
spends 4.8 to 11 s on the app-server, `initialize`, an ephemeral thread and the
`cua_repl` check (all measured 2026-09-23). `WarmCodex` does that ahead of time and
hands the ready agent to the next task that needs Codex. That task starts its
turn at once, and handing the agent over measured about 0 ms. Rules for the warm
agent:

- There is at most one, and it is used once.
- The next one is warmed after a task ends, never during it.
- It is replaced after `CC_BUDDY_CODEX_WARM_SECS` (default 900) or if its
  app-server dies.
- `CC_BUDDY_CODEX_PREWARM=0` turns it off.

## Controlling your logged-in Chrome (attach mode)

The owner's priority (2026-09-23): buddy should drive the **real, signed-in
Chrome**. With `CC_BUDDY_BROWSER_ATTACH=1`, the Playwright lane
(`browser_lane.py`) attaches to your running Chrome instead of launching its
own profile.

- **How it connects.** Chrome 136+ ignores `--remote-debugging-port` on your
  default profile. The supported route (Chrome 144+; this Mac has 153) is to
  switch on remote debugging **once** at `chrome://inspect/#remote-debugging`.
  Chrome then writes `DevToolsActivePort` (a port, then a WebSocket path) into
  `~/Library/Application Support/Google/Chrome`. The lane reads that file
  exactly as Google's chrome-devtools-mcp `--autoConnect` does, and attaches
  with Playwright's `connect_over_cdp`. `CC_BUDDY_CHROME_DIR` moves where it
  looks.
- **What it touches.** It works only in **its own new tab**. Closing it closes
  that tab and disconnects. It never closes your context, your windows or
  your other tabs. The same approval gate applies: a sensitive control (buy,
  send, delete, pay…) stops the plan until you say yes.
- **If it's off.** When remote debugging is off or Chrome isn't running, the
  lane says how to switch it on and never guesses.
- **Your click is the gate.** Chrome asks **"Allow remote debugging?"** for
  every new connection. On macOS, other apps can't read Chrome's
  `DevToolsActivePort` because app-data protection returns "Operation not
  permitted". So when that read is refused, the lane connects on Chrome's
  debugging port (9222 by default, `CC_BUDDY_CHROME_DEBUG_PORT`; it's the
  "Server running at" that chrome://inspect shows), and **your Allow click
  grants access**. It waits up to 2 minutes for the click. One connection
  serves many pages, so you click once per connection, not per page.
- **Verified 2026-09-23 on your real Chrome.** The attach took 6.1 s including
  the Allow click, and read `example.com` in 0.13 s. A second page on the same
  connection took 0.43 s with no new prompt. Afterward only buddy's tab had
  closed; your tab and Chrome were untouched.
- **Verified 2026-09-23 on a stand-in Chrome** (Chrome for Testing with
  `--remote-debugging-port=0`, which writes the same file). The lane attached
  in 0.75 s, typed a Wikipedia search and pressed Enter in its own tab. The
  "owner's" tab and the browser were still there afterward
  (`tests/test_browser_attach.py`; the live test runs with `CC_BUDDY_LIVE=1`).
- **Caution.** While remote debugging is on, any local program can connect to
  your Chrome, not just buddy. Switch it off in the same place when you don't
  need it.

## Two bodies for a web job: Codex or an isolated browser

A task that isn't a reflex goes to Codex, which drives your real Chrome
(signed in to your accounts) and the rest of the Mac. Some web jobs are
better done in an **isolated browser** with no accounts: public research,
comparing prices, filling a public form, testing a site. That keeps them away
from your logins and off your screen.

**Which body** (`browser_router.py`): Jev decides, asked the way TypeSafe's
docs recommend for a routing decision:

- **State:** only the request.
- **Options:** the two bodies are the options of one question, each described
  with its `what`, `for` and `not_for`.
- **Yes/no questions**, one judgment each:
  - Does it need your signed-in accounts?
  - Does it need anything outside a browser?
  - Do you want it shown on your own screen?
  - Is it a public-web job that can be reported back as text?

  Code combines the answers. The isolated body gets the task only when all four
  conditions hold *and* the choice picks it, with probability ≥ 0.95. An
  error, a timeout, or any doubt keeps the task with Codex.

The cut-offs are fitted on `browser_tuning.json` with zero unsafe routes
allowed, then scored once on `browser_holdout.json` (80 requests written blind,
27 of them deliberately hard). Result on 2026-09-23: 28 of 28 routed correctly,
**0 unsafe**, 77.8% coverage, 204 ms p50 (`tools/route_eval.py --browser`).

**The isolated body is not wired in yet**, so today every task goes to Codex.
- **auto-browser, retired 2026-09-23.**
  [auto-browser](https://github.com/LvcidPsyche/auto-browser) was the first
  isolated body. It needs Docker, which isn't installed here, it has no scored
  results, and it is a small project. Its adapter is in git history (last present at
  `b8c236d`).
- **Research that day.** It compared Vercel's agent-browser, Playwright and
  its MCP and CLI, browser-use, jev-ultrafast, Stagehand, Chrome DevTools MCP,
  auto-browser, and the status quo. Its recommendation: buddy's own Playwright
  lane (`browser_lane.py`) is the right isolated body. It is Python, already
  built, and has no LLM per step.
- **Techniques worth borrowing from
  [jev-ultrafast](https://github.com/browser-use/jev-ultrafast)** (MIT, same
  pattern): a single-call page snapshot, a check that nothing covers a target
  before clicking, and 50–200 ms wait caps.
- **Not adopted:** browser-use and Stagehand call an LLM on every step, and
  the Playwright MCP and CLI only wrap the library buddy already uses.
- **Next step:** build an evaluation set for the lane (recorded page
  snapshots, scored with `fastlane_eval.py`), then plug it in through
  `ReflexFirstAgent(make_auto=…, route_body=…)`.
- **Your real Chrome:** attaching the lane to it (Chrome's `chrome://inspect`
  remote-debugging switch) would reverse the lane's "never attach to the
  owner's Chrome" rule, so it waits for the owner's decision.

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

## Plan once, execute with Jev

The lane lost its A/B to turn overhead, not to its clicks: the planner spent a ~3.4 s turn to call
`delegate` and another to say it was done ([voice.md](voice.md#the-fast-lane)). So with
`CC_BUDDY_PLAN_EXEC=1` a request that reaches the planner is **planned once and then executed without it**:

```
astra, one call     the request + the front window's outline (labels and roles; no title, no field
                    contents, no screenshot) → a typed plan: plan_contract.py, strict JSON schema
executor            plan_executor.py walks the steps. A click is fast_lane.run_delegate, one step, on a
                    FRESH snapshot: the exact label first when the planner read one in the outline, then
                    its description. open_app/open_url are the helpers' own calls; type goes into the one
                    editable field, or Jev's pick among several; press_key is one key.
no closing turn     done is code: every step applied, none a suspected no-op, every `expect` held.
                    The sentence is the plan's `final_say`, or a local one that says what could not be
                    confirmed.
the floor           no plan, a bad plan, a checkpoint or a step that finds no control → today's
                    turn-by-turn loop, with a [note] of what was already applied
```

A plan is descriptions, never coordinates or element ids, so a plan written for a screen that has moved on
fails closed: the step finds no control. If the request names an app that is not in front, it is opened
before the outline is read (a planner shown nothing answers with a `checkpoint`); and because planning
takes seconds while the human keeps using the Mac, the executor brings the planned app back to the front
before its first step.

**Who decides a click.** `typed_ask.ask_jev_step` asks Jev the way this page says Jev must be asked: one
request carrying the `target` choice over the lane's own menu **plus an explicit `none`**, and beside it three
absolute nouls — `present`, `already_done`, `risky`. Two things were learned the hard way and are asserted
by tests: the nouls are answered against the *state*, so the control list has to be in the state too (with it
only in the choice's criteria, `present` read 0.33 for a right control and 0.37 for a missing one); and the
window title buys nothing (top-1 58/69 without it, 57/69 with it), so it is never sent.

In the lane's `jev` decide mode the keyword gate **proposes and Jev can refuse**: a gate pick is pressed only
when Jev's own top control is the same one; otherwise the step is Jev's, under cut-offs fitted with zero wrong
presses allowed. `tools/jev_step_eval.py`, on the Accessibility fixtures (menu of 25):

| holdout, 82 cases — **read before, so a tuning-set number** | pressed | right | wrong | coverage |
|---|---|---|---|---|
| the keyword gate alone (the click path until now) | 45 | 40 | 5 | 55.6 % |
| Jev alone, under the fitted cut-offs | 42 | 42 | 0 | 58.3 % |
| **the gate's pick only if Jev agrees, else Jev** (`jev` mode) | 52 | 51 | 1 | 70.8 % |

Jev's raw top-1 when the right control is on the menu: 57 of 60 on select (laya: 29.7 % on the cases it was
asked). Requests: 234 ms p50, 289 ms p90 over OpenRouter. **This is not a ship decision**: both fixture sets
had been read during the laya work, and `--check-default` refuses to enable `JEV_STEP_DEFAULT` without
`--fresh DIR`, a set nobody has read. Until one exists both switches ship off.

**What only you can approve.** A plan's `consequential` flag can only add a stop. So can everything else:
`ax_candidates.is_sensitive` (which gained the verbs the request-level gates already knew — Place Order,
Confirm, Publish, Transfer, Clear History, Format Disk, Turn Off, Add to Cart, Kill … — because a plan
executor presses controls nobody named aloud), Jev's `risky` noul (unfitted: 0.91 for "put the file in the
trash", 0.42 for the highest harmless step on select; cut at 0.5), a Return outside a search you dictated, and
composed text headed for a shell, which asks before the first key. The human is asked directly, with no planner
turn, and their yes lets exactly that control through once. Text the planner wrote is typed but never submitted
without that yes; whether text was yours or composed is checked against the request, not taken from the plan.

**Live, 2026-09-21** (`tools/plan_live.py --act`, Calendar, one planner call each):

| task | plan call | executor | total | before |
|---|---|---|---|---|
| "put the calendar on the year view, then go to next year" (2 clicks, both `confirmed`) | 3.72 s | 2.60 s | **6.64 s** | 13.1 s turn by turn, 16.9 s with `delegate` |
| "switch the calendar to the month view" (Calendar in front; label read from the outline) | 4.07 s | 0.86 s | 5.24 s | — |
| "open Calendar and switch it to the year view" (planned blind from a terminal) | 6.80 s | 2.25 s | 9.29 s | — |
| a three-step plan written by hand (`--plan`), no planner call | — | 2.88 s | 2.88 s | — |

Three runs on one app are a demonstration, not a measurement: the A/B with a code oracle per task, against the
turn-by-turn loop **and** against plan-once with the keyword gate alone (`plan_live.py --keyword-only` is that
arm), is what may flip `PLAN_EXEC_DEFAULT`. The plan call is now the whole cost, and `low` is the lowest
reasoning effort `gpt-6-astra` accepts.

## The browser lane: Playwright as the hands, Jev as the judge

The lane's ceiling on the Mac is the Accessibility tree over web content:
nameless buttons, stale frames, coordinate clicks. `browser_lane.py` gives web
goals a better body. Playwright drives **buddy's own Chromium** (a persistent
profile at `~/.config/cc-buddy-bridge/browser`, headed so you and the phone's
screenshot see it, signed in once by you), and the page supplies the
candidates: every visible control with its role, accessible name, state and
box, from one JavaScript evaluate. A click lands on the element, not on a
point. "Did it work" is a DOM question: the URL, the title, the text now on
the page, what is typed in a field.

Nothing about the brain changes. The page is presented as the same
`ax_candidates.Snapshot` of `Candidate`s the window walk produces, so
`plan_executor.run_plan` — astra's one plan, the keyword gate, Jev's typed step
answer, the sensitive-label table, "only the human approves" — runs unchanged
on top of it. The lane implements the nine-method senses/effectors contract
(`snapshot`, `text_visible`, `focused`, `frontmost_pid`, `screen_changed`,
`click_candidate`, `focus_and_type`, `press`, `settle`) and nothing else. It
never attaches to your Chrome: that needs a debugging port, a consent prompt
per session, and your live cookies under the daemon.

```
goal ── is_web_goal? (a URL, a site, "the browser", or the router's search) ──▶ browser lane
   no │                                                                          │ outline: role + label per control
      ▼                                                                          ▼
 reflex → lane → plan-once → planner (the Mac tiers)          astra plans once (plan_contract, "Frontmost app: browser")
                                                                                 │
                                                                                 ▼
                                             run_plan: per step the page's candidates → gate + Jev → Playwright → expect
                                                                                 │
                                                        complete → one sentence · partial/failed → the screenshot planner
```

A web search ("search the web for ramen near me") is one navigation with zero
model calls. When the lane takes a goal the Mac tiers stand down, so the URL
is never also opened in Safari.

Measured on this Mac, 2026-09-21: Chromium up in 0.19 s, a page in 0.52 s, a
full control snapshot in 40 ms. A four-step plan (open, tick a checkbox, type,
Return) ran through the real executor against a local page in the tests in
under 3 s including launch, every step `confirmed` by its oracle; a sensitive
control (`Place Order`) stopped for the human and a yes let exactly that
through.

Ships **off**. No evaluation set of recorded page snapshots exists yet; the
same `fastlane_eval.py` replay applies once one is recorded (a page snapshot
is a `Snapshot`, so the fixture format is unchanged). Install:
`pip install -e ".[browser]"` then `python -m playwright install chromium`.

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `CC_BUDDY_REFLEXES` | `1` | launch and web-search reflexes before the planner; `0` turns them off. The default is `tools/route_eval.py`'s decision, and `--check-default` asserts it |
| `CC_BUDDY_HEAD_MODEL` | `off` | `jev`: a spoken head pose ("look left", "a bit lower") is chosen by Jev in about a quarter of a second instead of ~3.2 s through the backend, which remains the fallback. The words of a turn that mentions a direction go to TypeSafe / OpenRouter |
| `CC_BUDDY_ROUTER_MODEL` | `off` | `jev`: ask Jev, natively, about a request the rules did not recognise; it may only add a bare launch. The request's words go to TypeSafe / OpenRouter, so it is your switch |
| `CC_BUDDY_LANE_FIRST` | `1` | the lane's router ([voice.md](voice.md#lane-first-the-router-before-the-planner)) |
| `CC_BUDDY_FAST_LANE_DECIDE` | `keyword` | who picks a lane step under the planner: `keyword`, `model`, or `jev` (the gate proposes, Jev can refuse; the step's words and the window's control labels go to `CC_BUDDY_JEV_ROUTE`, never the title) |
| `CC_BUDDY_PLAN_EXEC` | `0` | `1`: the planner plans once and `plan_executor.py` walks the plan with no planner turn between steps or at the end ([above](#plan-once-execute-with-jev)). Uses Jev for grounding when a route is configured, the keyword gate alone otherwise |
| `CC_BUDDY_PLAN_EXEC_REASONING` | `low` | the plan call's reasoning effort |
| `CC_BUDDY_DECIDER` | `laya` | the lane's model in `model` mode: `laya` or `jev` |
| `CC_BUDDY_BROWSER_LANE` | `0` | `1`: web goals go to buddy's own Chromium through Playwright ([above](#the-browser-lane-playwright-as-the-hands-jev-as-the-judge)); Jev grounds each step when `CC_BUDDY_JEV_STEP=1` and a route is configured, the keyword gate alone otherwise |
| `CC_BUDDY_BROWSER_PROFILE` | `~/.config/cc-buddy-bridge/browser` | the persistent Chromium profile (sign in here once) |
| `CC_BUDDY_BROWSER_HEADLESS` | `0` | `1` hides the window (benches); you should see it |
| `CC_BUDDY_EXPLORE` | `0` | `1`: buddy starts exploring on its own after ten idle minutes. Off: exploring is explicit only ("go explore", a text, `cc-buddy-bridge explore`) — owner decision, 2026-09-21 |

Every routing decision is in the task's run log (`{"route": …}`, `{"reflex": …}`, `{"lane_first": …}`),
so a wrong route can be read after the fact. Since 2026-09-21 the log ends with the **bill** (`{"bill": …}`):
the model's tokens in, cached and out and their USD at the grounded rates (`pricing.py`: gpt-6-astra
$10 / $1 cached / $50 per million, developers.openai.com, 2026-09-21), the Jev calls, input tokens and USD
made during the run ($0.042 per million, output free), and the wall seconds. `usd` is null for a model the
table does not know, never a guess. `tools/bill_report.py` sums a runs directory, one row per task and a
total. The Jev Engineering article's rule: "track the bill per completed task", so a cheap decision that
sent a worker down the wrong branch shows up as what it cost.

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
  [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast). Its 7.07 s figure is one whole
  11-action task (about 0.64 s an action), not a step. It clicks the argmax at any probability and has no
  notion of a consequential action, so none of its gating was taken.
- **Plan once, then ground each step: a closed step vocabulary, `target` as "how it would be labelled on
  screen", a `none` option on the grounding choice** — [savka777/jev-use](https://github.com/savka777/jev-use)
  (MIT), its dormant `Planner.swift` path. Assessed as the right shape with no evidence: one commit, no eval,
  every threshold a literal, and on that path a planned click gets no destructive check at all. The shape was
  taken; the thresholds and the safety model were not.
- **What an action did is one word from a closed set, and "unverifiable" is never success** —
  [trycua/cua](https://github.com/trycua/cua)'s action-result contract (MIT). Its own Jev recipe offers one
  executable candidate a step, so it proves plumbing and says nothing about choosing.
- **Nothing was taken from** [awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use):
  a fixed 2.0 s sleep after every action that its headline step time leaves out, one uncalibrated 0.4 threshold,
  no confirm path, and an OCR change-detector that misses text-sized changes (a toast, "Cart (7)" → "Cart (8)").
- **Reflexes under everything, a slow planner that never blocks action** —
  [rmalde/minecraft-agent](https://github.com/rmalde/minecraft-agent), assessed as *thin but real*: the three
  tiers are implemented and its async-planner test passes, but it is one commit by one author, and every
  headline number rests on evidence its README says is "excluded from Git", on a fixed seed in Peaceful
  mode. The ideas stand because they are ordinary control practice with other sources behind them; none of
  its numbers or timer constants were used.
