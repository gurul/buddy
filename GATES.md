# Gates: text buddy through Telegram

OWNS: bridge/src/cc_buddy_bridge/browser_lane.py, bridge/tests/test_browser_lane.py, bridge/tests/test_agent_browser_lane.py, bridge/src/cc_buddy_bridge/computer_agent.py, bridge/src/cc_buddy_bridge/explore.py, docs/stackchan/routing.md, docs/stackchan/build.md, bridge/src/cc_buddy_bridge/records.py, bridge/tests/test_records.py, bridge/src/cc_buddy_bridge/telegram.py, bridge/src/cc_buddy_bridge/daemon.py, bridge/src/cc_buddy_bridge/cli.py, bridge/tests/test_telegram.py, bridge/tools/check_telegram_docs.py, bridge/pyproject.toml, docs/stackchan/telegram.md, README.md, GATES.md

Scope: The owner can text buddy from Telegram and get an answer, start and stop a computer task, answer a task's question, receive a photo from the robot's camera, see the screen, and receive files from the home folder (never a hidden path). The daemon long-polls the Bot API (outbound HTTPS only: no port, no webhook, no public URL). Only the owner's numeric Telegram id, in a private chat, reaches a model; everyone and everything else is dropped without a reply. Message text and the bot token are never logged. It ships OFF behind CC_BUDDY_TELEGRAM. (2) The text brain has a memory layer in the Instinct shape (records.py): typed git-tracked markdown records with aliases and [[links]], a profile one-pager in every turn, and read-only memory_search / memory_get tools; the only writer is a nightly reconcile from the day's curated notes, committed to git with the pre-reconcile state committed first. It ships OFF behind CC_BUDDY_RECORDS. (3) The browser lane (browser_lane.py): Playwright drives buddy's own Chromium for web goals, the page is presented as the same Snapshot the executor reads, and plan_executor.run_plan with the keyword gate and Jev runs unchanged on it; the Mac tiers stand down for a goal the lane took. Ships OFF behind CC_BUDDY_BROWSER_LANE. (5) "claude on" / "claude off" relays Claude Code to the chat explicitly: its words, its waiting, a permission prompt as a yes/no the owner's next message answers (silence defers, never denies), and "claude: text" typed into the session's terminal; no emoji or dashes ever leave the Mac. (4) Over Telegram every robot tool the voice has is reachable, the robot shows what the chat does, and "stealth mode" makes it play asleep; the idle explore start now ships off (CC_BUDDY_EXPLORE=1 turns it on). The previous ledger (plan once, the voice gate, the power pill) is in git at 5ee6164. Pytest gates use `&& echo …_OK` so the exit code decides; tests are selected by file or node id, never by -k. No type-checker is configured for the bridge (pyproject has ruff and pytest only).

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=1673 passed, 1 skipped in 72.25s (0:01:12) | PYTEST_OK

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=All checks passed! | RUFF_CLEAN

- [x] G3: It ships off and refuses to run half-configured: the default is False, an empty environment is off, the switch without a token is off, and the switch with a token but no owner id is off (an inlet with no allowlist never starts). The daemon builds no inlet when it is off.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_it_ships_off tests/test_telegram.py::test_the_switch_alone_is_not_enough tests/test_telegram.py::test_no_owner_id_means_no_inlet tests/test_telegram.py::test_the_daemon_builds_no_inlet_when_off && echo SHIPS_OFF_OK
  CWD: bridge
  EXPECT: SHIPS_OFF_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.31s | SHIPS_OFF_OK

- [x] G4: Only the owner, in a private chat, reaches a model. A stranger, a group the owner is in, a channel post, an edited message, another bot and a message older than the stale window each cause zero model calls and zero sends. Words that are not the owner's own (a forwarded message) and things that are not words (a sticker, a voice note) cause zero model calls and one fixed line back. Positive control: the same update with the owner's id and a fresh date does reach the model in the same test.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_only_the_owner_in_a_private_chat_is_accepted tests/test_telegram.py::test_a_dropped_update_costs_no_model_call_and_no_reply tests/test_telegram.py::test_a_backlog_from_before_the_daemon_started_is_dropped tests/test_telegram.py::test_forwarded_words_and_non_text_never_reach_a_model && echo ALLOWLIST_OK
  CWD: bridge
  EXPECT: ALLOWLIST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.06s | ALLOWLIST_OK

