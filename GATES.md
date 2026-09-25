# Gates: the watcher — prices, stocks and ticket releases, rate limited, told over Telegram

OWNS: bridge/src/cc_buddy_bridge/watch.py, bridge/tests/test_watch.py, bridge/src/cc_buddy_bridge/telegram.py, bridge/tests/test_telegram.py, bridge/src/cc_buddy_bridge/daemon.py, bridge/src/cc_buddy_bridge/spend.py, bridge/tools/watch_smoke.py, bridge/tests/conftest.py, bridge/tests/test_watch_lean_*.py, verification/Buddy/Watch*.lean, verification/check-all.mjs, docs/stackchan/watch.md, docs/stackchan/telegram.md, docs/verification.md, README.md, GATES.md

Scope: The owner asks buddy (over Telegram) to watch something — a stock or crypto quote, a product or ticket page, or a question with no URL such as "tickets for X in Seattle on sale" — and buddy checks it on a schedule and texts when the price drops or rises past a mark, moves by a percentage, changes, comes into stock / on sale, or a phrase appears. Every outbound fetch goes through one rate limiter (a global token bucket, a minimum gap per host, a per-host backoff that honours Retry-After, per-kind interval floors) and every model call through a daily cap. Conditions fire on the edge, once, and re-arm. Watches persist across restarts. Added mid-build at the owner's asks: a Ticketmaster kind (TICKETMASTER_API_KEY); vision — a page that needs JavaScript or refuses a plain read is rendered in headless Chromium, its overlays pressed away, and its screenshot read by a vision model that also recognises bot checks; `/watch` in Telegram; the ideas a six-agent swarm found in changedetection.io, extruct, yfinance, PyrateLimiter/aiolimiter and the Ticketmaster docs; and a Lean pass over the limiter, the conditions, the scheduler and the Ticketmaster rule (ultracode workflow). The previous ledger (the Lean verification pass) is in git at 48fdbc3. No type-checker is configured for the bridge (pyproject has ruff and pytest only).

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3029 passed, 13 skipped in 480.20s (0:08:00) | PYTEST_OK

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=All checks passed! | RUFF_CLEAN

- [x] G3: The rate limiter: the bucket runs dry and refills with the clock; a second request to one host waits out the gap while another host goes at once; a 429 with Retry-After holds the host at least that long and doubles on repeat, capped; a success clears the backoff; the daily model cap refuses the call past it and resets the next day.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_the_bucket_runs_dry_and_refills tests/test_watch.py::test_one_host_waits_out_its_gap_another_goes_at_once tests/test_watch.py::test_retry_after_holds_the_host_and_backoff_doubles_capped tests/test_watch.py::test_the_daily_model_cap_refuses_and_resets tests/test_watch.py::test_a_clock_that_steps_back_mints_no_tokens && echo LIMITER_OK
  CWD: bridge
  EXPECT: LIMITER_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=5 passed in 0.02s | LIMITER_OK

- [x] G4: Reading a page: JSON-LD offers (price, lowPrice, availability), product/og price meta tags and itemprop price are read with no model call (positive control: a page with JSON-LD makes zero model calls); a page with none of them goes to the model once, and the model's JSON is validated; a phrase condition is decided by code on the visible text.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_structured_prices_are_read_without_a_model tests/test_watch.py::test_a_page_without_structure_asks_the_model_once tests/test_watch.py::test_a_phrase_is_found_by_code tests/test_watch.py::test_messy_structured_data_is_still_read tests/test_watch.py::test_quotes_read_minor_units_and_unknown_symbols && echo EXTRACT_OK
  CWD: bridge
  EXPECT: EXTRACT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=5 passed in 0.02s | EXTRACT_OK

