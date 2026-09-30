# Formal verification (Lean 4)

Twenty-one models of buddy's state machines are in Lean 4 under `verification/`: six from
the first pass, eight for the watcher (2026-09-25), four for the Voice PE controller
(2026-09-26), two for the Holo lane and the OpenAI seam (2026-09-28), and one for the
chief of staff (2026-09-29, below). A model of code that had a bug has two kernel-checked
theorems:

- **`current_violates`** — a concrete trace on a model of the code *as it was* on
  2026-09-25 (commit `0b7d36d`) after which the property is false: the bug, proved.
- **`fixed_invariant`** — the property holds on a model of the fixed code for **every**
  trace (induction over an arbitrary event list), not just the counterexample.

Every fix also has a pytest in `bridge/tests/` that replays the counterexample against the
real Python classes. Each replay was run before the fix and failed; it passes now.

`check-all.mjs` also accepts a third, honest shape, for code that was right from the start:
**`code_invariant`**, the property proved of the code as it is.

## The first six models

| Model | Code | Property | What the counterexample was |
|---|---|---|---|
| `Buddy/Command.lean` | `matchers.py` `classify_command` | "allow" answers only one simple command whose program cannot run another command or write a file — for **every** `matchers.toml` | The built-in `^echo( |$)` allowed `echo x; rm -rf ~`. Also `ls && curl … \| sh`, `ls $(…)`, `env rm -rf /`, `find -execdir`, `fd -x`, `rg --pre`, `tree -o`, `git log --output`. The daemon turned that "allow" into an explicit allow before Jev or the phone saw it. |
| `Buddy/Questions.lean` | `telegram.py` `_ask_user` / `decide_permission` | while any question waits, the typed-answer slot names the newest one still waiting | Three nested questions A, B, C; B then C end; the slot empties while A still waits, so a typed yes for A went to Claude. |
| `Buddy/TaskStop.lean` | `telegram.py` `_run_agent` / `_stop` | a task that completed is never reported "Stopped.", and its result is sent | A typed "stop" after the agent returned (while the progress message closed) said "Stopped." and ate the result. |
| `Buddy/Forget.lean` | `memory.py` `forget_apply`, `records.py`, `dream.py`, `mem0_memory.py` | once forget completes, no interleaving with the dream or the index ingest puts a forgotten line back in the records, git or the index | A forget during the dream's model call was undone by the dream's write, and its commit put the words back into the history the forget had squashed. Same for the mem0 ingest. |
| `Buddy/Serial.lean` | `serial_transport.py`, `daemon.py` watchdog and ack router, `folder_push.py` | (A) at most one write on the port at a time; (B) a flapping link reaches the RTS reset; (C) every ack reaches its own request's waiter | (A) a sender cancelled by `wait_for` freed the lock while its thread still wrote; (B) the reconnect's status ack counted as a clean poll, so a link that flapped never escalated to the RTS reset; (C) an ack that arrived before its waiter registered was dropped, and a late chunk ack answered the next chunk. |
| `Buddy/Voice.lean` | `voice_agent.py` `_backend_event`, `_ask_user` | once nothing is in flight the face is not stuck on thinking/speaking; a question slot is always released | A backend stream `error` left the response "active" forever: the face held "thinking" and every later reply was held back. A failed send of a spoken question left the slot set, blocking the idle close and the goodbye. |

## The watcher's eight models

Written by an ultracode workflow (2026-09-25):
- four modelers, one per subsystem
- an adversarial verifier per model, which re-ran the checker, compared the Lean
  step functions with the Python line by line, and re-ran every replay
- two code reviewers, whose own findings became four more models

Every model found a bug, and the Python fix is each model's `fixed_invariant`
step. A second workflow then checked each fixed step against the Python as
written:
- four auditors, one per two models
- two adversarial reviewers of the code written after the first pass
- a completeness critic

It found where the Python goes beyond its model. Each deviation is now written
in its model's docstring:
- the 1e-9 tolerance on percentages
- a first reading the owner never saw
- the fix for a check that raises, which sits in `_run_one` and `add`
- notes made plain for every kind
- the exponent cap and OpenRouter's hold
- the IPv6 prefixes, the one-request proxy and the process-tree kill

It also found new bugs, all fixed, each with a replay in
`test_watch_reverify.py`.

The watcher's code was not committed while it was modelled, so its
`current_violates` traces refer to the working tree of 2026-09-25, not to a commit.
The line numbers in the docstrings are those of that tree. The functions they name
are current.

