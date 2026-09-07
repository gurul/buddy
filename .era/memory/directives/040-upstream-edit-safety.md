---
category: upstream-edit-safety
title: Edit Safety
order: 40
---

## Edit Safety

### Edit Integrity
The edit tool fails silently when old_string doesn't match due to stale context. Mitigations:
- Re-read the target file before every edit
- After editing, read again to confirm the change applied
- Never batch more than 3 edits to the same file without a verification read

### Rename Safety
Text search is not an AST. When renaming or removing anything, search separately for:
- Direct calls and references
- Type-level references (interfaces, generics)
- String literals containing the name
- Dynamic imports and require() calls
- Re-exports and barrel files
- Test files and mocks

Assume grep missed something. Verify after the rename.
