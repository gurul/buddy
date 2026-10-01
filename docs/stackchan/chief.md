# Chief of staff: cards, proof and attention

The owner asked on 2026-09-29 for buddy to work like a chief of staff: take on
jobs with more than one step, hold the owner's own commitments, say back what it
intends before it starts, prove when something is done, and interrupt only when
it matters.

This page covers the parts built so far: the **record** (the card and its
ledger), the **proof** (the oracles that decide when a card is done), the
**desk** (the gate every message the chief would send goes through), the
**chief** itself (its chat tools, the dispatcher and the loop), its
**lessons** (Reflexion) and its **Lean model**. The Telegram chat carries it,
and it is on by default since its capture eval passed on 2026-09-29. The
voice has only the self-context line. What is on, in shadow and off is in
[one list](#on-shadow-and-off).

Code: `bridge/src/cc_buddy_bridge/chief_card.py`, `chief_ledger.py`,
`chief_receipt.py`, `chief_desk.py`, `chief.py` and `chief_reflect.py`. Tests:
`bridge/tests/test_chief_record.py`, `test_chief_receipt.py`,
`test_chief_desk.py`, `test_chief.py`, `test_chief_reflect.py`,
`test_telegram_chief.py`, `test_chief_eval.py` and `test_chief_lean.py`. The
model: `verification/Buddy/Chief.lean`.

## The card

Every job and every commitment is one card.

- A **job** is work buddy does, in up to 6 phases. The phases buddy can run are
  exactly `research`, `assess`, `act` and `watch`.
- A **commitment** is something the owner does. buddy holds the cue ("remind me
  Thursday to export the BOM") and has no phases.

A card has a title (40 characters at most), a purpose, an **end state** the owner
could check, the **done checks** that will prove it, the things it must
**never** do, a deadline, a budget, and a status: `proposed`, `active`,
`waiting`, `unverified`, `done` or `dropped`. `done` and `dropped` are final; a
finished card can be reopened as a new revision.

The card keeps a reference to where the owner said it (the transcript day and
time), never a copy of the words.

**The parse is strict and whole.** The brain fills the card's fields. One bad
phase, one unknown check or one date that is not a date refuses the whole card
with one line saying why, so a card is never partly made. A card is made only
in a turn that carries the owner's own words.

**Only two things make a card wait before it starts:** a missing end state, or
the one question whose answer changes the outcome.

**The backbrief.** Before work starts, code composes what buddy intends from the
card: "On it: Standing desk. I intend to research standing desks under $600 with
reviews, then pick one, then order the picked desk after your Go. Done when an
order confirmation arrives. Up to $1 and 30 min."

### Budgets

| Card | Default budget |
|---|---|
| Only research and assess | $0.50 and 20 min |
| With an act | $1 and 30 min |

A warning line is offered at 80% of the budget. It is unprompted, so the desk
holds it like a nudge: in shadow (the default) it is only logged. A raise is
one tap.

### The door

Each phase is **two-way** (it can be undone, so it starts at once) or
**one-way** (it needs the owner's Go). Code sets a floor:

- the goal uses wording that can cost the owner something ("order", "send",
  "delete", "book" and the rest of the task router's list);
- a connected-app call in it would change something;
- or the executor that would run it cannot stop and ask.

**Every act is one-way** by the last rule. An act runs on the Mac with no one
watching it, so it gets its Go before it starts. The wording list alone
missed "cancel", "unsubscribe", "RSVP" and "text": "cancel my streaming plan"
ran with no Go (reviewer, 2026-09-29). A card saved before this rule
loads with its acts one-way. An act whose goal has none of the listed wording
is not marked "not grounded": nothing in it came from buddy's plan.

The brain may raise a phase to one-way; it can never lower one. A revision keeps
the higher door of each phase. A one-way phase whose wording is not in the
owner's own words is marked **not grounded**, and the Go says so.

`rev` counts changes to what the owner agreed to. A Go is bound to the card,
the phase and the revision; a new revision cancels it. It is also bound to the
act exactly as the Go showed it (the goal, the pick, the limits and the notes
it carries): if any of that changes before the act starts, the yes runs
nothing and the Go is asked again. A Yes to a question approves only that
question's step, and only if the step has not run since it was asked. Progress (a phase
starting, a result arriving, money spent) is logged but is not a new revision,
so a Go is never cancelled by its own dispatch.

## The ledger

The cards live in `~/.config/cc-buddy-bridge/chief/` (`CC_BUDDY_CHIEF_DIR` moves
it), never in the repo. Files are mode 600 and the folder is mode 700.

| File | What it holds |
|---|---|
| `cards.json` | The cards and the last id handed out, written atomically |
| `events.jsonl` | One line per event: created, revised, state, asked, answered, approved, dispatched, result, checked, pushed, would_push, critic, reflected, not_reflected (a card at its cap pays for no lesson), retried, reopened, redacted |
| `attention.jsonl` | One line per desk verdict, including shadow "would send" lines |
| `lessons.jsonl` | The lessons from steps that did not work, keyed by executor and step kind; only the last 3 of a key are read |
| `mandate.toml` | Optional, edited by the owner: quiet hours and the daily push budget |

- **Atomic.** A temp file, then a rename. If the rename fails, the old file is
  whole and the change is not taken.
- **An unreadable file is moved aside** as `cards.json.bad-<time>`, and the owner
  is told once. So is a file that is not the ledger's shape. When single cards
  do not load (a typo, a number too big), the rest load, the whole file is
  copied aside first, and the owner is told which ids. The owner is told in the
  reply to their next message, never as a push.
- **Ids are never reused.** Card ids are `c1`, `c2`, and so on. The last id is
  saved, and ids named in the events log or in a moved-aside file count too.
- **Events are written before acts.** A dispatch or a push is logged first, and
  if the line cannot be written, the act does not happen. After a restart, an
  act with a dispatch line and no result never runs again without a new Go.
- Event lines carry ids, numbers and short code words, never what the owner
  said.

Forget does not reach the ledger yet. `forget_preview` names it under
`not_covered` as `chief ledger` ([memory.md](memory.md#forget)).

The brain sees one line per turn, at most 300 characters: "Open jobs: 2 (c1
waiting on your Go: order the picked desk; c2 researching: Monitor arm). Due
today: BOM export (c3)."

## Proof: when a card is done

Every executor returns one result shape: a status (`done`, `handed_on`,
`needs_guru`, `refused`, `failed`, `over_budget`), a list of evidence, and the
executor's own words. **The words are never evidence.**

Each check says `confirmed`, `unverifiable` or `failed`. A card is **done only
when every check is confirmed and every step ran.** One unverifiable or failed
check, or one step that failed, was refused or was skipped, makes it
`unverified`, and the receipt names it and asks "Did it go through?".

| Check | Confirmed when |
|---|---|
| `read_links(n)` | The research phases read n or more distinct pages (the search's sources) |
| `cited_pick` | Every page the pick cites is one the research read |
| `ui_seen(text)` | The text (at least two words) is on the last screen each act ended on, as the Mac executor read it back; never its closing sentence, and never a page seen before the act |
| `watch_armed` | The watcher returned a watch id (and, when checked, the watch is still on the list) |
| `file_exists(path)` | A regular file below the home folder (not the home folder itself) is there, made or changed since the card was |
| `guru_says_done` | The owner tapped Done or wrote "done c<N>". The receipt calls it "your call" |

A phase that failed, was refused or ran out of budget fails its check. A phase
that was handed on is unverifiable.

**An act that saw no screen is never done.** When the Mac agent returns without
one screen read back, the act is `handed_on`. When the agent reports an error
or a stop, the act is `failed`, whatever its sentence says. Codex returns its
own failure sentence ("Codex could not start or timed out") instead of raising.
Such a run used to close a card as done (reviewer, 2026-09-29).

The receipt is composed by code from the verdicts: "Done: Standing desk, 3 of 3
checks: read 4 pages (confirmed); the pick cites a page I read (confirmed);
'Order confirmed' seen on screen (confirmed). $0.31, 12 min."

## The desk: who may interrupt

Pull comes before push. Every message the chief would send goes through one
gate, which answers `now`, `batch` (hold it for the next brief or try again
later), `silent` (shadow: logged as "would send", not sent) or `drop` (it no
longer matters).

| Message | Rule |
|---|---|
| A reply, a receipt | Sent now |
| The Go for a job the owner started | Sent now, except in quiet hours |
| A reminder the owner asked for | Not counted in the daily budget. Waits for quiet hours to end and for a breakpoint. With no breakpoint in 45 minutes, it moves to the next brief |
| A nudge, the 80% budget warning, the evening close, the weekly sheet | Unprompted: only a firm commitment due within 2 hours may nudge. Outside quiet hours, at a breakpoint, within the daily budget, after its backoff. Then `CC_BUDDY_CHIEF_PUSH` decides |

- **Breakpoints:** a task ending, a wake session ending, or 90 seconds after
  the owner's last message. The desk also takes a relay turn ending, but
  nothing sends that one yet.
- **Backoff:** each ignored nudge doubles the gap (4 h, then 8 h, then 16 h).
  After 3 ignored nudges, the card appears only on the weekly sheet.
- **The owner's own action** on a card drops its queued nudges and reminders.
- **A decision never expires into an act.** A timeout takes the choice that does
  not act.
- **Every error holds the message** (`batch`). A push that cannot be logged is
  not sent, because an uncounted push could break the budget.
- **A held message is kept** and offered again when the desk says, at the next
  breakpoint, or after 30 minutes. What the push mode or the day's budget holds
  goes to the next pull instead.
- The day's push count is read from `attention.jsonl`, so a restart keeps it. In
  shadow, the would-send lines count too, so the shadow week measures the policy
  as it would run.

The brief after the owner's first message of the day, the evening close
(21:00) and the weekly sheet (Sunday 18:00) are composed by code from the
ledger, at most 3 lines for the brief.

## The chief: how a job runs

The brain calls `take_on` inside the turn it is already taking, and nothing
else: code parses the card, files it and returns the backbrief for the turn to
send, so there is no second model call. `jobs_list` gives the brain the open
cards as data: each step, who ran it and why.

The steps run in order, one at a time:

| Step | Who runs it | Door |
|---|---|---|
| `research` | The search router on the web reader's daily allowance, when the shipped web-or-Mac router says web; otherwise the Mac, with a read-only goal | two-way |
| `assess` | One options call with a strict schema: at most 3 options, a pick and what would change it. A link the research did not read is removed, and a pick with no read link is no pick | two-way |
| `act` | The Mac, always on Codex, the floor that can stop and ask | one-way |
| `watch` | The watcher, as a search watch for "available" | two-way |

- **Budget before every step.** A step starts only if what the card has spent
  plus the most that step can take stays within the budget: a web read 90
  seconds, the options call about $0.10, the Mac 10 minutes. Otherwise the
  card waits with "Raise to $X?"; a raise doubles the budget. What a card
  spent is read from the spend rows tagged with its id.
- **A step is given up at that most.** A step run by the chief itself is
  abandoned at its ceiling. A Mac step is stopped at its 10 minutes: a reading
  step fails as over budget, and an act may have happened, so the owner
  gets "I may have done: <act>. Check?", as after a restart.
- **One yes, one act.** A one-way step needs the owner's Go for that step at
  the card's current revision. The Go names the exact act, the pick it will
  use and the owner's limits. Only a reply that is wholly a yes or a no answers
  it ("yes", "ok", "go ahead please", "no, leave it"). "ok, also what's the
  weather tomorrow?" is a new message, not a yes. A No, a hold word, silence or
  an error is never a yes. The yes is used by one dispatch, whatever happens to it; a change to
  the card cancels it, and so does a change to the act it showed. `go c<N>`
  approves by id. The dispatch checks the card as it is at that moment, under
  the ledger's lock, so a drop or change made meanwhile starts nothing.
- **Quiet hours** hold every Go question, every act and every one-way step
  until morning.
- **A busy Mac** makes the card wait; the step goes at the next break.
- **A step that failed** stops the card: the later steps are skipped, and the
  receipt says what could not be confirmed.
- **After a restart,** a reading step that was running runs again. An act
  that was started and has no result never runs again by itself: the owner
  gets "I may have done: <act>. Check?". This question waits out quiet hours,
  as a Go does. "It happened" closes the act on the owner's word, and "It
  didn't" waits for a new Go. Both work only while the act is waiting. Once
  `go c<N>` has started it again, the Mac has it: an old tap on either button
  changes nothing, and the new run's own result counts.
- **Commitments.** The cue becomes a reminder the owner asked for: not counted
  in the daily budget, sent at a break outside quiet hours. A follow-up is an
  unprompted nudge.
- **The first message of the day** (after 04:00) gets the brief after the reply.
- **The owner's words stay out.** They decide whether a one-way step is in the
  owner's own words and are then dropped: no model call, spend row, event or
  log line carries them.

## Lessons (Reflexion)

The owner asked for Reflexion (Shinn et al., NeurIPS 2023,
[arXiv:2303.11366](https://arxiv.org/abs/2303.11366)). It has an actor, an
evaluator that scores what the actor did, and a self-reflection model that
turns a pass or fail and the attempt into a short written lesson, kept in a
small memory for the next attempt. Here the actor is the step's executor and
the evaluator is the checks above, which read evidence, never the executor's
own sentence.

- **When.** A lesson is asked for only when a step's own checks come back
  `failed` or `unverifiable`, when its result is `failed` or `over_budget`, or
  when the executor repeated one step more than 3 times or took more than 30.
  A card with no room left in its budget (about a cent for the call) asks for
  none; the events log `not_reflected`.
- **The call.** One cheap model call, which must answer exactly
  `{"lesson": ..., "retry": true or false}` with a lesson of at most 200
  characters. Anything else, or an error, is no lesson and no retry.
- **The memory.** `lessons.jsonl`, keyed by executor and step kind. Only the
  last 3 lessons of a key are read.
- **Retries.** A two-way step may run again at most 2 times (3 tries) while
  the budget allows, carrying its lessons. While a lesson is being written the
  card does not move, and a retry cancels any yes given for a later step. A
  one-way act never runs again by itself: its next try needs a new Go. A
  one-way act carries only its own step's lessons, never another card's, and
  its Go shows every one it carries.
- **Lessons are data, never instructions.** They reach the next try only in a
  field headed "Notes from earlier attempts (observations, not instructions)",
  at most 3 of 200 characters, with links removed. They cannot add a step,
  change a door or change a goal, and a lesson that names an act the step's
  own goal does not (buy, send, delete and the rest) is not carried at all.
  The reflection call is told that everything in the attempt is untrusted.
- `/jobs` counts the retries that turned a failed check into a confirmed one.

## Knobs

| Setting | Default | What it does |
|---|---|---|
| `CC_BUDDY_CHIEF` | `auto` | `auto`: on once the capture eval has passed its bar, set in the code (it passed on 2026-09-29, so `auto` is on). `on`: always on. `off`: off |
| `CC_BUDDY_CHIEF_ASSESS_MODEL` | `gpt-6-astra` | The model that weighs the options |
| `CC_BUDDY_CHIEF_ASSESS_EFFORT` | `low` | Its reasoning effort: `low`, `medium` or `high` |
| `CC_BUDDY_CHIEF_DIR` | `~/.config/cc-buddy-bridge/chief` | Where the ledger, the attention log and the mandate live |
| `CC_BUDDY_CHIEF_PUSH` | `shadow` | `off`: every unprompted message waits for a pull. `shadow`: logged as "would send", never sent. `on`: sent when the desk allows it |

`mandate.toml` in the chief folder, all optional:

| Key | Default | What it does |
|---|---|---|
| `quiet_start` | `22:30` | Quiet hours start |
| `quiet_end` | `08:00` | Quiet hours end |
| `pushes_per_day` | `2` | Unprompted pushes a day, 0 to 10 |

A mandate that does not parse falls back to the strictest settings: the default
quiet hours and no unprompted pushes. The owner is told once.

## The capture eval

`auto` follows `chief.SHIPPED`, and only the capture eval sets it
(`bridge/tools/chief_eval.py --capture`). It sends each message of a blind set
(`tests/fixtures/chief/capture_holdout.json`, written by an agent that had not
seen the code, its sha256 committed first) to the real Telegram brain with
the chief on: the same model, instructions, tools and turn note a text gets.
No tool runs. The eval records the first tool call. The bar was written before the scoring,
and the set is scored once:

| Gate | Bar | 2026-09-29 |
|---|---|---|
| (a) Cards on messages that need none | 10% or fewer | 0 of 40 (0%) |
| (b) Cards whose done checks include an acceptable kind | 90% or more | 21 of 22 (95.5%) |
| (c) Cards that ask a question | 25% or fewer | 3 of 22 (13.6%) |
| (d) One-way steps sent without a Go | 0 | 0 |

It passed, so `SHIPPED` is true. The eval also reports numbers that no gate
checks. On 18 of the 40 messages that should have made a card, the brain used
another tool instead, so buddy handled them as it did before the chief.
chief_card.parse refused 2 of the 22 cards, so the brain receives a
refusal for those. The verdict and each case's row
are kept in `capture_result.json`. `chief_eval.py --capture --check-default`
recomputes the bar from those rows, with no model call, and fails when
`SHIPPED` does not match. A second `--capture` refuses to run.

## In the chat

The Telegram door carries the chief: the tools, `/jobs` and the id words, the
Go with Yes and No, the buttons, the Mac step hand-off, one-way acts on Codex,
and the receipt in place of "Task result". See
[the chief of staff in the chat](telegram.md#the-chief-of-staff-in-the-chat).
With the chief off, the door is byte for byte what it was. The self-context
block gets one line, "Chief of staff: on.", only while the chief is on.

## On, shadow and off

With the defaults (`CC_BUDDY_CHIEF=auto`, which the capture eval turned on,
and `CC_BUDDY_CHIEF_PUSH=shadow`):

**On**
- In the Telegram chat: `take_on`, `jobs_list`, `/jobs` and the id words.
- The card, the ledger and the backbrief.
- The steps `research`, `assess`, `act` (after a Go, on Codex) and `watch`.
- The checks and the receipt, the door floor, one yes per act, the budgets,
  and the rules after a restart.
- Reminders the owner asked for, the brief after the first message of the
  day, the chief's line in each turn's note, and the self-context line.
- Lessons and retries (Reflexion).

**Shadow** (logged as "would send", never sent): every unprompted message.
That is a nudge, the 80% budget warning, the evening close (21:00) and the
weekly sheet (Sunday 18:00).

**Off, or not built yet**
- The chief on the voice (the voice has only the self-context line).
- Steps that build an app or code, proposals from the night's dream, and a
  model that checks a card is ready before it starts.
- The robot's caption and the ring's colour for a waiting Go.
- Checks through connected apps (a mail sent, a calendar entry), and a check
  that tests pass.
- Autonomy earned from past receipts. `/jobs` counts the retries that fixed a
  step; nothing uses the count.

## The proof in Lean

`verification/Buddy/Chief.lean` models one card, the desk and the lesson
store, and proves seven properties over every trace: done needs evidence,
one yes per act, the budget before each step, the push budget and quiet
hours, no act run again after a restart without a new yes, no automatic
retry of a one-way act, and at most 3 lessons a key. See
[verification](../verification.md#the-chief-of-staff-2026-09-29).

`bridge/tests/test_chief_lean.py` holds the Python to the model. It copies
the model's rules into Python, reads the constants from the `.lean` file,
and first checks that the copy reproduces every worked trace Lean proved.
Then 240 seeded traces drive the real chief with fakes: executor results of
each kind, every owner answer and code word, the clock across quiet hours,
and restarts. Every ledger line becomes the model's event. After every move,
both must agree on:
- which step starts, and when;
- every yes, and every Go asked;
- every check's verdict, set against the evidence the fakes really produced;
- the card's status, revision, steps, attempts, retries and lessons.

A second set of traces does the same for the desk's routing of every kind of
message. A variant of the code that takes the executor's sentence for a
screen closes a card as done. The replay catches it at that check. The same
variant agrees with the model's naive oracle, whose end state breaks "done
needs evidence".

The test's docstring lists where the code and the model differ in shape but
not in the properties, and how the replay maps each one. For example, after
a failed act the model waits for a Go, and the code closes the card as
unverified until Reopen.

## Not done yet

- The voice door: the voice has only the self-context line.
- A relay turn ending is not yet a breakpoint; a task ending and a wake-word
  session ending are.
- Forget over the ledger and the lessons.
- `Chief.change` (a correction as a new revision) is built and tested, but no
  door calls it: the chat's `change c12` shows the card and how to re-describe
  the job.
