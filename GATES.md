# Gates: lesson whiteboard watermark

OWNS: bridge/web-canvas/src/index.css, bridge/web-canvas/e2e/board.e2e.mjs, bridge/src/cc_buddy_bridge/learning/web/canvas/**, README.md, docs/learning.md

Scope: Hide the lesson whiteboard watermark, rebuild the shipped assets, and verify the real lesson page.

- [x] G1: The canvas typechecks and builds with its self-hosted assets.
  CHECK: npm run build
  CWD: bridge/web-canvas
  EXPECT: tldraw assets copied to
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge/web-canvas; path=45bc8676a83f/19 entries; output=- Use build.rolldownOptions.output.codeSplitting to improve chunking: https://rolldown.rs/reference/OutputOptions.codeSplitting | - Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.

- [x] G2: The real browser hides the watermark on desktop, mobile, and reload while drawing, saving, tutor interaction, and legacy lessons still work under the CSP. Removing the override exposes the watermark as a positive control.
  CHECK: npm run e2e
  CWD: bridge/web-canvas
  EXPECT: board e2e passed
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/gurucharan/Documents/personal/buddy/bridge/web-canvas; path=45bc8676a83f/19 entries; output=> node e2e/board.e2e.mjs | board e2e passed

- [x] G3: README and lesson documentation describe the current presentation behavior.
  EVIDENCE: Reviewed README.md lesson step 2 and docs/learning.md Licence section against the scoped CSS override and passing browser test. Both describe the hidden watermark and prompt; the lesson guide documents the preserved licence text and key passthrough.
