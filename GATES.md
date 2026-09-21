# Gates: computer-use fast lane (laya-mlx under gpt-6-astra) — v2

OWNS: bridge/src/cc_buddy_bridge/ax_candidates.py, bridge/src/cc_buddy_bridge/decider.py, bridge/src/cc_buddy_bridge/fast_lane.py, bridge/src/cc_buddy_bridge/desktop_helpers.py, bridge/src/cc_buddy_bridge/desktop_worker.py, bridge/src/cc_buddy_bridge/computer_agent.py, bridge/src/cc_buddy_bridge/update.py, bridge/pyproject.toml, bridge/tools/**, bridge/tests/**, docs/stackchan/voice.md, docs/stackchan/slither.md, README.md, bridge/README.md

Scope: A local typed-decision fast lane (accessibility-tree candidates ranked by laya-mlx, every safety judgement in code) that gpt-6-astra can delegate narrow in-app click runs to, a shadow-only local verifier, per-step local timing in the run log, and an offline holdout eval that decides whether the lane ships enabled. Pytest gates use `&& echo PYTEST_OK` so the exit code decides; new tests are selected by node id, never by -k.

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=1436 passed, 3 skipped in 61.88s PYTEST_OK (2026-09-21, whole bridge incl. slither files)

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=All checks passed! RUFF_CLEAN (src/ tests/ tools/)

- [x] G3: The real model loads from the default checkpoint path (a real directory under ~/.config, not a symlink into another repo), warms up, and answers a 4-option Calendar menu with a valid id in under 200 ms warm.
  CHECK: .venv/bin/python -c "import os; from cc_buddy_bridge.decider import Decider, DEFAULT_MODEL_PATH; p=os.path.expanduser(DEFAULT_MODEL_PATH); assert not os.path.islink(p) and os.path.realpath(p).startswith(os.path.expanduser('~/.config')), p; d=Decider.load(p); c=d.choose('switch to week view', app='Calendar', context='title: September 2026', options={'1':'click radio button: Week','2':'click button: Today','reobserve':'the screen is still changing, look again','abstain':'none of these advances the objective'}); assert c.id and c.ms < 200 and not c.error, c; print('LAYA_READY', c.id, round(c.ms,1), 'load', round(d.load_ms), 'warm', round(d.warm_ms))"
  CWD: bridge
  EXPECT: LAYA_READY
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=LAYA_READY 2 22.4 load 465 warm 1531 (leaf B, 2026-09-21 02:0x; checkpoint is a real directory under ~/.config)

- [x] G4: Live accessibility snapshots of a Calendar window, a System Settings window and a Safari page each complete untruncated within their app budget with at least 5 labelled pressable candidates, and a Safari walk capped at 800 nodes reports truncated=true (positive control for the truncation path). Precondition: a window of each app is open.
  CHECK: .venv/bin/python tools/fastlane_eval.py --live-snapshot Calendar --min-pressable 5 --max-ms 300 --require-complete && .venv/bin/python tools/fastlane_eval.py --live-snapshot "System Settings" --min-pressable 5 --max-ms 600 --require-complete && .venv/bin/python tools/fastlane_eval.py --live-snapshot Safari --min-pressable 5 --max-ms 2500 --require-complete && .venv/bin/python tools/fastlane_eval.py --live-snapshot Safari --max-nodes 800 --expect-truncated && echo LIVE_SNAPSHOTS_OK
  CWD: bridge
  EXPECT: LIVE_SNAPSHOTS_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=Calendar 124 nodes 0.174 s pressable 59 | System Settings 195 nodes 0.178 s pressable 50 | Safari 3632 nodes 0.887 s pressable 79 untruncated | Safari --max-nodes 800 truncated=True 0.218 s | LIVE_SNAPSHOTS_OK (2026-09-21; the first walk after a page load is truncated at 4000 nodes/1.1 s and settles on the second)

- [x] G5: Every committed fixture re-derives byte-equal from its raw walk dump with the current code and is untruncated; the select set has ≥ 30 cases and the holdout set ≥ 40 cases across ≥ 5 apps with ≥ 12 overlap:false and ≥ 6 distractor cases; the eval harness runs every style over both sets with the real model, prints the keyword baseline, the accuracy-vs-coverage table, the k-bucket table, the Wilson interval, the truncation counts and the cost-weighted score, and names the winner and thresholds. The harness itself is unit-tested with a fake predictor.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_fixtures_ax.py tests/test_fastlane_eval.py && .venv/bin/python tools/fastlane_eval.py --fixtures tests/fixtures/ax --min-select 30 --min-holdout 40 --min-apps 5 --min-no-overlap 12 --min-distractor 6 && echo EVAL_COMPLETE
  CWD: bridge
  EXPECT: EVAL_COMPLETE
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=48 passed | select 73 cases/5 apps, holdout 82 cases/7 apps, 34 overlap:false, 20 distractor; keyword baseline, gate usage, abstain-expected and overlap:false(click-expected) subsets, coverage table, k buckets, Wilson, truncation counts, ms, cost per style×set; WINNER style=hinted p_min=0.60 margin_min=0.15; holdout gated top-1 72.3%, coverage 79.3%, overlap:false(click) real 0.0% vs keyword 0.0% (n=9), abstain-expected real 50% vs keyword 90% (n=10), cost +0.61 s/case; SHIP DECISION: disabled; EVAL_COMPLETE (rerun after the review's criterion fix, 2026-09-21; full text scratchpad/g5-final.txt)

- [x] G6: The shipped default for CC_BUDDY_FAST_LANE and the shipped Thresholds match the holdout outcome: enabled only if gated top-1 ≥ 0.80 AND coverage ≥ 0.70 AND real ≥ keyword + 0.10 on overlap:false AND cost-weighted score > 0 at the shipped thresholds; and instructions(False) contains no "delegate" token while instructions(True) documents it.
  CHECK: .venv/bin/python tools/fastlane_eval.py --fixtures tests/fixtures/ax --check-default && .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_computer_agent.py::test_instructions_variants_track_fast_lane_default && echo DEFAULT_CONSISTENT
  CWD: bridge
  EXPECT: DEFAULT_CONSISTENT
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=holdout n=82 style=hinted thresholds 0.60/0.15: gated top-1 72.3% (need 80), coverage 79.3% (need 70), overlap:false(click-expected) real 0.0% vs keyword 0.0% (need +10), cost +0.61 (need > 0); decision: disabled; shipped FAST_LANE_DEFAULT=False; DEFAULT_CONSISTENT | test_instructions_variants_track_fast_lane_default 1 passed

- [x] G7: The sensitive-label gate is fail-closed with a committed table of ≥ 12 positives (including "Delete Event", "Don't Save", "Replace", "OK", "Remove filter", "Export as PDF", and a 90-char label whose sensitive word sits after character 60) and ≥ 12 boundary-safe negatives; and the helper list, exec_py text and instructions agree in both fast-lane variants.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ax_candidates.py::test_sensitive_label_table_positives tests/test_ax_candidates.py::test_sensitive_label_table_negatives tests/test_computer_agent.py::test_instructions_tool_text_and_helper_names_agree tests/test_computer_agent.py::test_instructions_variants_track_fast_lane_default && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=4 passed (sensitive table positives/negatives; helper names, exec_py text and instructions agree in both variants) PYTEST_OK

- [x] G8: The lane cannot act consequentially: with fake senses/effectors, a "Delete Event" candidate on a non-sensitive objective is never offered; a sensitive pick returns confirm with zero clicks; an "OK" button inside a sheet is never offered and the lane escalates dialog_open; a "Dark" radio in System Settings returns confirm; key="return" after typing into an AXTextArea "Message" returns confirm with zero presses while the same key after typing into a search field presses once; a hit-test miss and a focus mismatch produce zero input; the previous step's control is not re-offered after an unchanged step; approve=<label> allows exactly one sensitive click. Positive control: a "Week" radio is clicked exactly once.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_fast_lane.py::test_sensitive_candidate_not_offered tests/test_fast_lane.py::test_sensitive_pick_confirms_without_clicking tests/test_fast_lane.py::test_sheet_button_never_offered_escalates_dialog_open tests/test_fast_lane.py::test_system_settings_toggle_confirms tests/test_fast_lane.py::test_return_after_message_area_confirms tests/test_fast_lane.py::test_return_after_search_field_presses_once tests/test_fast_lane.py::test_hit_test_miss_zero_input tests/test_fast_lane.py::test_focus_mismatch_zero_paste tests/test_fast_lane.py::test_no_repeat_after_unchanged tests/test_fast_lane.py::test_approve_allows_one_sensitive_click tests/test_fast_lane.py::test_week_radio_clicked_once && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=11 passed (the eleven named node ids, incl. the Week radio positive control) PYTEST_OK

- [x] G9: The auto-screenshot after open_app no longer settles a second time (under 0.3 s with the fake clock, and the reply still carries an image), a raw click still settles up to 1.5 s, and a timed-out open_app settle still gets the auto settle (negative control).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_desktop_worker.py::test_open_app_auto_screenshot_settles_once tests/test_desktop_worker.py::test_raw_click_still_settles_up_to_1_5s tests/test_desktop_worker.py::test_timed_out_settle_still_auto_settles && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=3 passed (test_open_app_auto_screenshot_settles_once: one 1.5 s settle, clock 0.25 s, image present; raw click 1.5 s; timed-out open_app settle -> second settle, clock 3.0 s) PYTEST_OK

- [x] G10: Every execute/observe reply from a worker with helpers carries a timing dict that the agent writes to the run log; the shadow verifier is logged beside astra's verdict, adds under 50 ms to _verify's wall time with the fake clock (sequential variant as the positive control), a missing worker.verify is logged as an error without blocking, and a hung verify request never restarts the worker.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_desktop_worker.py::test_reply_carries_timing_with_helpers tests/test_computer_agent.py::test_loop_logs_worker_timing tests/test_computer_agent.py::test_shadow_verify_logged_beside_verdict tests/test_computer_agent.py::test_shadow_verify_is_concurrent tests/test_computer_agent.py::test_missing_worker_verify_is_logged_not_fatal tests/test_worker_client.py::test_verify_timeout_does_not_restart && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=6 passed (timing dict logged; local verdict beside valid; shadow wall delta < 50 ms with a 0.30 s sequential control; missing verify -> error logged; verify timeout -> {'error': 'timeout'}, restarts 0) PYTEST_OK

- [x] G11: Live, Calendar: with the Week control NOT selected (precondition asserted, else PRECONDITION_NOT_MET), a dry run reports the pick and clicks nothing; the real run reaches done with exactly one click step on the Week candidate, changed=true, and the post-run snapshot shows Week selected; run again from Week view it returns done with zero clicks (negative control). Per-step timings printed.
  CHECK: .venv/bin/python tools/fastlane_eval.py --live-delegate Calendar "switch to week view" --done-when Week --max-steps 3 --require-transition
  CWD: bridge
  EXPECT: LIVE_DELEGATE_DONE
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=precondition met (Month view); dry run: pick=5 radio button "Week" via keyword, zero clicks; real run: done matched="Week" via=ax; steps=1; last=click radio button: Week; changed=True; snapshot 34 ms (124 nodes) act 128 ms settle 210 ms; negative control from week view: done, steps=0, zero clicks; LIVE_DELEGATE_DONE (2026-09-21)

- [x] G12: Live paired A/B through the real ComputerAgent and gpt-6-astra: two tasks that need ≥ 2 sequential labelled clicks in one app, 4 runs per arm alternating CC_BUDDY_FAST_LANE=0/1, same app state reset between runs; the table reports per arm median wall, astra calls, delegate steps, failed clicks, recovery turns, and names the 16 run-log files. The gate is that the table exists and is stated honestly; no pass/fail on the number.
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=paired A/B, real gpt-6-astra + real worker, ABBA order, app state reset between runs, 2026-09-21 03:21-03:40 — calendar 'show the year view and then go to the next year': OFF n=4 wall p50 13.1 s astra 2.0 delegate 0 failed clicks 0 recovery 0 (4/4 done); ON n=4 wall p50 16.9 s astra 3.0 delegate 0.25/run (1 run used it: done via=ocr in 1 step) failed 0 recovery 0 (4/4 done) — settings 'open the General pane and then open Language & Region': OFF n=4 wall p50 13.3 s astra 2.0 failed 0 recovery 0 (4/4 done); ON n=4 wall p50 14.6 s astra 3.0 delegate 1.0 failed 0 recovery 2.0 — 3 of 4 lane runs returned confirm: "Language & Region" (System Settings rule: only rows/cells/tabs/links/Back are clickable, a pane-navigation BUTTON is treated as a value change) and the scripted human said no, so the task ended incomplete; 1 run: delegate done via=ocr in 1 step. Honest reading: on these two-click tasks the lane did not save wall time (the planner spends a turn to call it, +1 astra call) and the System Settings rule is too conservative for pane navigation. 16 run logs: 2026-09-21-032116/032136/032156/032220/032235/032255/032310/032334 (calendar) and 033524-034003 range (settings) under ~/.config/cc-buddy-bridge/agent-runs/ (scratchpad ab-calendar.json, ab-settings.json)

- [x] G13: docs/stackchan/voice.md, README.md and bridge/README.md describe the fast lane, the result lines, the code gates, the shadow verifier, the env knobs, the eval, the measured numbers and what is deferred; a doc test greps each knob and each status word in voice.md.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_fastlane_docs.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=3 passed (voice.md fast-lane section + knobs, README module row + [fast] clause, bridge/README install paragraph; documented defaults equal fast_lane.FAST_LANE_DEFAULT / DEFAULT_STYLE by import) PYTEST_OK

- [x] G14: Shadow-verifier agreement from real runs: a script over ~/.config/cc-buddy-bridge/agent-runs/*.jsonl prints n_shadow (entries with both astra's valid and local p_true), the confusion of p_true ≥ 0.5 against astra's verdict, and the p_true distribution, with n_shadow ≥ 5 (the G12 runs supply them).
  CHECK: .venv/bin/python tools/fastlane_eval.py --shadow-report --min-runs 5
  CWD: bridge
  EXPECT: SHADOW_REPORT_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=78 run logs; n_shadow=8 (the 8 lane-on A/B runs); confusion tp 8 fp 0 fn 0 tn 0, agreement 100%; p_true p10/p50/p90 0.983/0.995/1.0 — the local verdict said true every time and the model agreed every time: no negative case yet, so the agreement is uninformative about discrimination; SHADOW_REPORT_OK

- [x] G15: slither.io as a real-time eval of the local decider (added 2026-09-21 on request): tools/slither_eval.py drives its own Chrome over the DevTools protocol for the game state and the real mouse for input (move to steer, hold the button to boost), the state text names the controls (steer toward the mouse; holding boosts speed and burns length), every heading and the boost pass a code safety shield, and a run of ≥ 2 episodes prints per-episode survival seconds, peak length, decisions, decisions/s, model p50/p95 ms, loop p50/p95 ms, boost fraction and shield interventions, names the recording files, and prints SLITHER_EVAL_OK when ≥ 2 episodes each logged ≥ 100 decisions with zero state-read failures. The numbers are reported, not passed or failed. The menu builder and the shield are unit-tested with fake game states. Precondition: internet, nobody touching the mouse.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_slither_eval.py && .venv/bin/python tools/slither_eval.py --episodes 2 --max-secs 120
  CWD: bridge
  EXPECT: SLITHER_EVAL_OK
  EVIDENCE: exit=0; shell=zsh; cwd=bridge; output=32 passed (menu builder, shield, screen mapping, summary arithmetic) | live: 15 episodes over the session, SLITHER_EVAL_OK on 11 series outputs (one early 1-of-2 run INCOMPLETE), 19.5-19.6 decisions/s every episode, loop p95 20-24 ms, planner gpt-6-astra 3-4 s p50 off the tick path, mouse button up after every episode; ten-episode series peaks 948/45/240/402/217/177/168/581/911/171, kills 14 total; the tenth game is NOT the best (171 vs 911) — reported as measured; recordings, narrative logs, slither_series.jsonl, slither_settings.json and SLITHER_CHANGELOG.md under ~/.config/cc-buddy-bridge/slither-runs/ (leaf S, 2026-09-21)
