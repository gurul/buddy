# Gates: lane-first routing (keyword gate before the planner, laya out of the click path)

OWNS: bridge/src/cc_buddy_bridge/fast_lane.py, bridge/src/cc_buddy_bridge/lane_router.py, bridge/src/cc_buddy_bridge/desktop_helpers.py, bridge/src/cc_buddy_bridge/desktop_worker.py, bridge/src/cc_buddy_bridge/computer_agent.py, bridge/tools/fastlane_eval.py, bridge/tests/test_lane_first.py, bridge/tests/test_agent_lane_first.py, bridge/tests/test_desktop_worker.py, bridge/tests/test_fastlane_eval.py, bridge/tests/test_fastlane_docs.py, docs/stackchan/voice.md, README.md, GATES.md

Scope: The fast lane decides by the keyword gate alone (the local model leaves the click path), runs BEFORE the planner's first turn when the spoken goal is fully covered by one labelled control per clause, ends a fully lane-decided task with a local sentence and zero planner calls, and lets the planner hand the lane an ordered list of exact labels in one call. Every safety gate of fast_lane.py still applies. The previous ledger (fast lane v2) is in git at d28696d. Pytest gates use `&& echo …_OK` so the exit code decides; tests are selected by file or node id, never by -k.

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=1512 passed, 1 skipped in 67.08s (0:01:07) | PYTEST_OK

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=All checks passed! | RUFF_CLEAN

- [x] G3: In keyword decide mode the lane needs no model: with decider=None it clicks the one control that uniquely shares the most goal words, and on a tie or on zero shared words it escalates with zero input and zero model calls (a Decider fake that fails the test if asked is the positive control for "never asked").
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lane_first.py::test_keyword_mode_clicks_without_a_decider tests/test_lane_first.py::test_keyword_mode_tie_escalates_and_never_asks_the_model tests/test_lane_first.py::test_keyword_mode_zero_overlap_escalates_no_match tests/test_lane_first.py::test_model_mode_still_asks_the_model_on_a_tie && echo KEYWORD_MODE_OK
  CWD: bridge
  EXPECT: KEYWORD_MODE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.03s | KEYWORD_MODE_OK

- [x] G4: run_script runs an ordered list of objectives, one keyword-decided click each, against a fresh snapshot per step, and stops at the first step that does not act, reporting exactly which steps applied.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lane_first.py::test_script_runs_each_step_in_order tests/test_lane_first.py::test_script_stops_at_the_first_step_that_does_not_act tests/test_lane_first.py::test_script_sensitive_step_confirms_and_clicks_nothing_more tests/test_lane_first.py::test_script_is_capped_at_six_steps && echo SCRIPT_OK
  CWD: bridge
  EXPECT: SCRIPT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.02s | SCRIPT_OK

- [x] G5: The router engages only when every goal word of a clause is covered by the matched control's label, its value, or the frontmost app's name; it splits "A and then B" into clauses; it refuses a goal that names another installed app, a question, and an empty clause. Positive and negative goals are both asserted.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lane_first.py::test_router_clauses tests/test_lane_first.py::test_router_engages_on_a_fully_covered_goal tests/test_lane_first.py::test_router_refuses_a_goal_with_uncovered_words tests/test_lane_first.py::test_router_refuses_a_goal_naming_another_app tests/test_lane_first.py::test_router_two_clauses_two_clicks && echo ROUTER_OK
  CWD: bridge
  EXPECT: ROUTER_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=5 passed in 0.02s | ROUTER_OK

- [x] G6: Through ComputerAgent: a fully lane-decided task returns a spoken sentence with ZERO create_response calls; a partly decided one reaches the planner with a [note] naming what the lane already did; a lane click that changed nothing goes to the planner; lane-first off never calls the worker's lane_first.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_lane_first.py && echo AGENT_LANE_FIRST_OK
  CWD: bridge
  EXPECT: AGENT_LANE_FIRST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=5 passed in 0.04s | AGENT_LANE_FIRST_OK

- [x] G7: The worker answers a `lane_first` request with {status, line, applied, changed, log} and still rejects an unknown operation.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_desktop_worker.py::test_lane_first_operation_replies_with_the_route tests/test_desktop_worker.py::test_lane_first_without_the_lane_says_unavailable tests/test_desktop_worker.py::test_unknown_operation_is_still_unsupported && echo WORKER_LANE_FIRST_OK
  CWD: bridge
  EXPECT: WORKER_LANE_FIRST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed in 0.02s | WORKER_LANE_FIRST_OK

- [x] G8: In System Settings the General pane's own navigation buttons are offered (Language & Region is clicked), and a value button still returns confirm with zero input (Dark in Appearance is the positive control for the rule still biting).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lane_first.py::test_settings_general_navigation_button_is_clicked tests/test_lane_first.py::test_settings_value_button_still_confirms && echo SETTINGS_NAV_OK
  CWD: bridge
  EXPECT: SETTINGS_NAV_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.02s | SETTINGS_NAV_OK

