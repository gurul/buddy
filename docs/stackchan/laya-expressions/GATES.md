# Gates: live Laya expressions on Buddy

Scope: Run local Laya on live conversation/diary events, send bounded expression commands, render them during speaking and idle, enable on this Mac, flash Buddy, and verify the real model-to-device path. Previous model accuracy failures remain documented; activation is the owner's explicit experimental choice.

- [x] G1: Host service uses real model results asynchronously, bounds pending work, drops stale results, preserves mute, and consumes live voice/diary events; regression tests pass.
  CHECK: bridge/.venv/bin/python -m pytest -q bridge/tests/test_live_expressions.py bridge/tests/test_voice_agent.py bridge/tests/test_emotion_policy.py && echo LIVE_HOST_OK
  EXPECT: LIVE_HOST_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=07a05fc4f0d2/21 entries; output=108 passed in 23.99s | LIVE_HOST_OK

- [x] G2: Firmware expression arbitration validates labels, rejects duplicate events, expires cues, restores base states, and protects listening/attention and chirp cooldowns.
  CHECK: c++ -std=c++17 -Wall -Wextra -Werror firmware/claude_pet_stackchan/host/expression_test.cpp -o /tmp/buddy-expression-test && /tmp/buddy-expression-test
  EXPECT: EXPRESSION_FIRMWARE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=07a05fc4f0d2/21 entries; output=EXPRESSION_FIRMWARE_OK

- [x] G3: Changed Python files pass lint, firmware builds and uploads, and the bridge reconnects to the flashed revision.
  EVIDENCE: Changed Python lint passed. StackChan compile used 791787 bytes flash and 142624 bytes static RAM; USB upload to 80:45:6b:54:7f:44 verified all hashes. Daemon restarted and board telemetry reported b4d2b96-dirty. Clean committed build and final device check follow.

- [x] G4: With the actual local model enabled, a fresh event reaches the board; telemetry confirms eye-overlay application and chirp arbitration, then expiry, including a silent muted event and suppression while listening.
  CHECK: bridge/.venv/bin/python bridge/tools/expression_live.py --check
  EXPECT: LIVE_LAYA_DEVICE_OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy; path=07a05fc4f0d2/21 entries; output=] | LIVE_LAYA_DEVICE_OK

- [ ] G5: README and docs describe live behavior, controls, model location, known accuracy limits and measured verification. Authorized changes are pushed to main.
  EVIDENCE: pending