- [x] G5: What the owner wrote and the bot token never reach the log, on the accepted path, the dropped path and the error path (the token is part of every Bot API URL, so an HTTP error is the dangerous one). Positive control: the same capture does contain the sender's numeric id on the dropped path, so the capture is proven live.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_words_and_the_token_never_reach_the_log && echo PRIVACY_OK
  CWD: bridge
  EXPECT: PRIVACY_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=1 passed in 0.05s | PRIVACY_OK

- [x] G6: A text turn is answered in the chat, with buddy's memory in the prompt and the earlier turns of the chat as context; a long answer is split under Telegram's 4096-character limit without losing a character; a model failure is answered with one plain line rather than silence.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_text_turn_is_answered_with_memory_and_history tests/test_telegram.py::test_a_long_answer_is_split_without_losing_a_character tests/test_telegram.py::test_a_model_failure_is_answered_not_swallowed && echo TEXT_TURN_OK
  CWD: bridge
  EXPECT: TEXT_TURN_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed in 0.06s | TEXT_TURN_OK

- [x] G7: A texted task runs the same computer agent the voice uses and its result is texted back; a second task while one runs is refused; "stop" cancels the running task; with computer control disabled no task starts.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_texted_task_runs_the_agent_and_texts_the_result tests/test_telegram.py::test_a_second_task_is_refused_while_one_runs tests/test_telegram.py::test_stop_cancels_the_running_task tests/test_telegram.py::test_no_task_starts_when_computer_control_is_off && echo TASK_OK
  CWD: bridge
  EXPECT: TASK_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.07s | TASK_OK

- [x] G8: Only the human approves, over chat too: a task's question is sent to the chat, the owner's next message is the answer and is not also run as a new turn, a stranger's message cannot answer it, and no answer in time reads as no.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_tasks_question_is_answered_by_the_owners_next_message tests/test_telegram.py::test_a_stranger_cannot_answer_a_tasks_question tests/test_telegram.py::test_no_answer_in_time_reads_as_no && echo APPROVAL_OK
  CWD: bridge
  EXPECT: APPROVAL_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed in 0.13s | APPROVAL_OK

- [x] G9: "Send me a photo" sends the picture the robot took as a Telegram photo with its caption, and says why when there is no camera.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_photo_is_sent_as_a_photo tests/test_telegram.py::test_no_camera_is_said_not_sent && echo PHOTO_OK
  CWD: bridge
  EXPECT: PHOTO_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.06s | PHOTO_OK

- [x] G10: The poll loop survives: an update is handled once (the offset moves past it), a network error backs off and polling resumes, a slow turn does not stop the next poll, and a 401 (bad token) or a 409 (another poller on the same token) stops the inlet with one log line instead of spinning.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_an_update_is_handled_once tests/test_telegram.py::test_a_network_error_backs_off_and_polling_resumes tests/test_telegram.py::test_a_slow_turn_does_not_stop_the_next_poll tests/test_telegram.py::test_a_bad_token_or_a_second_poller_stops_the_inlet && echo POLL_OK
  CWD: bridge
  EXPECT: POLL_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.06s | POLL_OK

- [x] G11: The chat is remembered the way a spoken conversation is: after the chat goes quiet its turns are handed to buddy's conversation memory once, and the in-memory history is bounded.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_quiet_chat_is_handed_to_memory_once tests/test_telegram.py::test_history_is_bounded && echo MEMORY_OK
  CWD: bridge
  EXPECT: MEMORY_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.05s | MEMORY_OK