- [x] G9: The offline eval measures the router on the real fixtures (precision of its clicks, how often it engages, wrong clicks, sensitive picks) on both sets, prints its ship decision against criteria fixed in the tool, and the shipped LANE_FIRST_DEFAULT equals that decision.
  CHECK: .venv/bin/python tools/fastlane_eval.py --fixtures tests/fixtures/ax --router && .venv/bin/python tools/fastlane_eval.py --fixtures tests/fixtures/ax --check-default && echo ROUTER_EVAL_OK
  CWD: bridge
  EXPECT: ROUTER_EVAL_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=DEFAULT_CONSISTENT | ROUTER_EVAL_OK

- [x] G10: The planner prompt teaches `steps=[…]` and the docs describe lane-first, the decide modes, the router's rule and its measured numbers; the docs test passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_fastlane_docs.py && echo DOCS_OK
  CWD: bridge
  EXPECT: DOCS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.02s | DOCS_OK

- [x] G11: Live, on this Mac: one real lane-first task through ComputerAgent (Calendar, month view to week view) finishes with zero planner calls, and the wall time is printed by the tool. Precondition: Calendar is open and not in week view; the tool restores the starting view.
  CHECK: .venv/bin/python tools/fastlane_eval.py --live-route Calendar "switch to week view" --expect-selected Week --restore "switch to month view" && echo LIVE_ROUTE_OK
  CWD: bridge
  EXPECT: LIVE_ROUTE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=LIVE_ROUTE_DONE wall=1.47s planner_calls=0 | LIVE_ROUTE_OK

- [x] G12: The answer to "should astra stream instructions into laya" rests on measured planner-turn timing from the run logs (reasoning tokens against visible output tokens), not on a guess.
  EVIDENCE: manual; 184 planner turns with token usage from 79 run logs under ~/.config/cc-buddy-bridge/agent-runs (2026-09-21): api_secs p50 3.36 s, p90 5.53 s; visible output p50 35 tokens, reasoning p50 0; least-squares secs = 2.82 + 0.0223*reasoning_tokens + 0.0234*visible_tokens (R2 0.20, so rough); a median turn is ~2.8 s fixed before the first token and ~0.8 s of writing; median run is 3 turns. Conclusion recorded in docs/stackchan/voice.md (Lane first, Deferred): batching steps into one turn and skipping turns is worth seconds, token streaming at most ~0.8 s a turn.

- [x] G13: The request classifier's rules behave as specified on the traps the holdouts found, a model is asked only after the code gates and may only add a bare launch of an installed app, and through ComputerAgent a complete reflex makes ZERO planner calls, an unconfirmed one falls to the planner untouched, and a partial one tells the planner what is done and what is left.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_task_router.py && echo TASK_ROUTER_OK
  CWD: bridge
  EXPECT: TASK_ROUTER_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=14 passed in 0.08s | TASK_ROUTER_OK

- [x] G14: The reflex ship decision is the tool's, on a holdout written by an author who saw neither code nor any other set, against a bar fixed in the tool; the shipped REFLEX_DEFAULT and REFLEX_LAUNCH_DEFAULT equal it.
  CHECK: .venv/bin/python tools/route_eval.py --check-default && echo ROUTE_DEFAULT_OK
  CWD: bridge
  EXPECT: ROUTE_DEFAULT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=ROUTE_EVAL_COMPLETE | ROUTE_DEFAULT_OK

- [x] G15: laya and Jev are each asked in their own idiom (typed_ask.py): laya only relative choices with short options and a fitted cut-off, Jev an absolute noul gate plus choices in one request with literal instructions; the askers abstain on any model failure.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_task_router.py::test_jev_is_asked_in_its_own_idiom_and_gated_by_absolute_answers tests/test_task_router.py::test_each_model_is_asked_differently_for_a_head_pose && echo TYPED_ASK_OK
  CWD: bridge
  EXPECT: TYPED_ASK_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.02s | TYPED_ASK_OK

- [x] G16: The routing document states the shipped defaults, names every knob, and carries the engine comparison, the per-model protocols, the measured tables and the credibility notes; voice.md and the README point at it.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_task_router.py::test_the_routing_doc_states_the_shipped_defaults_and_names_every_knob && echo ROUTING_DOC_OK
  CWD: bridge
  EXPECT: ROUTING_DOC_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=1 passed in 0.02s | ROUTING_DOC_OK

