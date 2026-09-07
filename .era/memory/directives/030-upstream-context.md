---
category: upstream-context
title: Context Management
order: 30
---

## Context Management

### Context Decay
After 10+ messages in a conversation, re-read any file before editing it. Auto-compaction silently summarizes earlier context — do not trust memory of file contents from earlier in the session.

### File Read Budget
File reads are capped at 2,000 lines. For files over 500 LOC, use offset and limit to read in chunks. Never assume a single read captured the complete file.

### Tool Result Truncation
Large tool results are silently truncated. If a search returns suspiciously few results, re-run with a narrower scope or different query. State when truncation is suspected.

### Context Handover

The Era handover system automatically manages context window health. At ~30% context utilization, the message history is trimmed and a structured handover is injected into the system prompt. When this happens:

1. Read the handover document completely before acting
2. Re-read all files listed in "Required Reading"
3. Do not trust memory of previous tool outputs — re-run commands if needed
4. Ask the user for clarification if the handover is ambiguous

Manual handovers can be triggered with `/era-handover`.

### Cleanup Before Refactoring
Before structural refactors on files >300 LOC, remove dead code (unused imports, exports, props, debug logs). Commit cleanup separately so it can be reverted independently.
