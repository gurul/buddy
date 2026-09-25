# Formal verification (Lean 4)

Six of buddy's state machines are modelled in Lean 4 under `verification/`. For each one
there are two kernel-checked theorems:

- **`current_violates`** — a concrete trace on a model of the code *as it was* on
  2026-09-25 (commit `0b7d36d`) after which the property is false: the bug, proved.
- **`fixed_invariant`** — the property holds on a model of the fixed code for **every**
  trace (induction over an arbitrary event list), not just the counterexample.

Every fix also has a pytest in `bridge/tests/` that replays the counterexample against the
real Python classes. Each replay was run before the fix and failed; it passes now.

## The six models

| Model | Code | Property | What the counterexample was |
|---|---|---|---|
| `Buddy/Command.lean` | `matchers.py` `classify_command` | "allow" answers only one simple command whose program cannot run another command or write a file — for **every** `matchers.toml` | The built-in `^echo( |$)` allowed `echo x; rm -rf ~`. Also `ls && curl … \| sh`, `ls $(…)`, `env rm -rf /`, `find -execdir`, `fd -x`, `rg --pre`, `tree -o`, `git log --output`. The daemon turned that "allow" into an explicit allow before Jev or the phone saw it. |
| `Buddy/Questions.lean` | `telegram.py` `_ask_user` / `decide_permission` | while any question waits, the typed-answer slot names the newest one still waiting | Three nested questions A, B, C; B then C end; the slot empties while A still waits, so a typed yes for A went to Claude. |
| `Buddy/TaskStop.lean` | `telegram.py` `_run_agent` / `_stop` | a task that completed is never reported "Stopped.", and its result is sent | A typed "stop" after the agent returned (while the progress message closed) said "Stopped." and ate the result. |
| `Buddy/Forget.lean` | `memory.py` `forget_apply`, `records.py`, `dream.py`, `mem0_memory.py` | once forget completes, no interleaving with the dream or the index ingest puts a forgotten line back in the records, git or the index | A forget during the dream's model call was undone by the dream's write, and its commit put the words back into the history the forget had squashed. Same for the mem0 ingest. |
| `Buddy/Serial.lean` | `serial_transport.py`, `daemon.py` watchdog and ack router, `folder_push.py` | (A) at most one write on the port at a time; (B) a flapping link reaches the RTS reset; (C) every ack reaches its own request's waiter | (A) a sender cancelled by `wait_for` freed the lock while its thread still wrote; (B) the reconnect's status ack counted as a clean poll, so a link that flapped never escalated to the RTS reset; (C) an ack that arrived before its waiter registered was dropped, and a late chunk ack answered the next chunk. |
| `Buddy/Voice.lean` | `voice_agent.py` `_backend_event`, `_ask_user` | once nothing is in flight the face is not stuck on thinking/speaking; a question slot is always released | A backend stream `error` left the response "active" forever: the face held "thinking" and every later reply was held back. A failed send of a spoken question left the slot set, blocking the idle close and the goodbye. |

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