- [x] G12: The docs say what the code does: every CC_BUDDY_TELEGRAM* name the module reads is documented in docs/stackchan/telegram.md (the checker reads the names out of telegram.py, it does not carry a list), the README names the module and links the doc, and pyproject declares the HTTP client the module imports.
  CHECK: .venv/bin/python tools/check_telegram_docs.py
  CWD: bridge
  EXPECT: TELEGRAM_DOCS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=7 names documented: CC_BUDDY_RECORDS, CC_BUDDY_RECORDS_MODEL, CC_BUDDY_TELEGRAM, CC_BUDDY_TELEGRAM_EFFORT, CC_BUDDY_TELEGRAM_MODEL, CC_BUDDY_TELEGRAM_OWNER, CC_BUDDY_TELEGRAM_TOKEN | TELEGRAM_DOCS_OK

- [x] G18: The owner can see the Mac and receive files: "screenshot" / "show me the screen" is answered by code with zero model calls, mid-task too; a task whose request asked to see something arrives with the screen it left and one that did not gets words only; a failed capture is said; a file leaves only from the owner's home, never from a hidden path even through a symlink, never a folder, never over the Bot API limit; the document really goes out as multipart.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_task_that_was_asked_to_show_something_arrives_with_the_screen_it_left tests/test_telegram.py::test_a_plain_screenshot_request_is_answered_by_code_even_mid_task tests/test_telegram.py::test_the_owner_can_ask_for_the_screen_and_a_failed_capture_is_said tests/test_telegram.py::test_files_leave_only_from_the_owners_home_and_never_from_a_hidden_folder tests/test_telegram.py::test_send_file_sends_a_document_and_refuses_folders_and_big_files && echo SEE_AND_SEND_OK
  CWD: bridge
  EXPECT: SEE_AND_SEND_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.09s | SEE_AND_SEND_OK

- [x] G14: The records layer ships off, and the text brain is offered the profile and the two memory tools only when there is a profile; neither tool writes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_records.py::test_it_ships_off tests/test_records.py::test_the_text_brain_gets_the_profile_and_the_two_tools_only_with_records && echo RECORDS_OFF_OK
  CWD: bridge
  EXPECT: RECORDS_OFF_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.02s | RECORDS_OFF_OK

- [x] G15: A record round-trips through its file and a hand-edited one still parses; search is keyword over aliases and lines with aliases weighted; the reader re-reads disk so an owner edit counts at once.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_records.py::test_a_record_round_trips_and_a_hand_edited_one_still_parses tests/test_records.py::test_load_skips_the_profile_and_a_file_whose_id_does_not_match_its_name tests/test_records.py::test_search_is_keyword_over_aliases_and_lines_and_aliases_count_more tests/test_records.py::test_the_reader_re_reads_disk_so_an_owner_edit_counts_at_once && echo RECORDS_READ_OK
  CWD: bridge
  EXPECT: RECORDS_READ_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.02s | RECORDS_READ_OK

- [x] G16: The reconcile is the only writer: a curated day becomes changed records, a rewritten profile with the record index, and two git commits (as-found, then reconcile) so the replaced fact is in HEAD~1; a forgotten record leaves the tree but stays in history; a bad model result writes nothing and the day stays due; malformed ids never escape the folder; without git the records are still written.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_records.py::test_a_day_is_reconciled_into_records_a_profile_and_one_commit tests/test_records.py::test_forgetting_removes_the_file_and_git_still_has_it tests/test_records.py::test_a_bad_result_writes_nothing_and_the_day_stays_due tests/test_records.py::test_apply_skips_malformed_entries_and_never_escapes_the_folder tests/test_records.py::test_a_day_without_notes_is_nothing_and_the_loop_stops_on_shutdown tests/test_records.py::test_without_git_records_are_still_written && echo RECONCILE_OK
  CWD: bridge
  EXPECT: RECONCILE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=6 passed in 0.50s | RECONCILE_OK

