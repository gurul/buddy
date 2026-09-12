---
category: upstream-quality
title: Quality Patterns
order: 50
---

## Quality Patterns

### Follow References Over Descriptions
When existing code is pointed to as a reference, study it and match patterns exactly. Working code is a better spec than an English description.

### Bug Autopsy
After fixing a bug, explain WHY it happened and whether a guardrail could prevent that category of bug in the future.

### Failure Recovery
If a fix doesn't work after two attempts, stop. Re-read the entire relevant section top-down, identify where the mental model was wrong, and say so. Do not spiral into increasingly desperate patches.

### Demand Evidence
When debugging, work from raw error output. Do not guess or chase theories without data. If a bug report has no error output, ask for it before proceeding.
