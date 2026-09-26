# Gates: Meet — buddy joins a Google Meet call, listens, and texts the notes

OWNS: bridge/src/cc_buddy_bridge/meet.py, bridge/src/cc_buddy_bridge/meet_page.js, bridge/tests/test_meet.py, bridge/tests/test_meet_page.py, bridge/src/cc_buddy_bridge/telegram.py, bridge/tests/test_telegram.py, bridge/src/cc_buddy_bridge/voice_agent.py, bridge/tests/test_voice_agent.py, bridge/src/cc_buddy_bridge/daemon.py, bridge/src/cc_buddy_bridge/spend.py, bridge/tools/meet_smoke.py, docs/stackchan/meet.md, docs/stackchan/telegram.md, README.md, GATES.md

Scope: The owner asks buddy — `/meet <link>` on Telegram, "join my 3pm" in the chat (the link found on the owner's calendar), or out loud — to join a Google Meet call. Buddy opens its own tab in the owner's Chrome (attach mode, the owner's Google account), turns the microphone and the camera off and verifies both are off before it presses Join, presses only "Join now", "Ask to join" or "Join here too" (never "Switch here", which would take the call away from the owner's other device), waits in the lobby while telling the owner once, reads Meet's live captions with speaker names, keeps them in a transcript as they settle, and when the call ends or the owner says leave writes the notes into transcripts/meetings and texts a summary. Listen only, permanently: no code path unmutes, speaks or turns a camera on. The page scripts are a port of OpenClaw's google-meet plugin (MIT). The previous ledger (the watcher) is in git at 2e52bb8. No type-checker is configured for the bridge (pyproject has ruff and pytest only).

- [x] G1: The whole bridge test suite passes.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_desktop_live.py && echo PYTEST_OK
  CWD: bridge
  EXPECT: PYTEST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3112 passed, 13 skipped in 493.44s (0:08:13) | PYTEST_OK

- [x] G2: Ruff reports nothing on src, tests and tools.
  CHECK: .venv/bin/ruff check src/ tests/ tools/ && echo RUFF_CLEAN
  CWD: bridge
  EXPECT: RUFF_CLEAN
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=All checks passed! | RUFF_CLEAN

- [x] G3: Only a Google Meet link is joined: meet.google.com/<abc-defg-hij> (with or without https, a lookup path, authuser kept) is normalised with hl=en; any other host, a lookalike host, a non-https scheme or a bare word is refused with a reason.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_meet.py -k "link" && echo LINKS_OK
  CWD: bridge
  EXPECT: LINKS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=21 passed, 38 deselected in 0.03s | LINKS_OK

- [x] G4: The join decision, run by the real page script in real Chromium on fixture pages: with the mic or camera on, the script turns them off and does not press Join in that pass; with both off it presses Join now / Ask to join; offered "Join here too" and "Switch here" it presses only Join here too (positive control: the Switch here button's click counter stays 0 while Join here too's is 1); an in-call page with the mic on is muted again.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_meet_page.py && echo PAGE_OK
  CWD: bridge
  EXPECT: PAGE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=14 passed in 2.40s | PAGE_OK

- [x] G5: The session's state machine against a scripted page: lobby tells the owner once and keeps waiting up to the lobby limit; admitted turns into in-call; denied, removed, meeting ended, the tab closed, a sign-in page and a join timeout each end the session with a reason the owner is told; "leave" presses Leave and closes only buddy's tab.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_meet.py -k "session" && echo SESSION_OK
  CWD: bridge
  EXPECT: SESSION_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=14 passed, 45 deselected in 0.80s | SESSION_OK

- [x] G6: Captions become a transcript: a growing line is one line, not many; a line replaced by a non-prefix commits; buddy's own tile is never transcribed; the transcript file is appended as lines settle (a crash mid-call keeps what was heard); at the end the notes are written under transcripts/meetings/<date>/ with speakers kept, and the summary is texted; an empty call texts that nothing was said and makes no model call.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_meet.py -k "caption or notes" && echo NOTES_OK
  CWD: bridge
  EXPECT: NOTES_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=11 passed, 48 deselected in 0.03s | NOTES_OK

- [x] G7: The calendar finds the link: of the owner's events, the one in progress or starting within the window with a Meet link is chosen ("now", "next", a time like 3pm); an event without a Meet link is skipped; no match is a plain reason.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_meet.py -k "calendar" && echo CALENDAR_OK
  CWD: bridge
  EXPECT: CALENDAR_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=15 passed, 44 deselected in 0.02s | CALENDAR_OK

- [x] G8: Telegram: `/meet <link>` joins by code with no model call, relay on or off; bare `/meet` answers the status by code; `/meet leave` leaves; "join my 3pm" is a model turn offered the meet tools, and its meet_join call reaches the session; the lobby and the end are texted to the owner.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_telegram.py -k "meet" && echo TELEGRAM_MEET_OK
  CWD: bridge
  EXPECT: TELEGRAM_MEET_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=4 passed, 217 deselected in 0.16s | TELEGRAM_MEET_OK

- [x] G9: Voice: the live session is offered join_meeting and leave_meeting; "join my meeting" calls join_meeting, which reaches the same Meeter (link or calendar); with no Meeter the tool says why.
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_voice_agent.py -k "meeting" && echo VOICE_MEET_OK
  CWD: bridge
  EXPECT: VOICE_MEET_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=3 passed, 104 deselected in 0.04s | VOICE_MEET_OK

- [x] G10: Listen only, by construction: no source line in meet.py or meet_page.js presses an unmute or camera-on control, and the page script's only clicks are the named safe ones (checked by the page test's click log across every fixture).
  CHECK: .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_meet_page.py -k "only_safe" && echo LISTEN_ONLY_OK
  CWD: bridge
  EXPECT: LISTEN_ONLY_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge; path=574d30059456/19 entries; output=2 passed, 12 deselected in 0.30s | LISTEN_ONLY_OK

