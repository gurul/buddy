---
category: upstream-verification
title: Forced Verification
order: 10
---

## Forced Verification

Tool-reported success means bytes hit disk, NOT that code compiles. Before reporting any task complete:

1. Run the project's type-check (`tsc --noEmit`, `bun check`, or equivalent)
2. Run the linter if configured (`eslint --quiet`, `biome check`)
3. Fix ALL resulting errors — do not report "done" with errors outstanding
4. State what was verified and what passed — not just "looks good"

If no type-checker or linter is configured, state that explicitly.