| Model | Code | Property | What the counterexample was |
|---|---|---|---|
| `Buddy/WatchLimiter.lean` | `watch.py` `RateLimiter`, the gate in `_run_one` / `add` | the bucket never exceeds its burst and matches a monotone-clock reference (a clock that steps back mints nothing); no read goes to a host inside its hold; the model cap counts every day once | a second 429 shortened a Retry-After hold still in force; a check let through before a 429 landed read the host during its hold; a day label the clock stepped back onto got a fresh model cap |
| `Buddy/WatchConditions.lean` | `watch.py` `evaluate` | no alert on a reading the owner saw at add time; an edge condition alerts at most once per false-to-true transition; a percentage alert fires exactly at the move and re-bases | a zero baseline made `drop_pct` / `rise_pct` raise `ZeroDivisionError` (and stopped the tick); an exact move (3.00 to 2.70) missed by float rounding |
| `Buddy/WatchScheduler.lean` | `watch.py` `tick`, `add`, `remove`, `check`, `_run_one` | ids are unique and never reused; a removed watch texts nothing; the via ladder only moves forward; "I can't read" is said once per streak; a new watch wakes the loop | a watch removed mid-check still texted its alert; one watch whose check raised anything but `FetchError` stopped every watch after it; removing the newest watch handed its id to the next |
| `Buddy/WatchTicketmaster.lean` | `watch.py` `ticketmaster_reading`, `pick_attraction`, `evaluate` | available iff an event is onsale or rescheduled, or a presale window is open now; an ended sale is never listed; no price is `None`, never 0; the owner hears once when the sale opens | a sale already open at the first background check (the add's check failed) was never texted; "The Jimi Hendrix Experience" was dropped as a tribute act; every game ("A vs. B") was dropped |
| `Buddy/WatchStarve.lean` | `watch.py` `tick`, `_run_one` | every watch due when a tick starts is taken up by that tick | a read that raised `LookupError` (an unknown charset) left `next_at` unchanged and aborted the tick, every time |
| `Buddy/WatchSsrf.lean` | `watch.py` `http_request`, `tls_request`, `render` | every connection the watcher opens goes to a public address | DNS rebinding between the check and the connect; the browser's redirects, WebSockets and popups, which `page.route` never sees |
| `Buddy/WatchLink.lean` | `watch.py` `model_reading`, `evaluate` | a page alert links only the owner's page; a search alert only the search's link; no link hides behind words | a page's words talked the reader model into a URL that replaced the owner's link in buddy's alert |
| `Buddy/WatchRoute.lean` | `telegram.py` `_handle` | "/watch <words>" is never typed into the Claude session or sent to Codex | with a relay on, "/watch AAPL below 300" from buddy's own menu was typed into the terminal |

The replays are `bridge/tests/test_watch_lean_*.py`, `test_watch_security.py`,
`test_watch_shapes_replay.py` and `test_watch_reverify.py`. The replays that start a real
Chromium are marked `live` and run with `CC_BUDDY_LIVE=1`: the guard test, its negative
control, and the overlay-navigation test. The render-deadline tests use a stand-in child
process and run in the normal suite.

## The Voice PE controller's four models

The owner asked for Lean proofs before these changes are pushed (2026-09-26). Each model
found a bug. Three were caught first by a pytest or seen live, and the Lean
`current_violates` restates them. The desk-call model found four more that nothing had
caught. The replays are named in each row.

| Model | Code | Property | What the counterexample was |
|---|---|---|---|
| `Buddy/Pacing.lean` | `desk_call.py` `BoardSpeaker.play` | the daemon is never more than `LEAD_SECS` (3 s) ahead of the board's playback, for any piece size up to the lead | waiting until 3 s ahead and then sending another 100 ms piece left it 3.1 s ahead (`test_the_daemon_stays_at_most_three_seconds_ahead_of_the_board`) |
| `Buddy/ControllerRoute.lean` | `controller.py` `mirror`, `_replay`, `_on_message`; the tee in `daemon.py` | the controller is never sent sound-on (beeps only on the StackChan); only `ptt`, `key` and `focus` from it reach the daemon | the first version mirrored `{"cmd":"sound","on":true}` to the Voice PE, live or on the next connect (`test_the_beeps_are_the_robots_alone`) |
| `Buddy/PortPick.lean` | `serial_transport.py` `_resolve_port` | the robot's glob never opens a node whose USB serial is skipped; a `usbsn:` pick is that serial's node | seen live: `/dev/cu.usbmodem101` (the Voice PE) sorted before `31201` (the StackChan) and was opened as the robot. The model's docstring also named a gap it could not cover: `comports()` raising skipped the skip filter. With a skip set, that now waits instead of guessing (`test_two_esp32_boards_are_told_apart_by_usb_serial`, `test_when_the_usb_list_fails_a_skip_is_never_guessed_past`) |
| `Buddy/DeskCall.lean` | `desk_call.py` `DeskCalls`, `phone_call.py` `Call.run`, `telegram.py` `listen`, `daemon.py` `ptt` and `_wake_suppressed` | the desk mic is open only while the button is held; the chat's one call listener is never cleared under a live call; the wake word stays off while the button is held | four traces: a desk call ending cleared a phone call's reader, so the phone call went silent; a quick tap opened the mic after its release; a release during a conversation was dropped, leaving the mic open; a call ending mid-hold turned the wake word back on (`bridge/tests/test_desk_call_lean.py`) |

## The Holo lane and the OpenAI seam (2026-09-28)

Two models written with the code they check, the same day. Both have a `current_violates`
on code that shipped (the cli-driver Holo lane, which refused every correction; the
OpenAI seam before its retry, which died on one TLS drop), and `ResponseRetry.lean` also
proves the obvious alternative wrong. The replays are `bridge/tests/test_holo_lean.py`
(against the real `HoloComputerAgent` with a fake driver, and the real
`make_stream_creator` with a fake client); the live proof of the steering path is
`bridge/tools/holo_live_check.py`.

| Model | Code | Property | What the counterexample was |
|---|---|---|---|
| `Buddy/HoloSteer.lean` | `holo_computer.py` `steer`, `cancel`, `_run_client`; `holo_driver.py` `Turn.announce`, `Turn.steer` | a correction given while the task runs (and no stop was asked) is never refused; a stop that was asked never lets a result be certified, even when Holo finished first; a certified result has no correction still waiting | `[start, steer]`: the cli driver returned False for every correction, so the owner's words were dropped while the task went on. Also proved: a correction before the session exists is delivered when the session is announced; a stop then a completed final is "stopped" |
| `Buddy/ResponseRetry.lean` | `computer_agent.py` `make_response_creator`, `make_stream_creator` | no text is read out twice; a turn fails only after two drops, or a drop after text was read out | `[drop]`: one `ssl.SSLError` mid-read killed the turn (three phone-call turns on 2026-09-28). The naive cure, "always retry once", is proved wrong too: `[text, drop, text]` reads the sentence out twice. The fix retries once, only while nothing has been read out |

## The chief of staff (2026-09-29)

`Buddy/Chief.lean` was written before the chief's Python, from its design, so it has the
third shape: one `code_invariant`, proved over every trace. There was never a buggy version
to put a `current_violates` on.

The model is one card, the Desk it shares with every other card, and the lesson store. It
has one step function over 23 events:
- the owner's taps and words: a Go, "go c<N>", Change, Reopen, Drop, "done c<N>", a raise;
- the executors' results, and the oracles' readings of the evidence;
- a restart, and "It happened" / "It didn't";
- Reflexion: a lesson, and a retry;
- the clock, a new date, breakpoints, and `Desk.offer`.

`code_invariant` is the conjunction of seven properties:

| Property | What it says |
|---|---|
| DoneNeedsEvidence | A card is done only on the owner's word, or when every done check is confirmed. Every confirmation must rest on evidence the oracle saw outside the executor's own sentence. |
| OneYesOneAct | Every one-way dispatch spends its own yes, given for that phase at the card's revision at that moment. A no, a hold word or a 180 s timeout is never a yes. A revision cancels a yes that has not been spent. |
| BudgetBeforeDispatch | What the card spent, plus the ceiling held by its running phase, never exceeds the cap. The only excess allowed is what an executor ran past its own ceiling. |
| PushBudgetQuiet | An unprompted push, or a reminder the owner asked for, goes out now only outside 22:30–08:00 and at a breakpoint. An unprompted push also needs pushes on, and at most 2 go out a day. The day's count survives a restart. No Go is asked, and no act or one-way phase starts, in quiet hours. |
| NoActReplayAfterRestart | A one-way act that was in flight at a restart never runs again without a yes given after the restart. |
| OneWayNeverAutoRetried | A one-way phase's attempt count rises only with a fresh yes. A two-way phase retries itself at most twice (3 trials). |
| LessonsBounded | At most 3 lessons are kept per (executor, phase kind) key: Reflexion's Omega (Shinn et al., NeurIPS 2023). |

The positive control is `naive_violates`. The same machine with an oracle that takes the
executor's "done" as evidence closes a research card as done with no evidence. With the
code's oracle, `executor_word_is_unverified` shows the same trace ends unverified.

Worked traces, each checked by `decide +kernel`:
- the standing-desk job of the design closes as done, with one yes for one act;
- a timed-out Go approves nothing;
- a revision cancels the yes;
- after a restart, "It didn't" runs nothing until a new yes;
- a failed one-way act waits for a Go even when the reflection says retry;
- a two-way phase stops after three trials;
- the third unprompted push of a day is batched, across a restart;
- an approved act does not start at 23:00.

The replay, `bridge/tests/test_chief_lean.py`, holds the Python to the model:

- **The rules.** The model's step function is copied into Python, rule by rule, each citing
  its line in `Chief.lean`. The constants (quiet hours, push budget, retries, Omega) are read
  from the `.lean` file and compared with the code's. The copy must reproduce every worked
  trace above, and `quiet` must equal the desk's reading for all 1,440 minutes of a day.
- **The traces.** 240 seeded traces, of 40 moves each, drive the real `Chief` with fakes.
  The moves are:
  - each executor's result: a web read with four links, a failed read, an options sheet, a
    reply that is not one, an act that saw the confirmation on screen, an act whose only claim
    is its own sentence, and a failed act;
  - every owner answer and code word;
  - the clock across the quiet-hour edges;
  - restarts.

  Every ledger line becomes a model event. After every move, the step started, every yes and
  every Go asked, each check's verdict (set against the evidence the fakes produced), the
  card's status, revision, steps, attempts, retries and lessons must agree, and the model's
  `Spec` must hold. A coverage test checks that the traces reached every rule.
- **The desk.** 36 traces of 120 events run each of the three push modes through the model's
  `offer` and a desk reloaded from the same folder.
- **The positive control.** A variant of the code takes the Mac executor's sentence for a
  screen state. The replay stops at `ui_seen`: the code says confirmed, the model
  unverifiable. The same variant agrees with the model's `naiveVerdict`, and its end state
  breaks DoneNeedsEvidence, as `naive_violates` proves.

Where the code and the model differ in shape but not in the properties, the replay maps one
onto the other. Its docstring lists each case. Two examples:
- after a failed act, the model waits for a Go, while the code closes the card as unverified
  until Reopen;
- "go c12" on an act in doubt is It didn't then a Go in the model, and a Go by id in the code.

The replay also found a liveness gap, now fixed. A card closed while a web step ran, then
reopened, kept the step marked running and did not move until a restart. The step's late result
is now held and put on the step at Reopen, as the model does
(`test_a_card_closed_during_a_web_step_then_reopened_moves_on`).

## Running it

```bash
# toolchain (once): elan with Lean 4.34.1, pinned in verification/lean-toolchain
curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --no-modify-path

cd verification
~/.elan/bin/lake build          # compiles every model
node check-all.mjs              # every theorem: no sorry, no native_decide, standard axioms only
```

`check.mjs` is the honest gate. It fails on any `sorry`. It then asks Lean which axioms each theorem
rests on, and refuses anything outside `propext`, `Classical.choice` and `Quot.sound`. So a proof
closed by `native_decide`, which trusts compiled code, is refused. The models use core Lean only,
with no Mathlib.

## What a proof here does and does not say

A theorem is about the **model**, not the Python. Two things connect them:

- **Every transition cites the Python lines it models.** A reviewer can check the correspondence
  line by line.
- **The replay tests run the counterexample against the real code.** For the command classifier,
  `test_is_simple_agrees_with_the_lean_model` also runs the Lean `simple` and the Python `is_simple`
  on 3,000 random commands and requires them to agree on each one. Its positive control, dropping
  `#` from the Lean model, makes it fail.

Stated assumptions, per model:

- **Command.** The shell reads the characters in `shellMeta`, and whitespace other than a space,
  as structure. The runner list and the run-or-write flag table cover the programs an owner would
  allow, and the theorem is only as strong as that table. A command the guard refuses falls
  through as unmatched: to Claude Code's own flow, or to "ask" under `strict`.
- **Questions.** The done-callback that moves the slot is modelled as running when the future
  settles. asyncio actually runs it one loop step later.
- **TaskStop.** A cancel before the agent returns makes it return its stopped result, as the
  agent contract says.
- **Forget.**
  - Each step is atomic under its lock, and model output contains only lines from its input.
  - Anything said after the forget's redaction step counts as new input.
  - The records generation works across processes (`records/.forgets` under the folder flock).
    The mem0 one is in-process, relying on the local store allowing one process at a time.
- **Serial.**
  - (B) is proved for every flap cycle of the form "resync acks, at most one answered poll,
    then deaf". The rule that only two consecutive answered polls de-escalate is proved for
    every single step.
  - (C) treats request ids as distinct. On the wire a chunk is identified by (type, bytes
    written to the file so far), and other ack types still match by type.
- **Voice.** The model covers captions mode, the default.
- **Chief.** One card at a time is modelled; cards share only the Desk and the lesson store,
  which are modelled whole. A card runs one phase at a time. The door floor (the verb
  regex), the choice of executor, the Codex floor for acts and the one Mac slot are outside
  the model; they are unit-tested. A door only rises, and only before the phase first ran.
  A reflection that errors is no event: no lesson, no retry. Money and wall time are two
  whole-number counters.
