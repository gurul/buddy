---
category: upstream-time-awareness
title: Time-Aware Scoping
order: 45
---

## Time-Aware Scoping

LLMs inherit human estimation bias from training data. Engineering tickets and post-mortems systematically underestimate, so when a plan says "2-day task" you tend to pace yourself accordingly even when the underlying work is hours. Counter this by grounding pace in real elapsed time, not in inherited expectations.

The `era-code time` CLI is the tool. Use it.

### Canonical step-name vocabulary

Step names are a **closed enum**. Use exactly these names — synonyms (`investigate`, `code`, `qa`) break aggregation across runs and the pacing-signal heuristics.

| Step name | When to use |
|---|---|
| `research` | External docs, library lookups, technology-researcher delegation |
| `evaluate` | Approach evaluation, ADR production, solutions-architect delegation |
| `implement` | Code changes — split into `implement-<scope>` for multi-domain work |
| `test` | Test writing and test runs |
| `review` | Reviewer delegation cycles |
| `validate` | code-validation skill — type-check, lint, full test suite |
| `preview` | ui-preview-generator output |

Multi-domain implementation can suffix the step name (e.g., `implement-handler`, `implement-ui`) — the prefix must match the enum.

### When you produce a plan
After producing a plan with discrete steps, record per-step time estimates immediately:

```bash
era-code time plan --steps '[
  {"name":"investigate-auth","estimate_minutes":45},
  {"name":"implement-handler","estimate_minutes":90},
  {"name":"write-tests","estimate_minutes":60}
]'
```

Estimates are best-effort honest — predict what an experienced engineer would actually take, not what a JIRA ticket would claim. They are priors, not commitments. Recording a plan resets the event log.

### When you execute a step
Mark the boundaries:

```bash
era-code time start <step-name>
# ... do the work ...
era-code time finish <step-name>
```

Step names must match the names recorded in the plan exactly.

### Before each step decision
Query status:

```bash
era-code time status
```

The output reports per-step actual vs estimated, total elapsed vs total estimated, and a pacing signal. Read the signal and act on it:

- **Running far ahead (≥3× faster):** Consolidate remaining steps, expand scope, or skip checkpoints sized for a slower trajectory. Confidence is justified by actual evidence, not by the original estimate. Do not pace yourself to a budget that the data has invalidated.
- **On pace (within ~2× either direction):** Continue as scoped.
- **Running long (≥2× slower):** Pause. Re-read the spec, identify where the mental model was wrong, and surface it before continuing. Do not silently extend.

The point is calibration, not bookkeeping. The wall-clock data exists so you can update your priors mid-run, not so you can produce a timesheet.
