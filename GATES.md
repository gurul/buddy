# Gates: plan once with astra, execute with Jev; the voice gate; the card's power pill

OWNS: bridge/src/cc_buddy_bridge/typed_ask.py, bridge/src/cc_buddy_bridge/fast_lane.py, bridge/src/cc_buddy_bridge/plan_contract.py, bridge/src/cc_buddy_bridge/plan_executor.py, bridge/src/cc_buddy_bridge/computer_agent.py, bridge/src/cc_buddy_bridge/desktop_worker.py, bridge/src/cc_buddy_bridge/desktop_helpers.py, bridge/src/cc_buddy_bridge/ax_candidates.py, bridge/src/cc_buddy_bridge/jev.py, bridge/src/cc_buddy_bridge/voice_gate.py, bridge/src/cc_buddy_bridge/ears.py, bridge/src/cc_buddy_bridge/voice_agent.py, bridge/src/cc_buddy_bridge/daemon.py, bridge/tools/jev_step_eval.py, bridge/tools/plan_live.py, bridge/tools/voice_gate_eval.py, bridge/tests/test_jev_step.py, bridge/tests/test_plan_executor.py, bridge/tests/test_agent_plan_once.py, bridge/tests/test_voice_gate.py, bridge/tests/test_desktop_worker.py, bridge/tests/test_ax_candidates.py, bridge/tests/fixtures/jev_step_answers.json, widget/Shared/BuddyPower.swift, widget/StackChanNotes/PowerRelay.swift, widget/StackChanNotes/BuddyService.swift, widget/StackChanNotesWidget/StackChanNotesWidget.swift, docs/stackchan/routing.md, docs/stackchan/voice.md, docs/stackchan/widget.md, README.md, GATES.md

Scope: (1) A request that reaches the planner can be planned ONCE by astra and executed with no planner turn between steps or at the end, every click grounded on a fresh Accessibility snapshot by the keyword gate and Jev asked in its own idiom; consequential steps stop for the human and nothing in a plan can approve one. (2) In a wake-word conversation only the person who woke buddy reaches the live model; notes (think-aloud) are never gated; the gate fails open. (3) The desktop card's power pill always shows the result of a press and never drops one. Everything new ships OFF behind an env switch, because no unread evaluation set exists yet for any of it. The previous ledger (lane-first routing) is in git at 983ed5f. Pytest gates use `&& echo …_OK` so the exit code decides; tests are selected by file or node id, never by -k. No type-checker is configured for the bridge (pyproject has ruff and pytest only).

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=1577 passed, 1 skipped in 67.14s (0:01:07) | PYTEST_OK

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=All checks passed! | RUFF_CLEAN

- [x] G3: Jev is asked one request per step — a target choice with an explicit "none" plus three absolute nouls — the control list is in the STATE so the nouls can see it, and a failing or malformed answer presses nothing.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_jev_step.py::test_one_request_carries_the_choice_with_none_and_three_nouls tests/test_jev_step.py::test_the_nouls_can_see_the_controls_because_the_state_lists_them tests/test_jev_step.py::test_a_failing_or_malformed_answer_abstains tests/test_jev_step.py::test_gates_press_only_when_the_absolute_and_the_relative_both_clear && echo JEV_ASK_OK
  CWD: bridge
  EXPECT: JEV_ASK_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.01s | JEV_ASK_OK

- [x] G4: In the lane's "jev" mode the keyword gate proposes and Jev can refuse: an agreed pick is pressed, a pick Jev does not share is not, a weak or failed answer presses nothing, a risky step confirms with zero input, and a sensitive control is never offered to Jev at all.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_jev_step.py::test_jev_mode_presses_the_gate_pick_when_jev_agrees tests/test_jev_step.py::test_jev_mode_refuses_a_gate_pick_jev_does_not_share tests/test_jev_step.py::test_jev_mode_decides_a_step_the_gate_cannot tests/test_jev_step.py::test_jev_mode_presses_nothing_on_a_weak_or_failed_answer tests/test_jev_step.py::test_jev_mode_a_risky_step_confirms_and_clicks_nothing tests/test_jev_step.py::test_jev_mode_never_offers_a_sensitive_control_and_the_code_gate_still_confirms && echo JEV_LANE_OK
  CWD: bridge
  EXPECT: JEV_LANE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=6 passed in 0.01s | JEV_LANE_OK