- [x] G17: Live on this Mac, through the real ComputerAgent with a planner that fails the run if called: a rule launch, a launch only Jev recognised, and a web search each finish with zero planner calls.
  EVIDENCE: manual; 2026-09-21 ~05:20 PDT: "Open up Calculator on my Mac." said "Opened Calculator." wall 1.61 s, reflex 1.24 s, planner calls 0; "Can you get Safari up on the screen?" (CC_BUDDY_ROUTER_MODEL=jev; the rules declined, Jev native named Safari) said "Opened Safari." wall 2.45 s, planner calls 0; "Search Google for the tallest mountain in Japan." said "Here's a search for the tallest mountain in Japan." wall 2.55 s, planner calls 0. The same run found two defects, both fixed: a launch that never takes focus (Preview with no document) cost the helper's full 8 s before the planner ran (now 4 s), and Jev correctly abstained on "Preview, please." (launch_only 0.19) and "Notes, please." (risky 0.64), which go to the planner.

- [x] G18: The head-move question ("jev, or is laya better?") is answered by measurement on utterances written by an author who saw no code, with each model asked natively and its cut-offs fitted on a half it is not scored on.
  EVIDENCE: manual; tools/head_eval.py on tests/fixtures/routes/head_moves.json (110 utterances: 66 poses, 44 none, 34 traps), 2026-09-21. Jev native, test half: 32/33 poses (97.0%), 0 wrong, 0 false moves of 22, messy speech 12/12, 244 ms p50 / 292 ms p95; fitted gate 0.30, direction 0.70. Jev asked one 16-option question: 86.4%, 1 false move. laya native, test half: 4/33 (12.1%), 3 wrong, 0 false moves, 9.6 ms; with the gate given (oracle): direction 54/66 (81.8%), exact pose 41/66 (62.1%). Keyword rule, untuned: 47/66 (71.2%), 1 wrong, 4 false moves of 44. Conclusion in docs/stackchan/routing.md: Jev is ready for the move call; laya needs fine-tuning, which its own authors say. Not wired into the voice path.

- [x] G19: In a conversation the follower converges on a still person and then does not twitch; one sighting is not a person and an outlier does not move the head; frames from a swinging head are not evidence; a walking person is led; a passer-by does not steal the gaze; an asked-for pose is the owner's; leaving a follow phase hands the head back at once; someone who leaves fast is found "where they were heading" and someone who vanishes from stillness "where they usually are"; it searches once per loss and never keeps scanning an empty room; the presence map learns, decays and survives a restart; presence is a bus topic; the tracker hands it every face and survives its failure.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_follow.py && echo FOLLOW_OK
  CWD: bridge
  EXPECT: FOLLOW_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=19 passed in 0.21s | FOLLOW_OK

- [x] G20: The firmware with the eye lead compiles for the board (it is NOT flashed by this gate); it needs the libraries of docs/stackchan/build.md, including StackChan-BSP at main 8d4d6fc and AnimatedGIF 2.2.0.
  CHECK: arduino-cli compile --fqbn "esp32:esp32:m5stack_cores3:PartitionScheme=huge_app,PSRAM=enabled" --build-path /tmp/buddy-fwbuild firmware/claude_pet_stackchan >/tmp/buddy-fwbuild.log 2>&1 && grep -q "Sketch uses" /tmp/buddy-fwbuild.log && echo FIRMWARE_COMPILES
  EXPECT: FIRMWARE_COMPILES
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=574d30059456/19 entries; output=FIRMWARE_COMPILES

- [x] G21: With CC_BUDDY_HEAD_MODEL=jev a sure pose turns the head at once through Head.move with the backend's own numbers; the backend's move_head for the same turn is answered "already done" and does not turn it again, while a new turn's move runs; "none", a failure and a late answer all leave the turn to the ordinary path; only turns that mention a direction or the head are ever sent.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_voice_fast_head.py && echo FAST_HEAD_OK
  CWD: bridge
  EXPECT: FAST_HEAD_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.02s | FAST_HEAD_OK

- [x] G22: The eye-lead firmware is on the board, with a rollback: the previous app partition was read off the board first, the new image was written and verified block by block, the board booted it and a live conversation ran on it with the follower and the fast head path.
  EVIDENCE: manual; 2026-09-21 05:2x PDT. Backup: esptool read-flash 0x10000 0x300000 at 460800 baud (921600 failed with "Serial data stream stopped") → ~/.config/cc-buddy-bridge/firmware-app-backup-2026-09-21.bin, 3145728 bytes, containing "[boot] claude_pet_stackchan 835c395-dirty". Flash: tools/flash_stackchan.sh, ELF archived as firmware/build-archive/claude_pet_stackchan-d28696d-dirty-20260921-052936.elf, 789744 bytes at 0x10000, "Hash of data verified" for all four regions. Boot: "board diag: boot #4 after unknown (up=3s heap=207964 …)"; the DIED IN sprite-push line is the OLD run being reset by the flasher. Live: wake, follower moves accepted ("[gaze] host look"), and "Look to your left" → "voice: head → left in 0.45 s (fast path)" then "head request already delegated by the voice — not routing it again". NOT verified: what the eyes look like — nobody but the owner can see the screen. Restore with: esptool --chip esp32s3 --port /dev/cu.usbmodem101 write-flash 0x10000 <backup>.