- [x] G17: MANUAL — the reconcile prompt works on the real model and the real notes: on a scratch copy of this Mac's debrief store, two curated days (2026-09-12, 2026-09-15) reconciled with gpt-5.4-nano into eight typed records with aliases and dated facts, a three-section profile, and the record index; the scratch store received two commits per day.
  EVIDENCE: manual; run 2026-09-21 in the session's scratchpad (scratchpad/store), output in the session transcript; records included ai-voice-preference (preference), ai-memory-system (project), math-learning-routine (routine), friend-3-crayons (person).

- [x] G19: The browser lane ships off, web goals are decided by code, and a page becomes the Snapshot the gate reads (roles, states, the focused field, no secure field in the outline).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_browser_lane.py::test_it_ships_off_and_web_goals_are_decided_by_code tests/test_browser_lane.py::test_a_page_becomes_a_snapshot_the_gate_can_read && echo BROWSER_OFF_OK
  CWD: bridge
  EXPECT: BROWSER_OFF_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.21s | BROWSER_OFF_OK

- [x] G20: Live Chromium through the real executor: the page senses and effectors keep the lane contract (a click, a toggled state seen as a change, a sensitive control refused by code then let through by an approval, a secure field never typed into, typing and Return with their oracles); a four-step plan runs to complete with no model; a sensitive control stops the plan for the human and a yes lets exactly that through.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_browser_lane.py::test_the_page_senses_and_effectors_keep_the_lane_contract tests/test_browser_lane.py::test_a_plan_runs_through_the_real_executor_with_no_model tests/test_browser_lane.py::test_a_sensitive_control_stops_the_plan_for_the_human_and_a_yes_lets_it_through -rs | tee /dev/stderr | grep -q "3 passed" && echo BROWSER_LIVE_OK
  CWD: bridge
  EXPECT: BROWSER_LIVE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=...                                                                      [100%] | 3 passed in 3.35s

- [x] G21: In the agent, a web goal is planned once against the browser with one planner call and the Mac tiers never touched; a Mac goal never touches the browser; a web search is one navigation with no model call and Safari is not also opened; a failing browser or a partial plan hands the goal to today's loop with a note of what was done.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_browser_lane.py && echo BROWSER_AGENT_OK
  CWD: bridge
  EXPECT: BROWSER_AGENT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=5 passed in 0.03s | BROWSER_AGENT_OK

- [x] G22: Over text the robot's tools work ("look left" moves the head, "start taking notes" starts the taker, mute, remember, look), the robot shows the chat's task on its face and screen, and stealth mode — a code word — sends the face idle, shows nothing, refuses to move the head, and "wake up" ends it; the idle explore start ships off with CC_BUDDY_EXPLORE=1 turning it on.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_text_is_a_word_said_to_the_robot tests/test_telegram.py::test_the_robot_shows_what_the_chat_does_unless_stealth tests/test_explore.py::test_configured_defaults && echo ROBOT_OVER_TEXT_OK
  CWD: bridge
  EXPECT: ROBOT_OVER_TEXT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed in 0.09s | ROBOT_OVER_TEXT_OK

- [x] G23: The Claude relay is off until "claude on" and forwards only while on (words, waiting, never idle reminders); a permission prompt is answered from the phone by the owner only, silence defers; the daemon honours an allow or deny from the phone before its robot-connected check and defers without one; "claude: text" types into the terminal only while on; no emoji or dash ever leaves in a message or a caption.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_the_claude_relay_is_off_until_said_and_forwards_only_while_on tests/test_telegram.py::test_a_permission_prompt_is_answered_from_the_phone_and_silence_defers tests/test_telegram.py::test_the_daemon_honours_the_phones_decision_and_defers_without_one tests/test_telegram.py::test_no_emoji_leaves_the_mac && echo CLAUDE_RELAY_OK
  CWD: bridge
  EXPECT: CLAUDE_RELAY_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed in 0.13s | CLAUDE_RELAY_OK

- [ ] G13: MANUAL — a live round trip from the owner's phone: a text is answered, a texted task runs on this Mac and its result arrives, a task question is answered from the phone, a photo arrives, and a message from a second Telegram account gets nothing.
  EVIDENCE: pending
