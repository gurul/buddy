# Gates: the chief of staff (cards, proof, attention, Reflexion, Lean)

OWNS: bridge/src/cc_buddy_bridge/chief*.py, bridge/src/cc_buddy_bridge/{telegram,daemon,self_context,spend,memory,search_router}.py, bridge/tests/test_chief*.py, bridge/tests/test_telegram_chief.py, bridge/tests/fixtures/chief/**, bridge/tools/chief_eval.py, bridge/tools/check_telegram_docs.py, verification/Buddy/Chief.lean, verification/Buddy.lean, docs/**, README.md, GATES.md

Scope: the chief design of 2026-09-29 (intent-first) and its build addendum, phases P1-P4, P8 and the E1 capture eval: cards and their ledger, the oracles and receipts, the attention desk, the chief loop with fake-tested executors, Reflexion lessons and retries, the Telegram wiring, the Lean model with its replay, and the blind capture eval that set `chief.SHIPPED`. P5 (build steps), P6 (dream proposals) and P7 (the readiness critic) are out of scope, so their gates G5.x-G7.2 are absent. The previous ledger (Meet) is in git at origin/main. The bridge has no type-checker (pyproject configures ruff and pytest only). Every CHECK below ran with the owner's `CC_BUDDY_*` and `*_API_KEY` variables unset; EVIDENCE is the exit code and the last line of output.

## P1 Record

- [x] G1.1: One bad phase refuses the whole card; "order the picked desk" is one-way and "compare desks" two-way; the door is add-only; a failed os.replace keeps the old file; an unreadable file moves to .bad-<ts>; ids are never reused; the ledger is in a temp folder under test (positive control: unset, it is ~/.config/cc-buddy-bridge/chief).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_record.py && echo CHIEF_RECORD_OK
  CWD: bridge
  EXPECT: CHIEF_RECORD_OK
  EVIDENCE: exit=0; output=56 passed in 0.23s | CHIEF_RECORD_OK

- [x] G1.2: Forget's tests still pass with the chief ledger named under not_covered.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_memory.py && echo MEMORY_OK
  CWD: bridge
  EXPECT: MEMORY_OK
  EVIDENCE: exit=0; output=31 passed in 1.38s | MEMORY_OK

- [x] G1.3: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; output=All checks passed! | RUFF_CLEAN

## P2 Proof and attention

- [x] G2.1: Every done kind has a confirming and a non-confirming fixture; the executor's sentence alone is unverifiable; one unverifiable check makes the card unverified, never done.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_receipt.py && echo CHIEF_RECEIPT_OK
  CWD: bridge
  EXPECT: CHIEF_RECEIPT_OK
  EVIDENCE: exit=0; output=22 passed in 0.04s | CHIEF_RECEIPT_OK

- [x] G2.2: 23:00 batches and 10:00 sends; the third push of a day is batched; backoff doubles; the owner's own action cancels queued nudges; an expired decision takes the option that does not act; the push count survives a reload; no breakpoint in 45 min moves the item on; any error batches.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_desk.py && echo CHIEF_DESK_OK
  CWD: bridge
  EXPECT: CHIEF_DESK_OK
  EVIDENCE: exit=0; output=25 passed in 0.10s | CHIEF_DESK_OK

- [x] G2.3: docs/stackchan/chief.md names every CC_BUDDY_CHIEF* setting the code reads, and its documented defaults equal the constants.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_desk.py -k docs && echo CHIEF_DOCS_OK
  CWD: bridge
  EXPECT: CHIEF_DOCS_OK
  EVIDENCE: exit=0; output=3 passed, 22 deselected in 0.09s | CHIEF_DOCS_OK

## P3 The chief, with fake executors

- [x] G3.1: A whole job runs (take_on, backbrief, research, assess, Go yes, act, receipt done); a Go timeout and an exhausted budget each make the card wait with 0 dispatches; a restart after an act dispatch says "I may have done" with 0 new dispatches; research runs again after a restart; "buy it now" on a page adds no phase; a one-way act asks for floor="codex"; a busy slot queues the phase; plus every reviewer attack after P3 and P4.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief.py && echo CHIEF_LOOP_OK
  CWD: bridge
  EXPECT: CHIEF_LOOP_OK
  EVIDENCE: exit=0; output=66 passed in 0.62s | CHIEF_LOOP_OK

- [x] G3.2: Tool enums and instructions hold only live names (positive control: a planted non-live name is caught); a sentinel word from the owner's message reaches no model payload, spend row, event, attention line or log line.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief.py -k "names or privacy" && echo CHIEF_GUARDS_OK
  CWD: bridge
  EXPECT: CHIEF_GUARDS_OK
  EVIDENCE: exit=0; output=5 passed, 61 deselected in 0.06s | CHIEF_GUARDS_OK

- [x] G3.3: Spend rows written inside spend.job("c7") carry the tag, rows outside do not, and asyncio.to_thread keeps it.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_spend.py && echo SPEND_OK
  CWD: bridge
  EXPECT: SPEND_OK
  EVIDENCE: exit=0; output=30 passed in 0.12s | SPEND_OK

- [x] GR: Reflexion (addendum): a lesson is written only on a failed or unverifiable check, a failed or over-budget result, or a looping executor, and at most 3 are read per key; a retry carries the lessons in the delimited notes field; a one-way act never retries by itself; a reflection error is no lesson and no retry; injected executor text cannot change the next goal beyond the notes field.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_reflect.py && echo CHIEF_REFLECT_OK
  CWD: bridge
  EXPECT: CHIEF_REFLECT_OK
  EVIDENCE: exit=0; output=17 passed in 0.18s | CHIEF_REFLECT_OK

- [ ] G3.4: FULL: the whole bridge suite, then ruff.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=1; output=4 failed, 4027 passed, 13 skipped in 556.30s (0:09:16), after the fix (no PYTEST_OK); ruff then: All checks passed! | RUFF_CLEAN. The 4: tests/test_ears.py::test_real_model_hears_hey_buddy_and_ignores_a_control_sentence, ::test_real_model_custom_phrase, ::test_real_model_through_ears_fanout (ImportError: dlopen, the wake-word model's native library; also fails on origin/main), tests/test_voice_agent.py::test_while_listening_only_goodbye_and_mute_act_and_nothing_is_starred (also fails on origin/main). None touches the chief; G3.4b is the same run without them.

- [x] G3.4b: FULL without the tests that also fail on untouched origin/main or are known flakes that do not touch the chief (3 x test_ears real-model dlopen, the codex-computer daemon factory env test, the voice-agent listening test, the daemon-lesson caption test).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py --deselect tests/test_ears.py::test_real_model_hears_hey_buddy_and_ignores_a_control_sentence --deselect tests/test_ears.py::test_real_model_custom_phrase --deselect tests/test_ears.py::test_real_model_through_ears_fanout --deselect tests/test_codex_computer.py::test_daemon_factory_routes_shared_task_contract_to_codex --deselect tests/test_voice_agent.py::test_while_listening_only_goodbye_and_mute_act_and_nothing_is_starred --deselect tests/test_daemon_lesson.py::test_a_dispatch_notice_is_the_only_caption && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; output=4024 passed, 13 skipped, 6 deselected, 1 xfailed in 554.76s (0:09:14) | PYTEST_OK (before the fix; after it the undeselected run above has 0 new failures)

## P4 Wiring

- [x] G4.1: Off: take_on is absent and a stray call gets the OFF reason, never think_hard; take_on makes no second model call; /jobs and the id words make 0 model calls; turn_context carries the chief's line; a Mac step queues while a task runs or the desk has the Mac; "go c12" approves only c12's pending act at its revision; with Holo as the floor the approved act is built on codex; the self_context block stays under BUDGET with everything on.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram_chief.py tests/test_self_context.py && echo TG_CHIEF_OK
  CWD: bridge
  EXPECT: TG_CHIEF_OK
  EVIDENCE: exit=0; output=37 passed in 0.42s | TG_CHIEF_OK

- [x] G4.2: telegram.md documents the door's chief (FULL is G3.4 above).
  CHECK: .venv/bin/python tools/check_telegram_docs.py
  CWD: bridge
  EXPECT: TELEGRAM_DOCS_OK
  EVIDENCE: exit=0; output=18 names documented: CC_BUDDY_COMMAND_RISK, CC_BUDDY_COMPOSIO, CC_BUDDY_COMPOSIO_POLICY, CC_BUDDY_COMPOSIO_STATE, CC_BUDDY_COMPOSIO_TIMEOUT_SECS, CC_BUDDY_SECOND_BRAIN, CC_BUDDY_TELEGRAM, CC_BUDDY_TELEGRAM_ASK, CC_BUDDY_TELEGRAM_DRAFTS, CC_BUDDY_TELEGRAM_EFFORT, CC_BUDDY_TELEGRAM_MODEL, CC_BUDDY_TELEGRAM_OWNER, CC_BUDDY_TELEGRAM_TOKEN, CC_BUDDY_VAULT, CC_BUDDY_WEB_SEARCH, CC_BUDDY_WEB_SEARCH_MAX_USES, CC_BUDDY_WEB_SEARCH_MODEL, CC_BUDDY_WEB_SEARCH_RESULTS | TELEGRAM_DOCS_OK

- [ ] G4.3: MANUAL, owner live, with the chief on: one research-only card runs end to end; one one-way card stops at the Go and No leaves nothing done; one approved act runs on Codex while Holo is the floor. The owner says what he saw on the phone.
  WHY MANUAL: it needs the owner's own Telegram, his Mac with Holo as the live floor, Codex signed in, and his taps and eyes on the phone; no command can stand in for "what the owner saw". Not run: the owner is asleep and the daemon was not restarted onto this branch.
  EVIDENCE: pending (owner)

- [ ] G4.4: MANUAL, owner live: wall time per E7 from the logs, 10 card turns against 10 start_task turns: p50 overhead <= 300 ms and the backbrief <= 1.5 s after the tool call. The push shadow week starts when P4 lands.
  WHY MANUAL: it needs 20 real turns from the owner on the live daemon, which is not running this branch.
  EVIDENCE: pending (owner)

## E1 The capture eval (the G7.3 check for chief.SHIPPED)

- [x] G7.3: chief.SHIPPED equals the pre-registered E1 bar recomputed from the kept per-case rows of the single blind scoring (no model call); the set's sha256 matches the committed one.
  CHECK: .venv/bin/python tools/chief_eval.py --capture --check-default && echo CAPTURE_EVAL_OK
  CWD: bridge
  EXPECT: CAPTURE_EVAL_OK
  EVIDENCE: exit=0; output=DEFAULT_CONSISTENT | CAPTURE_EVAL_OK

- [x] G7.3b: the eval tool's own tests (the bar, the scoring rules, the one-scoring lock, the sha256 refusal).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_eval.py && echo CHIEF_EVAL_TESTS_OK
  CWD: bridge
  EXPECT: CHIEF_EVAL_TESTS_OK
  EVIDENCE: exit=0; output=14 passed in 0.05s | CHIEF_EVAL_TESTS_OK

## P8 Proof

- [x] G8.1: Chief.lean's code_invariant is kernel-checked, with standard axioms only.
  CHECK: node verification/check.mjs Buddy/Chief.lean Buddy.Chief.code_invariant
  CWD: .
  EXPECT: LEAN_CHECK_OK
  EVIDENCE: exit=0; output=LEAN_CHECK_OK Buddy/Chief.lean (1 theorems)

- [x] G8.2: Every Lean model and theorem checks.
  CHECK: cd verification && node check-all.mjs | tail -1
  CWD: .
  EXPECT: ALL_MODELS_OK
  EVIDENCE: exit=0; output=ALL_MODELS_OK 290 theorems

- [x] G8.3: The Python chief agrees with Chief.lean's rules on 240 generated traces and 36 desk traces; the transliteration reproduces every Lean worked trace; positive control: a variant that closes a card on the executor's word disagrees (and agrees with the naive oracle instead).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chief_lean.py && echo CHIEF_LEAN_REPLAY_OK
  CWD: bridge
  EXPECT: CHIEF_LEAN_REPLAY_OK
  EVIDENCE: exit=0; output=287 passed (the closed-then-reopened xfail fixed and passing) | CHIEF_LEAN_REPLAY_OK

## Before any push

- [x] GP: No email, long digit id, token-shaped string or the owner's name in the branch's diff against origin/main or its untracked files, apart from the reviewed allowlist (the check-kind identifiers guru_says_done and needs_guru from the design's closed sets, and test_chief_desk.py's own reserved-domain positive control and name guard). GATES.md is left out: it holds this gate's own planted strings. Positive control: the same pattern finds all 4 of them.
  CHECK: P='[A-Za-z0-9._%-][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b[0-9]{9,}\b|(sk|ghp|gho|xox[bp])[-_][A-Za-z0-9]{10,}|[Gg]uru|[Ll]ingamallu'; c=$(printf 'mail: someone.else@mail.example.com\nid 12345678901\nkey sk-abcdefghijk1\nGuru\n' | grep -cE "$P"); n=$({ git diff origin/main -- . ':!GATES.md'; git ls-files -z --others --exclude-standard | xargs -0 cat; } | sed -E 's/guru_says_done|needs_guru|someone@example\.org|"Guru" not in text//g' | grep -cE "$P"); echo "control=$c hits=$n"; [ "$c" = 4 ] && [ "$n" = 0 ] && echo DIFF_CLEAN
  CWD: .
  EXPECT: DIFF_CLEAN
  EVIDENCE: exit=0; output=control=4 hits=0 | DIFF_CLEAN
