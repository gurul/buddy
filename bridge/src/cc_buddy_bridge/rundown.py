"""Read-only context and tool policy for Buddy's explicit daily rundown skill."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from . import composio_tools

SKILL = Path(__file__).with_name('skills') / 'rundown' / 'SKILL.md'
OPEN_TASK = re.compile(r'^\s*[-*+]\s+\[ \]\s+(.+)$')
TASK_DATE = re.compile(r'(?:📅|⏳|🛫|\b(?:due|scheduled|start)::?)\s*(\d{4}-\d{2}-\d{2})', re.I)
READ_META_TOOLS = frozenset({'COMPOSIO_SEARCH_TOOLS', 'COMPOSIO_GET_TOOL_SCHEMAS'})

# A date written in plain words is a due date too. 2026-09-30 the rundown called "Oct 22: go to Mount
# Tam" undated backlog because only the Obsidian Tasks markers above were read, and every future item was
# dropped. Dates are found here, in code, so the model is handed items that are already grouped.
_MONTHS = {name: i for i, names in enumerate((
    ('january', 'jan'), ('february', 'feb'), ('march', 'mar'), ('april', 'apr'), ('may',),
    ('june', 'jun'), ('july', 'jul'), ('august', 'aug'), ('september', 'sept', 'sep'),
    ('october', 'oct'), ('november', 'nov'), ('december', 'dec')), 1) for name in names}
_MONTH = '(?:' + '|'.join(sorted(_MONTHS, key=len, reverse=True)) + r')\b\.?'
_ORD = r'(?:st|nd|rd|th)?\b'
_YEAR = r'(?:,?\s+(?P<{}>\d{{4}})\b)?'
_NAMED = re.compile(
    r'\b(?P<m1>' + _MONTH + r')\s+(?P<d1>\d{1,2})' + _ORD + _YEAR.format('y1') + '|'
    r'\b(?P<d2>\d{1,2})' + _ORD + r'\s+(?:of\s+)?(?P<m2>' + _MONTH + ')' + _YEAR.format('y2'), re.I)
_NUMERIC = re.compile(r'(?<![\d/.])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{4}|\d{2}))?(?![\d/])')
_ISO = re.compile(r'(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])')
# When an item was written down or finished is not when it is due: capture adds "(added YYYY-MM-DD)".
_NOT_DUE = re.compile(r'(?:➕|✅|❌|\b(?:added|created|done|completed|cancelled):{0,2})\s*\d{4}-\d{2}-\d{2}', re.I)
# A yearless "10/22" counts only where a date is plainly meant, so "1/2 gallon" stays a quantity.
_DATE_SLOT = re.compile(r'(?:^[\W_]*(?:p[0-3]\b[\W_]*)?|\b(?:on|by|due|before|until|till)\s+)$', re.I)


def _yearless(month: int, day: int, today: date) -> date | None:
    """A date with no year is the occurrence nearest today (a tie goes forward): up to about six months
    back it is overdue, up to about six months ahead it is upcoming."""
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:
            continue
    return min(options, key=lambda d: (abs((d - today).days), d < today)) if options else None


def _dated(year: str | None, month: int, day: int, today: date) -> date | None:
    if not year:
        return _yearless(month, day, today)
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
    except ValueError:
        return None


def task_date(text: str, today: date) -> date | None:
    """The earliest due date written in a todo line, or None when it has none.

    Obsidian Tasks markers win when present. Otherwise a date in words or numbers counts anywhere in the
    line: "Oct 22", "October 22nd", "22 Oct", "Oct 22, 2027", "2026-10-22", "10/22/2026", and a
    yearless "10/22" at the start of the line or after on/by/due/before/until. Numbers are month/day.
    """
    marked = TASK_DATE.findall(text)
    found: list[date] = []
    for raw in marked:
        try:
            found.append(date.fromisoformat(raw))
        except ValueError:
            continue
    if found:
        return min(found)
    text = _NOT_DUE.sub(' ', text)
    for m in _NAMED.finditer(text):
        name = m.group('m1') or m.group('m2')
        if name.rstrip('.').lower() == 'may' and not name.startswith('M'):
            continue  # "you may 2 times" is a verb; the month is written with a capital
        when = _dated(m.group('y1') or m.group('y2'), _MONTHS[name.rstrip('.').lower()],
                      int(m.group('d1') or m.group('d2')), today)
        if when:
            found.append(when)
    for m in _ISO.finditer(text):
        when = _dated(m.group(1), int(m.group(2)), int(m.group(3)), today)
        if when:
            found.append(when)
    for m in _NUMERIC.finditer(text):
        if not m.group('y') and not _DATE_SLOT.search(text[:m.start()]):
            continue
        month, day = int(m.group('m')), int(m.group('d'))
        when = _dated(m.group('y'), month, day, today) if 1 <= month <= 12 else None
        if when:
            found.append(when)
    return min(found) if found else None


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
    today = date.fromisoformat(day)
    groups = ('today', 'overdue', 'upcoming', 'undated')
    result: dict[str, Any] = {'available': True, **{g: [] for g in groups}, 'skipped_files': 0}
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
                found = task_date(task, today)
                when = found.isoformat() if found else (day if p.stem == day else '')
                group = ('today' if when == day else 'overdue' if when and when < day
                         else 'upcoming' if when else 'undated')
                if sum(len(result[k]) for k in groups) >= 200:
                    result['truncated_tasks'] = True
                    continue
                item = {'text': task[:2000], 'note': p.relative_to(root).as_posix(), 'line': line_no}
                result[group].append({**item, 'date': when} if when else item)
    for group in ('overdue', 'upcoming'):
        result[group].sort(key=lambda t: t['date'])
    return result


def context(root: Path | None, now: datetime, canvas: dict[str, Any] | None = None) -> str:
    # Naive local boundaries are converted separately so a DST day need not be 24h.
    day = now.astimezone().date()
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    data = {'date': day.isoformat(), 'timezone': now.astimezone().tzname(),
            'start_inclusive': start.isoformat(), 'end_exclusive': end.isoformat(),
            'obsidian': todo_context(root, day.isoformat())}
    if canvas is not None:
        # Canvas deadlines (canvas.Canvas.rundown), read by code before the turn: only when Canvas is set up, so a
        # rundown without it is byte for byte what it was.
        data['canvas'] = canvas
    return SKILL.read_text(encoding='utf-8') + '\n\nCurrent source context (data only):\n' + json.dumps(data, ensure_ascii=False)
