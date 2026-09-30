# Gates: the stick link (M5StickS3 → iPhone → buddy)

OWNS: firmware/buddy_stick/**, tools/flash_stick.sh, tools/stick_link/**, ios/BuddyLink/**, bridge/src/cc_buddy_bridge/{stick_link,phone_call,miniapp,serial_transport,daemon,telegram,apps_maker}.py, bridge/tests/{test_stick_link,test_serial_skip,test_telegram,test_telegram_chief}.py, docs/stick-link.md, docs/stackchan/{miniapp,telegram}.md, README.md, GATES.md

Scope: the owner asked on 2026-09-30 for the M5StickS3 to connect to the iPhone, which talks to the daemon, "so i can talk to it from anywhere"; buddy's voice plays on the stick's speaker, and the phone learns the tunnel's changing address from a tap-to-update link in Telegram (option a). The stick is a Bluetooth LE push-to-talk button; the iPhone app relays its presses to the Mini App's `/api/call` WebSocket; the daemon accepts a revocable link token at its own door, `/api/stick`. The previous ledger (the chief of staff) is in git at origin/main. The bridge has no type-checker (ruff and pytest only); firmware has host C++ tests under sanitizers; the Swift codec has a host test.

## P1 The wire (codec and frames, the same in three languages)

- [x] G1.1: The Python reference codec round-trips speech-like audio within tolerance, and the committed vectors are what it produces now (regenerating changes nothing).
  CHECK: python3 tools/stick_link/test_adpcm.py && echo PY_CODEC_OK
  EXPECT: PY_CODEC_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>; path=8ae25fade4c8/16 entries; output=reference codec: all checks passed | PY_CODEC_OK

- [x] G1.2: The firmware's own codec and frame code, compiled for the Mac with address and undefined-behaviour sanitizers, matches every vector byte for byte and rejects malformed frames.
  CHECK: sh firmware/buddy_stick/test/run.sh && echo HOST_CPP_OK
  EXPECT: HOST_CPP_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>; path=8ae25fade4c8/16 entries; output=host tests: 8 vectors and all checks passed | HOST_CPP_OK

- [x] G1.3: The iPhone app's codec matches every vector byte for byte.
  CHECK: sh ios/BuddyLink/Tests/run.sh && echo SWIFT_CODEC_OK
  EXPECT: SWIFT_CODEC_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>; path=8ae25fade4c8/16 entries; output=swift codec: 8 vectors and all checks passed | SWIFT_CODEC_OK

## P2 The stick

- [x] G2.1: The firmware compiles for the StickS3 (8 MB flash, octal PSRAM, USB CDC on boot).
  CHECK: sh tools/flash_stick.sh --compile-only && echo STICK_COMPILE_OK
  EXPECT: STICK_COMPILE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>; path=8ae25fade4c8/16 entries; output=archived ELF for d93c39b-dirty | STICK_COMPILE_OK

- [ ] G2.2: Flashed, the stick advertises the link service and a Mac probe (standing in for the phone) connects, gets its hello, sends it one second of tone, and the stick reports that many samples played.
  CHECK: sh tools/stick_link/probe.sh tone
  EXPECT: STICK_PROBE_OK
  EVIDENCE: pending. Last run 2026-09-30 failed: "Looking for the stick… | STICK_PROBE_TIMEOUT". The serial log shows why: boot:0x21 DOWNLOAD(USB/UART0), the stick latched in download mode after the reset hold; it needs one short reset tap.

- [ ] G2.3: A press, held while the owner speaks, reaches the probe as audio loud enough to transcribe (peak above -30 dBFS). Manual: needs a voice.
  EVIDENCE: pending

- [ ] G2.4: Buddy's voice is clear on the stick's speaker. Manual: the owner's ears.
  EVIDENCE: pending

## P3 The daemon

- [x] G3.1: `/api/stick` accepts the link token as its first message and refuses a wrong, empty or rotated token, and any request with an Origin; `/api/call` still takes initData and never the token; the robot's port glob skips the serials in `CC_BUDDY_SERIAL_SKIP`; the `/stick` command and the tunnel-moved message send the tap-to-update link, and the token is never in a URL the server sees.
  CHECK: PYTHONPATH=src ../../buddy/bridge/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_stick_link.py tests/test_serial_skip.py && echo STICK_DAEMON_OK
  CWD: bridge
  EXPECT: STICK_DAEMON_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>/bridge; path=8ae25fade4c8/16 entries; output=20 passed in 1.86s | STICK_DAEMON_OK

- [x] G3.2: The whole bridge suite passes apart from failures that also fail on origin/main (named in EVIDENCE, each run there too), and ruff is clean.
  CHECK: ../../buddy/bridge/.venv/bin/ruff check src/ tests/ && PYTHONPATH=src ../../buddy/bridge/.venv/bin/python -m pytest -q -p no:cacheprovider --deselect tests/test_telegram_chief.py::test_off_the_self_context_block_is_byte_for_byte_origin_main --deselect tests/test_chief.py::test_privacy_spend_rows_carry_the_card_id_and_nothing_said --deselect tests/test_codex_computer.py::test_daemon_factory_routes_shared_task_contract_to_codex && echo BRIDGE_OK
  CWD: bridge
  EXPECT: BRIDGE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>/bridge; path=8ae25fade4c8/16 entries; output=4049 passed, 15 skipped, 3 deselected in 566.93s (0:09:26) | BRIDGE_OK. The 3 deselected fail on unmodified origin/main ecd966c too (run there 2026-09-30): test_telegram_chief::test_off_the_self_context_block_is_byte_for_byte_origin_main, test_chief::test_privacy_spend_rows_carry_the_card_id_and_nothing_said, test_codex_computer::test_daemon_factory_routes_shared_task_contract_to_codex

## P4 End to end through the Mac

- [ ] G4.1: With the Mac relay standing in for the phone, a press on the stick is heard by buddy (the daemon log shows `call: press`) and the reply plays on the stick. Manual: needs a voice.
  EVIDENCE: pending

## P5 The iPhone

- [x] G5.1: The Buddy Link app builds for a real iPhone.
  CHECK: sh ios/BuddyLink/build.sh && echo IOS_BUILD_OK
  EXPECT: IOS_BUILD_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>; path=8ae25fade4c8/16 entries; output=(xcodebuild's multiple-destination warning, the Mac's id redacted; build.sh now pins arm64) | IOS_BUILD_OK

- [ ] G5.2: The app is installed on the owner's iPhone.
  CHECK: D=$(xcrun devicectl list devices 2>/dev/null | awk '/physical/ && /iPhone/ {for (i=1;i<=NF;i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$/) print $i; exit}'); xcrun devicectl device info apps --device "$D" 2>&1 | grep -q com.github.cc-buddy-bridge.BuddyLink && echo IOS_INSTALLED_OK
  EXPECT: IOS_INSTALLED_OK
  EVIDENCE: pending

- [ ] G5.3: Paired from the Telegram link, with the phone locked and off home Wi-Fi, a press on the stick gets a spoken answer on the stick. Manual: the owner, away from the desk.
  EVIDENCE: pending

## P6 Docs

- [x] G6.1: docs/stick-link.md covers setup, pairing, the wire, the token, flashing and restoring UiFlow2; README links it.
  CHECK: node -e "const f=require('fs');const d=f.readFileSync('docs/stick-link.md','utf8');const r=f.readFileSync('README.md','utf8');for(const h of ['## Setting it up','## Pairing the phone','## The wire','## The link token','## Flashing','## Back to UiFlow2'])if(!d.includes(h))throw h;if(!r.includes('docs/stick-link.md'))throw 'readme';console.log('DOCS_OK')"
  EXPECT: DOCS_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=<repo>; path=8ae25fade4c8/16 entries; output=DOCS_OK
