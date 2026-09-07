---
category: upstream-execution
title: Execution Discipline
order: 20
---

## Execution Discipline

### Phased Execution
Never attempt multi-file refactors in a single pass. Break work into phases:
- Each phase touches no more than 5 files
- Complete the phase, verify, then proceed
- For tasks touching >5 independent files, use parallel sub-agents (5-8 files each)

### Plan and Build Are Separate
When asked to plan, output ONLY the plan. When given a plan, follow it exactly. If you spot a problem mid-build, flag it and wait — don't improvise.

### One-Word Confirmations
When the user says "yes," "do it," or "go" — execute immediately. Do not repeat the plan or add preamble. Context is loaded; the message is just the trigger.
