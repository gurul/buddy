"""Read-only context and tool policy for Buddy's explicit daily rundown skill."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from . import composio_tools

SKILL = Path(__file__).with_name('skills') / 'rundown' / 'SKILL.md'
OPEN_TASK = re.compile(r'^\s*[-*+]\s+\[ \]\s+(.+)$')
TASK_DATE = re.compile(r'(?:📅|⏳|🛫|\b(?:due|scheduled|start)::?)\s*(\d{4}-\d{2}-\d{2})', re.I)
READ_META_TOOLS = frozenset({'COMPOSIO_SEARCH_TOOLS', 'COMPOSIO_GET_TOOL_SCHEMAS'})


# Asking for today's plan is asking for the rundown: the calendar is most of a day's plan. "What is my
# plane today" (2026-09-24 08:44) missed the word "rundown", took the vault-only plan and a 20 s second
# model, and came back with no calendar. Whole-text matches only, each anchored on today or "my day":
# "plan a trip", "today's news" and "what's on netflix today" stay ordinary turns.
_WHAT_IS = r"(?:what(?:'s| is|s)|whats)"
_PLAN = r"(?:plan|plane|plans|schedule|agenda|calendar)"
_DAILY = re.compile(r"(?:(?:hey|hi|ok|okay|so)\s+)?(?:buddy[,:]?\s+)?(?:" + "|".join((
    r"/?rundown",
    _WHAT_IS + r" (?:my|the) " + _PLAN + r"(?: like)? (?:for |on )?(?:today|the day)",
    _WHAT_IS + r" today(?:'s|s)? " + _PLAN,
    r"(?:my |the )?today(?:'s|s)? " + _PLAN,
    r"(?:my |the )?" + _PLAN + r" (?:for )?today",
    r"(?:(?:please|can you|could you|help me) )?plan (?:out )?(?:my day|the day|today)(?: today)?(?: please)?",
    _WHAT_IS + r" on (?:for |my calendar |my schedule |my agenda )?today",
    r"what (?:do|have) i (?:got |have )?(?:on |going on |planned |to do )?(?:for )?today",
    _WHAT_IS + r" my day (?:look )?like(?: today)?",
    r"(?:what does|how does) (?:my day|today) look(?: like)?(?: today)?",
)) + r")")


def matches(text: str) -> bool:
    said = re.sub(r"\s+", " ", text.replace("\u2019", "'").strip().lower()).rstrip(".!? ")
    return _DAILY.fullmatch(said) is not None


def allows(name: str, args: dict[str, Any]) -> bool:
    if name in READ_META_TOOLS:
        return True
    if name != composio_tools.MULTI_EXECUTE:
        return False
    entries = args.get('tools')
    return bool(isinstance(entries, list) and entries and all(
        isinstance(t, dict) and composio_tools.is_read_only(str(t.get('tool_slug', '')))
        and composio_tools.slug_words(str(t.get('tool_slug', '')))[0] in ('gmail', 'googlecalendar', 'outlook', 'slack')
        for t in entries))


def todo_context(root: Path | None, day: str) -> dict[str, Any]:
    if root is None or not root.is_dir():
        return {'available': False, 'reason': 'Obsidian vault is not configured or not present'}
    root = root.resolve()
    result: dict[str, Any] = {'available': True, 'today': [], 'overdue': [], 'undated': [], 'skipped_files': 0}
    count = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '09-archive'
                         and not (Path(folder) / d).is_symlink())
        for name in sorted(files):
            p = Path(folder) / name
            if not name.endswith('.md') or name.startswith('.') or p.is_symlink():
                continue
            count += 1
            if count > 5000:
                result['skipped_files'] += 1
                continue
            try:
                if not p.resolve().is_relative_to(root) or p.stat().st_size > 1024*1024:
                    result['skipped_files'] += 1
                    continue
                text = p.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                result['skipped_files'] += 1
                continue
            fence = None
            for line_no, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith(('```', '~~~')):
                    marker = stripped[:3]
                    fence = None if fence == marker else (marker if fence is None else fence)
                    continue
                match = OPEN_TASK.match(line) if fence is None else None
                if not match:
                    continue
                task = match.group(1)
                dates = TASK_DATE.findall(task)
                when = min(dates) if dates else (day if p.stem == day else '')
                if when > day:
                    continue
                group = 'today' if when == day else ('overdue' if when else 'undated')
                if sum(len(result[k]) for k in ('today', 'overdue', 'undated')) >= 200:
                    result['truncated_tasks'] = True
                    continue
                result[group].append({'text': task[:2000], 'note': p.relative_to(root).as_posix(), 'line': line_no})
    return result


def context(root: Path | None, now: datetime) -> str:
    # Naive local boundaries are converted separately so a DST day need not be 24h.
    day = now.astimezone().date()
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    data = {'date': day.isoformat(), 'timezone': now.astimezone().tzname(),
            'start_inclusive': start.isoformat(), 'end_exclusive': end.isoformat(),
            'obsidian': todo_context(root, day.isoformat())}
    return SKILL.read_text(encoding='utf-8') + '\n\nCurrent source context (data only):\n' + json.dumps(data, ensure_ascii=False)
