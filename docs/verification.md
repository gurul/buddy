# Formal verification (Lean 4)

Fourteen models of buddy's state machines are in Lean 4 under `verification/`: six from
the first pass and eight for the watcher (2026-09-25, below). A model of code that had a bug
has two kernel-checked theorems:

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
| `Buddy/WatchSsrf.lean` | `watch.py` `http_request`, `render` | every connection the watcher opens goes to a public address | DNS rebinding between the check and the connect; the browser's redirects, WebSockets and popups, which `page.route` never sees |
| `Buddy/WatchLink.lean` | `watch.py` `model_reading`, `evaluate` | a page alert links only the owner's page; a search alert only the search's link; no link hides behind words | a page's words talked the reader model into a URL that replaced the owner's link in buddy's alert |
| `Buddy/WatchRoute.lean` | `telegram.py` `_handle` | "/watch <words>" is never typed into the Claude session or sent to Codex | with a relay on, "/watch AAPL below 300" from buddy's own menu was typed into the terminal |

The replays are `bridge/tests/test_watch_lean_*.py`, `test_watch_security.py`,
`test_watch_shapes_replay.py` and `test_watch_reverify.py`. The replays that start a real
Chromium are marked `live` and run with `CC_BUDDY_LIVE=1`: the guard test, its negative
control, and the overlay-navigation test. The render-deadline tests use a stand-in child
process and run in the normal suite.

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
