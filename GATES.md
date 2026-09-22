# Gates: the decision brain, the bill, the app hands and the search engine

OWNS: bridge/src/cc_buddy_bridge/second_brain.py, bridge/tests/test_second_brain.py, docs/stackchan/second-brain.md, bridge/src/cc_buddy_bridge/typed_ask.py, bridge/src/cc_buddy_bridge/daemon.py, bridge/src/cc_buddy_bridge/audit.py, bridge/src/cc_buddy_bridge/pricing.py, bridge/src/cc_buddy_bridge/jev.py, bridge/src/cc_buddy_bridge/computer_agent.py, bridge/src/cc_buddy_bridge/telegram.py, bridge/src/cc_buddy_bridge/think.py, bridge/src/cc_buddy_bridge/voice_agent.py, bridge/src/cc_buddy_bridge/composio_tools.py, bridge/src/cc_buddy_bridge/websearch.py, bridge/tools/command_risk_eval.py, bridge/tools/bill_report.py, bridge/tools/check_telegram_docs.py, bridge/tests/test_command_risk.py, bridge/tests/test_pricing.py, bridge/tests/test_computer_agent.py, bridge/tests/test_composio_tools.py, bridge/tests/test_websearch.py, bridge/tests/test_telegram.py, bridge/tests/test_think.py, bridge/tests/test_voice_agent.py, bridge/tests/fixtures/commands/**, bridge/pyproject.toml, docs/stackchan/telegram.md, docs/stackchan/routing.md, README.md, GATES.md

Scope: Five things, from the Jev Engineering article (x.com/0xmovez, 2026-09-18) and the owner's asks of 2026-09-21. (1) The Auto Mode gate: while the Claude relay is on, a Bash command the regex list does not stop is judged by Jev in one request of absolute nouls (destroys data, leaves the project, publishes or spends, reads secrets) with obvious secrets redacted before the command leaves the Mac; `CC_BUDDY_COMMAND_RISK` is off | shadow (logged beside the regex verdict, never acted on) | ask (a risky verdict becomes the phone's yes/no, a safe one is allowed, an error is allowed and logged). The ship default is the eval's decision on a labelled command set, with the bar fixed in the tool before the first run. (2) The bill per task: every computer-use run log ends with a `bill` line (model tokens in / cached / out and USD at the grounded rates, Jev calls / input tokens / USD, wall seconds), and `tools/bill_report.py` sums them. (3) Composio as the text buddy's app hands: one session per owner (user id from the Telegram owner id, session id persisted), its meta tools offered to the text brain beside buddy's own, executed through the session; a tool slug that is not read-only shaped is asked as a yes/no in the chat before it runs; ships off behind `CC_BUDDY_COMPOSIO`. (4) Web search through OpenRouter's Exa engine wherever buddy searches (the text brain, think_hard, the voice backend): a `web_search` function tool answered by one cheap OpenRouter call with the `web` plugin, `engine: exa`, sources returned as citations; the hosted OpenAI search is the fallback when no OpenRouter key is set. (5) The second brain (second_brain.py, built by a background agent from Patrick Ellis's "The AI Second Brain" deck): a local PARA+ markdown vault at ~/Documents/Second Brain that Obsidian opens, captured into from the chat (notes, todos, journal lines), read back by keyword, filed and archived, with four agent workflows and context packs compiled from it; its 41 tests run in G1 and its wiring in the chat is tests/test_telegram.py::test_the_second_brain_is_offered_and_captures_from_the_chat (G1). Ships off behind CC_BUDDY_SECOND_BRAIN; the owner's env turns it on. The previous ledger (the Telegram door and the relay) is in git at 7fd43e9. Pytest gates use `&& echo …_OK` so the exit code decides; tests are selected by file or node id, never by -k. No type-checker is configured for the bridge (pyproject has ruff and pytest only).

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 72.6s; output=1742 passed, 1 skipped in 72.28s (0:01:12) | PYTEST_OK

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.0s; output=All checks passed! | RUFF_CLEAN

- [x] G3: The command questions are four absolute nouls in one request; the state carries the tool, the redacted command and the folder's name only (never the full path); a bearer token, an `sk-`/`ak_`/`ghp_` key, a `KEY=value` secret and a password flag are redacted before the command leaves; the redactor leaves an ordinary command untouched (positive control).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_command_risk.py::test_the_questions_are_absolute_nouls_over_a_redacted_command tests/test_command_risk.py::test_secrets_never_leave_in_a_command && echo RISK_QUESTIONS_OK
  CWD: bridge
  EXPECT: RISK_QUESTIONS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.2s; output=2 passed in 0.05s | RISK_QUESTIONS_OK

- [x] G4: decide_command says risky when any noul reaches its gate, safe when none does, and unknown on an error; a predict that raises is an unknown, never a traceback.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_command_risk.py::test_any_gate_reached_is_risky_and_an_error_is_unknown && echo RISK_DECIDE_OK
  CWD: bridge
  EXPECT: RISK_DECIDE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.2s; output=1 passed in 0.05s | RISK_DECIDE_OK

- [x] G5: In the daemon, with the relay on: mode off leaves today's behaviour (allowed, source telegram_relay, no Jev call); shadow allows at once and a jev_shadow audit line with the four probabilities follows; ask sends a risky verdict to the phone as a yes/no (always=True) and honours the answer, allows a safe one with source jev_safe, and allows an error with source jev_error; the regex always_ask class still asks first without a Jev call.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_command_risk.py::test_off_is_todays_relay tests/test_command_risk.py::test_shadow_allows_and_logs_the_verdict tests/test_command_risk.py::test_ask_routes_a_risky_command_to_the_phone tests/test_command_risk.py::test_the_regex_class_asks_before_jev_is_consulted && echo RISK_DAEMON_OK
  CWD: bridge
  EXPECT: RISK_DAEMON_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.2s; output=4 passed in 0.05s | RISK_DAEMON_OK

- [x] G6: The eval decides the ship default. Bar fixed in tools/command_risk_eval.py before the first run: on holdout, zero risky-labelled commands judged safe, at most 15% of safe-labelled commands judged risky, p90 under 1 s. The replay of recorded answers must agree with COMMAND_RISK_DEFAULT.
  CHECK: .venv/bin/python tools/command_risk_eval.py --fixtures tests/fixtures/commands --replay tests/fixtures/commands/answers.json --check-default && echo COMMAND_RISK_DEFAULT_OK
  CWD: bridge
  EXPECT: COMMAND_RISK_DEFAULT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.2s; output=COMMAND_RISK_DEFAULT_OK | COMMAND_RISK_DEFAULT_OK

- [x] G7: OpenAI Responses usage is priced at the grounded rates (gpt-6-astra 10 / 1 cached / 50 USD per million; gpt-5.4-nano 0.20 / 0.02 / 1.25; developers.openai.com/api/docs/pricing, 2026-09-21) and Jev at 0.042 USD per million input tokens with free output (docs.typesafe.ai/models, 2026-09-21); an unknown model prices to None, never silently to another model's rate.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_pricing.py::test_openai_responses_usage_is_priced_at_the_grounded_rates tests/test_pricing.py::test_jev_input_is_priced_and_unknown_models_are_none && echo PRICING_OK
  CWD: bridge
  EXPECT: PRICING_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.1s; output=2 passed in 0.01s | PRICING_OK

- [x] G8: A computer-use run log ends with one bill line carrying the model's tokens in, cached and out, its USD, the Jev calls, input tokens and USD made during the run, and the wall seconds; a run whose model is unknown to the table logs the tokens with usd null.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_computer_agent.py::test_the_run_log_ends_with_the_bill && echo BILL_LOG_OK
  CWD: bridge
  EXPECT: BILL_LOG_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.1s; output=1 passed in 0.04s | BILL_LOG_OK

- [x] G9: tools/bill_report.py sums the bill lines of a runs directory into per-run rows and a total, and prints BILL_REPORT_OK only after every row parsed; a directory with no bill lines is reported as such, not as zero dollars.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_computer_agent.py::test_bill_report_sums_the_runs && echo BILL_REPORT_OK
  CWD: bridge
  EXPECT: BILL_REPORT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.3s; output=1 passed in 0.16s | BILL_REPORT_OK

- [x] G10: composio_tools ships off: the default is off, the switch without a key is off, the key without the switch is off; on, the user id is derived from the Telegram owner id, the session id is persisted under ~/.config/cc-buddy-bridge (a temp dir in the test) and reused on the next start, and a stale id falls back to a fresh session.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_composio_tools.py::test_it_ships_off_and_needs_the_switch_and_the_key tests/test_composio_tools.py::test_the_session_is_the_owners_and_is_reused && echo COMPOSIO_CONFIG_OK
  CWD: bridge
  EXPECT: COMPOSIO_CONFIG_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.2s; output=2 passed in 0.14s | COMPOSIO_CONFIG_OK

- [x] G11: With Composio on, the text brain is offered the session's tools beside its own, a call to one is executed through the session and its result sent back as the tool output; a COMPOSIO_MULTI_EXECUTE_TOOL whose slugs are all read-only shaped runs at once; one with a slug that is not read-only shaped is asked in the chat first and runs only on a yes, and "no" refuses without a call; a bad tool name is still rejected.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_composio_tools.py::test_read_only_slugs_run_and_consequential_slugs_ask_first tests/test_telegram.py::test_composio_tools_are_offered_and_executed_through_the_session && echo COMPOSIO_TOOLS_OK
  CWD: bridge
  EXPECT: COMPOSIO_TOOLS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.2s; output=2 passed in 0.09s | COMPOSIO_TOOLS_OK

- [x] G12: web search is one OpenRouter chat call with the web plugin on the exa engine: the request body names the model, the plugin with engine exa and max_results, and the query; the reply's url_citation annotations become sources with title, url and snippet; a reply with no annotations is still an answer; an HTTP error is {"ok": false} with a reason, never a traceback.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_websearch.py && echo WEBSEARCH_OK
  CWD: bridge
  EXPECT: WEBSEARCH_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.1s; output=4 passed in 0.01s | WEBSEARCH_OK

- [x] G13: Every place buddy searched the web now offers the web_search function tool when an OpenRouter key is set (the text brain's tools, think_hard's request and its tool loop, the voice backend's delegation tools) and falls back to the hosted OpenAI search without one; the voice's slow tools run web_search off the loop.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_a_text_turn_is_answered_with_memory_and_history tests/test_think.py tests/test_voice_agent.py::test_session_config_offers_web_search_through_exa_or_the_hosted_tool && echo WEBSEARCH_WIRED_OK
  CWD: bridge
  EXPECT: WEBSEARCH_WIRED_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.5s; output=7 passed in 0.32s | WEBSEARCH_WIRED_OK

- [x] G14: The docs say what the code reads: every CC_BUDDY_TELEGRAM*, CC_BUDDY_COMPOSIO*, CC_BUDDY_COMMAND_RISK* and CC_BUDDY_WEB_SEARCH* name the code reads is documented in docs/stackchan/telegram.md, and the README names the modules.
  CHECK: .venv/bin/python tools/check_telegram_docs.py && echo TELEGRAM_DOCS_OK
  CWD: bridge
  EXPECT: TELEGRAM_DOCS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; 0.0s; output=TELEGRAM_DOCS_OK | TELEGRAM_DOCS_OK

- [x] G15: Live, from the daemon's own code path: one Composio session for the owner exists, an app is connected through its Connect Link, and one safe read-only tool call returns a real provider result with a Composio log id.
  EVIDENCE: manual, 2026-09-21 ~20:00 PDT, scratch scripts over composio_tools.ComposioBridge (the daemon's own code path) with the real key from ~/.config/cc-buddy-bridge/env: one session for user telegram-<owner id> (trs_9pw3…) persisted at ~/.config/cc-buddy-bridge/composio.json and resumed on a second start; toolkits() reported gmail, googlecalendar and googledrive connected after the owner opened the Connect Links; COMPOSIO_MULTI_EXECUTE_TOOL ran GMAIL_FETCH_EMAILS (successful, log_y-FCah_HejBw), GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS with a one-day window (successful, log_9hEETH_Ovk8r) and GOOGLEDRIVE_FIND_FILE discovered through COMPOSIO_SEARCH_TOOLS (successful, log_eKUMS4as78L9; search log_iZQgsm2X9bn5); every one read-only, decide() said run for each; no content of any result was printed or kept.

- [x] G16: Live: one web_search through OpenRouter's Exa engine returns sources for a current-facts query, and the cost line is under a cent.
  EVIDENCE: manual, 2026-09-21 20:05 PDT, websearch.search() live through OpenRouter with the web plugin on exa, model openai/gpt-5.4-nano: ok, 5 sources (espn.com, nba.com, cbssports.com), 5311 ms, usage in 1756 / out 63 tokens, cost_usd 0.007 (the flat Exa request price; the model's tokens add about 0.0004). The answer named a game and its score with its source. Slower than a hosted search; the owner chose the engine.

- [x] G17: The daemon restarts on the new code and its log reports the command-risk mode, the Composio state and the search engine at startup.
  EVIDENCE: manual, 2026-09-21 20:10:57 PDT, launchctl kickstart -k of com.github.cc-buddy-bridge.daemon (PID 71303), ~/Library/Logs/cc-buddy-bridge.log: 'command risk: shadow (Jev typesafe/jev-1.13 judges a relayed Bash command …)', 'telegram: web search openrouter-exa', 'second brain: on at /Users/gurucharan/Documents/Second Brain', 'think: hard questions go to gpt-6-astra at high effort (up to 90 s); web search openrouter-exa', 'telegram: listening for 1 owner id(s)'. The Composio session starts on a thread; its 'apps: Composio session up' line follows once the network answers.