- [x] G11: Live: buddy joins a real Meet in the owner's Chrome, with the mic and camera off, reads at least one caption line, leaves, and writes the notes file (bridge/tools/meet_smoke.py against a meeting the owner opens).
  EVIDENCE: manual, 2026-09-25 17:41 and 17:48 PT, `tools/meet_smoke.py 6pm` against the owner's real 6pm "Meeting" (found on the personal calendar through Composio). Both runs printed MEET_SMOKE_OK (in the call, lines > 0, notes file written): run 1 lines=4, run 2 lines=3; click log both times exactly ['mic-off', 'camera-off', 'join', 'captions-on']; a screenshot at 17:42 showed the mic and camera icons crossed out and captions on. Run 2's texted summary named the decision ("ship the deck on Friday") and the speaker. Run 1 exposed the real caption markup and run 2 Meet's rewrites: both fixed after, with regression tests (test_meet_page.py real-markup fixture; test_meet.py live revision sequence). Not seen live: "Join here too"; the post-fix parser was checked against the captured live caption HTML, not a third live call.

- [x] G12: The docs say what shipped: docs/stackchan/meet.md exists and names /meet, the listen-only rule and "Join here too"; telegram.md lists /meet; the README links meet.md.
  CHECK: node -e "const f=require('fs');const m=f.readFileSync('docs/stackchan/meet.md','utf8');const t=f.readFileSync('docs/stackchan/telegram.md','utf8');const r=f.readFileSync('README.md','utf8');if(!/\/meet/.test(m)||!/Join here too/.test(m)||!/listen/i.test(m)||!/\/meet/.test(t)||!/meet\.md/.test(r))process.exit(1);console.log('DOCS_OK')"
  EXPECT: DOCS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=574d30059456/19 entries; output=DOCS_OK
