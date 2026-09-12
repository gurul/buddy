---
category: upstream-context-sync
title: Session Readiness & Catch-Up
order: 5
---

## Session Readiness & Catch-Up

After the user's first prompt of a session, before doing task-specific work, perform a **session readiness check**. This catches stale state from overnight timezone handoffs and verifies that the tools needed for the task are available before the agent starts spending tokens on dead-end paths.

### When to run

Run readiness only when **all** of these are true:

1. The current message is the first task prompt of the session.
2. The task is non-trivial (use the same heuristic as orchestration's "When to Skip the Pipeline" — typos, single-line config changes, and doc-only changes skip).
3. Either the user requested catch-up explicitly, OR `era-code catch-up status --json` reports `is_stale: true` for the current repo.

If the task is trivial, or readiness already ran in this session, skip silently.

### What it does

1. **Tool readiness check.** For the task type, verify the relevant tools are available and authed:
   - Most tasks: `gh auth status` (PRs / GitHub access)
   - Tasks referencing Linear identifiers (e.g., `ERA-1234`): Linear MCP tools available
   - Production / incident tasks: observability MCP available — **critical**, block on failure

   Critical-tool failure → block with clear instructions. Non-critical failure → warn loudly and proceed without that source.

2. **Scoped catch-up.** Delegate to the `catch-up` persona via the Task tool with the user's task description as scoping context. The persona reads the registered-repo list (`era-code catch-up list --json`) and dispatches `codebase-explorer` per repo to assess overnight changes against the user's task.

3. **Brief presentation.** The persona returns a structured brief with per-repo rebase recommendations as **proposed actions**.

4. **User approval.** Present per-repo proposals. Approved actions are executed by the orchestration agent (which has edit/bash permissions); declined actions leave state unchanged.

### Plan-mode compliance

The catch-up flow is **read-only by default** in both plan mode and normal mode. All mutations are surfaced as proposed actions for explicit user approval:

- Information gathering (gh queries, Linear, codebase-explorer dispatch) → allowed
- Per-repo rebase recommendations → presented as a proposed plan
- `git fetch` / `git rebase` / branch updates → never executed by the catch-up persona; only by orchestration after approval
- `~/.era/catch-up-state.json` `last_check` updates → executed only after the user approves **and** the action completes successfully
- Optional handover note write to `.era/memory/handovers/` → also gated on approval

In plan mode, the user exits plan mode to execute approved actions. State (`last_check`) advances only on approval + successful action — rejected proposals leave the timestamp stale so the next session re-prompts.

### State

Per-machine state lives at `~/.era/catch-up-state.json` and is managed by the `era-code catch-up` CLI:

```bash
era-code catch-up register   # add the current repo to tracking
era-code catch-up status     # show staleness for this repo
era-code catch-up list       # all registered repos (use --json for the agent)
era-code catch-up unregister # remove the current repo
```

Repos are registered explicitly. Auto-discovery (walking `~/code/` or similar) is intentionally not supported — false positives are worse than false negatives here.

### Failure modes

- **No registered repos** → skip silently. If the user's task is non-trivial and cross-repo, suggest `era-code catch-up register` and continue without catch-up.
- **Network down / `gh` unauthed** → degrade gracefully. Brief on whatever sources work; clearly mark missing ones.
- **Readiness must never block `era-code start`.** A degraded readiness check is better than a blocked session.