- [x] G5: Conditions fire on the edge: below/above fire once on crossing and re-arm after going back; drop/rise percent fire against the baseline and move the baseline; available fires when out-of-stock turns in-stock; change fires on each new value; nothing fires on a first reading the owner was shown (an unshown one may: WatchTicketmaster's fix).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_conditions_fire_on_the_edge_and_rearm && echo EDGE_OK
  CWD: bridge
  EXPECT: EDGE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=1 passed in 0.75s | EDGE_OK

- [x] G6: The store and the guards: watches survive a restart (written atomically), a watch cap refuses the one past it, a URL to a private, loopback or link-local address or a non-http scheme is refused, and an interval below the kind's floor is raised to it.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_watches_survive_a_restart tests/test_watch.py::test_guards_refuse_private_urls_and_raise_short_intervals && echo STORE_OK
  CWD: bridge
  EXPECT: STORE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.03s | STORE_OK

- [x] G7: The scheduler: a due watch is checked, a fired condition texts the owner once through the notify it was lent, a blocked page (403) falls back to a search check, and repeated errors back the watch off and tell the owner once.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_a_due_watch_fires_and_texts_once tests/test_watch.py::test_a_blocked_page_falls_back_to_search tests/test_watch.py::test_repeated_errors_back_off_and_tell_once && echo SCHEDULER_OK
  CWD: bridge
  EXPECT: SCHEDULER_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed in 0.03s | SCHEDULER_OK

- [x] G8: The chat: with a watcher lent the three watch tools and the watch block are offered (and not without one); watch_add runs a first check and returns the reading; /watches is answered by code with no model call and is in the / menu.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py::test_the_watcher_is_offered_and_adds_from_the_chat tests/test_telegram.py::test_the_menu_offers_only_commands_the_dispatch_knows "tests/test_telegram.py::test_each_menu_command_is_accepted_as_the_menu_sends_it[watch]" && echo CHAT_OK
  CWD: bridge
  EXPECT: CHAT_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed in 0.11s | CHAT_OK

- [x] G9: Live (depends on real sites; a site change can fail it — step 4a failed once to a late LEGO modal before the overlay fix): a real Yahoo quote through the limiter; a real shop page from its own data with no model call; a real 403 shop read through headless Chromium with no model call; the vision model reads that shop's price off its screenshot and calls a real bot check blocked.
  CHECK: .venv/bin/python tools/watch_smoke.py
  CWD: bridge
  EXPECT: WATCH_SMOKE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4b. vision saw the bot check and called it blocked (the watch would move to search) | WATCH_SMOKE_OK

- [x] G10: The docs say it: watch.md states the switch line (CC_BUDDY_WATCH on by default), each limiter default with its value (12 per minute, burst 4, 20 s per host, 60 s doubling to 1 h, 48 model calls a day), the four kinds, and Retry-After; telegram.md lists /watch; README links the watch doc; verification.md lists the eight watcher models.
  CHECK: node -e "const f=require('fs');const w=f.readFileSync('docs/stackchan/watch.md','utf8');const t=f.readFileSync('docs/stackchan/telegram.md','utf8');const r=f.readFileSync('README.md','utf8');const v=f.readFileSync('docs/verification.md','utf8');const need=['\x60CC_BUDDY_WATCH\x60 is **on** by default','| \x60CC_BUDDY_WATCH_RATE\x60 | 12 per minute |','| \x60CC_BUDDY_WATCH_BURST\x60 | 4 |','| \x60CC_BUDDY_WATCH_HOST_GAP\x60 | 20 s |','60 s, doubling, capped at 1 h','| \x60CC_BUDDY_WATCH_MODEL_CALLS\x60 | 48 per day |','| \x60quote\x60 |','| \x60page\x60 |','| \x60search\x60 |','| \x60ticketmaster\x60 |','Retry-After'];for(const x of need) if(!w.includes(x)) throw new Error('watch.md lacks '+x);if(!/\x60\/watch\x60/.test(t)) throw new Error('telegram.md lacks /watch');if(!r.includes('docs/stackchan/watch.md')) throw new Error('README lacks link');for(const m of ['WatchLimiter','WatchConditions','WatchScheduler','WatchTicketmaster','WatchStarve','WatchSsrf','WatchLink','WatchRoute']) if(!v.includes('Buddy/'+m+'.lean')) throw new Error('verification.md lacks '+m);console.log('DOCS_OK')"
  CWD: .
  EXPECT: DOCS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=574d30059456/19 entries; output=DOCS_OK

- [x] G11: Vision: a JS-only page is rendered, its screenshot goes to the vision model as an image, the page is read in the browser from then on (no plain fetch, no text model), rendered JSON-LD needs no model; a page refused by fetch and browser falls to search; without a browser the browser is never called.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_a_js_page_is_rendered_then_seen tests/test_watch.py::test_a_refused_page_tries_the_browser_then_search && echo VISION_OK
  CWD: bridge
  EXPECT: VISION_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.02s | VISION_OK

- [x] G12: Ticketmaster: statuses follow Ticketmaster's own definitions (onsale/rescheduled/open presale available; offsale before its sale watched; postponed/canceled/ended not), no price means None never 0, the artist resolves to the non-tribute attraction and only its events count, a hex link id is matched by url and never fetched as an API id, price conditions are refused, and without a key the kind is not offered.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch.py::test_ticketmaster_statuses_follow_its_own_definitions tests/test_watch.py::test_ticketmaster_resolves_the_artist_and_matches_links && echo TM_OK
  CWD: bridge
  EXPECT: TM_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed in 0.02s | TM_OK

- [x] G13: Lean: every model in verification/Buddy (the six earlier ones and the watcher's) compiles with no sorry, no native_decide and only the standard axioms, and each proves current_violates + fixed_invariant or code_invariant.
  CHECK: node check-all.mjs
  CWD: verification
  EXPECT: ALL_MODELS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/verification; path=574d30059456/19 entries; output=LEAN_CHECK_OK Buddy/WatchTicketmaster.lean (36 theorems) | ALL_MODELS_OK 199 theorems

- [x] G14: Every replay the Lean pass and the reviews wrote passes on the fixed code: the limiter, conditions, scheduler and Ticketmaster replays, the security replays and the shape replays (first pass: 17 Lean replays and 18 review replays were run on the code as reviewed and failed, this session's pytest output; second pass: test_watch_reverify.py, whose scenarios the re-verification agents reproduced on the code they reviewed with their own probes, not re-run here against that code).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch_lean_limiter.py tests/test_watch_lean_conditions.py tests/test_watch_lean_scheduler.py tests/test_watch_lean_ticketmaster.py tests/test_watch_security.py tests/test_watch_shapes_replay.py tests/test_watch_reverify.py && echo REPLAYS_OK
  CWD: bridge
  EXPECT: REPLAYS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=77 passed, 3 skipped in 13.95s | REPLAYS_OK

- [x] G15: Live, a real headless Chromium behind the guard proxy: a page's redirect, sub-resource redirect, iframe, fetch, beacon, WebSocket and popup never reach a private address, while the page's own asset does load (positive control). Negative control (runnable): the same page with the guard letting everything through reaches the LAN. An overlay button that navigates is undone; a hung render is killed with its whole process tree.
  CHECK: CC_BUDDY_LIVE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_watch_security.py::test_the_render_guard_covers_redirects_websockets_and_popups tests/test_watch_security.py::test_a_render_that_never_answers_is_killed_at_its_deadline tests/test_watch_reverify.py::test_negative_control_the_guard_test_sees_a_leak_when_the_guard_is_open tests/test_watch_reverify.py::test_an_overlay_button_that_navigates_is_undone tests/test_watch_reverify.py::test_a_render_that_hangs_is_killed_with_everything_it_started && echo GUARD_OK
  CWD: bridge
  EXPECT: GUARD_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=5 passed in 16.97s | GUARD_OK
