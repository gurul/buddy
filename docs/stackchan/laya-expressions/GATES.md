# Gates: live Laya expressions on Buddy

Scope: Run local Laya on live conversation/diary events and render temporary eye cues. The owner revised the request to eyes only after trying the initial chirps: original sound behavior must be restored. Enable on this Mac, flash Buddy, verify the actual model-to-device path, and push to main. Model accuracy limits remain documented.

- [ ] G1: Host service uses real model results asynchronously, bounds pending work, drops stale results, preserves original caption chirps and mute, and consumes live voice/diary events; regression tests pass.
  CHECK: bridge/.venv/bin/python -m pytest -q bridge/tests/test_live_expressions.py bridge/tests/test_voice_agent.py bridge/tests/test_emotion_policy.py bridge/tests/test_sound.py && echo LIVE_HOST_OK
  EXPECT: LIVE_HOST_OK
  EVIDENCE: pending

- [ ] G2: Firmware expression arbitration validates labels, rejects duplicate events, expires cues, restores base states, and protects listening/attention.
  CHECK: c++ -std=c++17 -Wall -Wextra -Werror firmware/claude_pet_stackchan/host/expression_test.cpp -o /tmp/buddy-expression-test && /tmp/buddy-expression-test
  EXPECT: EXPRESSION_FIRMWARE_OK
  EVIDENCE: pending

- [ ] G3: Changed Python files pass lint, firmware builds and uploads, and the bridge reconnects to the flashed revision.
  EVIDENCE: pending

- [ ] G4: With the actual local model enabled, a fresh event reaches the board; telemetry confirms eye-overlay application, expiry, suppression while listening, no Laya chirps, and an unchanged mute setting.
  CHECK: bridge/.venv/bin/python bridge/tools/expression_live.py --check
  EXPECT: LIVE_LAYA_DEVICE_OK
  EVIDENCE: pending

- [ ] G5: README and docs describe live behavior, controls, model location, known accuracy limits and measured verification. Authorized changes are pushed to main.
  EVIDENCE: pending