- [x] G5: The recorded Jev answers (155 cases, asked over OpenRouter 2026-09-21) replay offline to the numbers the docs state — on holdout the gate alone presses 5 wrong and the jev-mode path 1 wrong at higher coverage — and the tool refuses to enable the default without an unread set. The positive control for "refuses": the same replay prints the ship decision as keep off AND the check passes only because JEV_STEP_DEFAULT is False.
  CHECK: .venv/bin/python tools/jev_step_eval.py --fixtures tests/fixtures/ax --replay tests/fixtures/jev_step_answers.json --check-default | awk '/^holdout/{h=1} h&&/keyword gate alone/&&/wrong +5 /{g=1} h&&/gate only if jev agrees/&&/wrong +1 /{v=1} /ship decision: keep off/{k=1} /^JEV_STEP_DEFAULT_OK/{d=1} END{if(g&&v&&k&&d)print "JEV_EVAL_REPLAY_OK"; else {print "gate5="g" veto1="v" keepoff="k" default="d; exit 1}}'
  CWD: bridge
  EXPECT: JEV_EVAL_REPLAY_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=JEV_EVAL_REPLAY_OK

- [x] G6: The sensitive-label table knows the verbs a plan executor can reach (Place Order, Confirm, Publish, Transfer, Clear History, Format Disk, Turn Off, Add to Cart, Kill …) without firing on Bookmarks, Forward, Format or Sort Order, and the router eval it feeds is unchanged (23 of 23).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ax_candidates.py::test_sensitive_label_table_positives tests/test_ax_candidates.py::test_sensitive_label_table_negatives && .venv/bin/python tools/fastlane_eval.py --fixtures tests/fixtures/ax --router | grep -q "right 23: precision 100.0%" && echo SENSITIVE_OK
  CWD: bridge
  EXPECT: SENSITIVE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.01s | SENSITIVE_OK

- [x] G7: A plan the code cannot fully read is never run; whether text was the human's is checked against the request, not taken from the plan; the planner is shown labels and roles, never the window title.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_plan_executor.py::test_a_plan_the_code_cannot_fully_read_is_not_a_plan tests/test_plan_executor.py::test_where_text_came_from_is_checked_not_trusted tests/test_plan_executor.py::test_the_planner_is_shown_labels_and_roles_never_the_title tests/test_plan_executor.py::test_the_planner_can_decline && echo PLAN_CONTRACT_OK
  CWD: bridge
  EXPECT: PLAN_CONTRACT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.02s | PLAN_CONTRACT_OK

- [x] G8: The executor walks a plan step by step, hands back with zero input when no control answers, stops on a click that changed nothing or an expectation that does not hold, never reports an unconfirmed step as plain success, and brings the planned app back to the front first.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_plan_executor.py::test_a_two_step_plan_runs_with_no_model_when_the_labels_are_exact tests/test_plan_executor.py::test_a_step_no_control_answers_hands_back_with_zero_input tests/test_plan_executor.py::test_jev_refusing_the_gates_pick_presses_nothing tests/test_plan_executor.py::test_a_click_that_changes_nothing_stops_the_plan tests/test_plan_executor.py::test_an_unconfirmed_step_is_never_reported_as_plain_success tests/test_plan_executor.py::test_an_expectation_that_does_not_hold_stops_the_plan tests/test_plan_executor.py::test_the_app_the_plan_was_written_for_is_brought_back_before_the_first_step tests/test_plan_executor.py::test_a_dry_run_touches_nothing && echo EXECUTOR_OK
  CWD: bridge
  EXPECT: EXECUTOR_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=8 passed in 0.02s | EXECUTOR_OK

- [x] G9: Only the human approves: the plan's flag, a sensitive control, Jev's risky answer, a Return outside a dictated search and composed text into a shell all stop with zero input for that step, a yes lets exactly that through once, and a secure field or a dialog is never typed into.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_plan_executor.py::test_the_plans_own_flag_stops_before_any_input_and_resumes_on_a_yes tests/test_plan_executor.py::test_a_sensitive_control_needs_the_human_whatever_the_plan_says tests/test_plan_executor.py::test_jevs_risky_answer_stops_a_step_the_code_table_does_not_know tests/test_plan_executor.py::test_a_dictated_search_may_be_submitted tests/test_plan_executor.py::test_composed_text_is_typed_but_never_submitted_without_a_yes tests/test_plan_executor.py::test_composed_text_into_a_shell_asks_before_the_first_key tests/test_plan_executor.py::test_a_secure_field_or_a_dialog_is_never_typed_into && echo HUMAN_APPROVES_OK
  CWD: bridge
  EXPECT: HUMAN_APPROVES_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=7 passed in 0.02s | HUMAN_APPROVES_OK

- [x] G10: With CC_BUDDY_PLAN_EXEC on, a plan that runs costs exactly one planner call and none at the end (the fake client raises on a second request: that is the positive control), a consequential step asks the human with no planner turn, a no stops everything, a partial plan hands the loop what was done, and no plan or a bad plan is today's loop exactly. It ships off.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_plan_once.py && echo PLAN_ONCE_OK
  CWD: bridge
  EXPECT: PLAN_ONCE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=8 passed in 0.03s | PLAN_ONCE_OK

- [x] G11: The worker serves outline and run_plan through the real helpers, and the Jev asker exists only when the owner's switches and a route key say so.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_desktop_worker.py::test_outline_and_run_plan_run_through_the_real_helpers tests/test_desktop_worker.py::test_the_jev_asker_is_configured_only_by_the_owners_switches && echo WORKER_OK
  CWD: bridge
  EXPECT: WORKER_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.02s | WORKER_OK

- [x] G12: The voice gate forwards its owner whole and in order, turns another voice into silence of the same length (positive control: the same audio with the gate off is forwarded), changes nothing in shadow mode, never gates notes, never holds back an awaited short answer, and fails open on every way it can fail to judge.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_voice_gate.py && echo VOICE_GATE_OK
  CWD: bridge
  EXPECT: VOICE_GATE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=19 passed in 0.04s | VOICE_GATE_OK

- [x] G13: The voice session and the wake-word path behave exactly as before when the gate is off (their whole suites pass unchanged).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_voice_agent.py tests/test_ears.py && echo VOICE_UNCHANGED_OK
  CWD: bridge
  EXPECT: VOICE_UNCHANGED_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=99 passed in 27.29s | VOICE_UNCHANGED_OK

- [x] G14: The docs say what the code does: routing.md has the plan-once section, its knobs and the four repos' credits; voice.md has the voice gate and its knob; README names the modules; every new switch's shipped default is off.
  CHECK: grep -q "## Plan once, execute with Jev" docs/stackchan/routing.md && grep -q "CC_BUDDY_PLAN_EXEC" docs/stackchan/routing.md && grep -q "savka777/jev-use" docs/stackchan/routing.md && grep -q "trycua/cua" docs/stackchan/routing.md && grep -q "## The voice gate" docs/stackchan/voice.md && grep -q "CC_BUDDY_VOICE_GATE" docs/stackchan/voice.md && grep -q "plan_executor.py" README.md && grep -q "answerWait" docs/stackchan/widget.md && bridge/.venv/bin/python -c "import sys; sys.path.insert(0,'bridge/src'); from cc_buddy_bridge import computer_agent as c, typed_ask as t, voice_gate as v, fast_lane as f; assert c.PLAN_EXEC_DEFAULT is False and t.JEV_STEP_DEFAULT is False and v.DEFAULT_MODE=='off' and f.DEFAULT_DECIDE=='keyword'; print('DOCS_AND_DEFAULTS_OK')"
  EXPECT: DOCS_AND_DEFAULTS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=574d30059456/19 entries; output=DOCS_AND_DEFAULTS_OK

- [x] G15: MANUAL — plan once, execute with Jev, live on this Mac (tools/plan_live.py --act, Calendar, 2026-09-21): "put the calendar on the year view, then go to next year" ran with ONE planner call (3.72 s) and an executor pass of 2.60 s, both clicks `confirmed` via keyword+jev, 6.64 s in all against 13.1 s turn by turn; the re-fronting fix fired live in that run. Three runs on one app are a demonstration, not a measurement.
  EVIDENCE: manual; the four runs and their timings are in docs/stackchan/routing.md ("Live, 2026-09-21") and in this session's transcript.

- [x] G16: MANUAL — the widget's power path, live: the rebuilt helper (xcodebuild BUILD SUCCEEDED, installed to ~/Applications) served an off then an on request written exactly as the new card writes them, each answered with a state stamped at or after the request; a request stamped in the same second as the state was reproduced as DROPPED by the helper before the card-side fix. The pill's own click was not driven: a real press on the desktop card is the owner's to make.
  EVIDENCE: manual; power.json after the round trip read {"at":"2026-09-21T19:59:51Z","on":true,…} against a request at 19:59:51Z, daemon pid 99694.

- [ ] G17: A ship decision for each new switch on an evaluation set nobody has read: Jev's click grounding (tools/jev_step_eval.py --fresh DIR --check-default), plan-once against the turn-by-turn loop and against plan-once with the keyword gate alone on tasks with a code oracle, and the voice gate on recordings from the owner's own room (tools/voice_gate_eval.py --check).

ABANDON: G17 No unread evaluation set exists for any of the three, and one cannot be authored honestly inside the session that wrote the code: the Accessibility fixtures were read during the laya work, the task A/B needs the owner's desk, and the voice gate needs the owner's voice and room. Every new default therefore ships OFF, jev_step_eval.py refuses ENABLE without --fresh, and the docs label every reported number a tuning-set number.
