#!/usr/bin/env python3
"""Build apps with the REAL maker, end to end, and write down how each build went.

    .venv/bin/python tools/app_eval.py OUT "a habit tracker with daily check-ins and streaks" ["a tip splitter" ...]
    .venv/bin/python tools/app_eval.py OUT --edit habit-tracker "add a monthly calendar view"
    options: --effort high  --max-repairs 2  --model claude-opus-5-5  --jobs 1

Each request goes through apps_maker.AppMaker exactly as a build from the phone does: Claude
(``claude-opus-5-5``, the owner's ``ANTHROPIC_API_KEY`` from ``~/.config/cc-buddy-bridge/env``, never printed),
the static checks, the headless phone check (app_check.py) and up to ``--max-repairs`` repair rounds. The apps go
to ``OUT/apps`` (never the owner's real apps folder, so ``--edit`` changes an app an earlier run of this tool
built there) and each app's screenshot to ``OUT/screenshots/<slug>.jpg``.

``OUT/report.json`` gets one entry per build, appended to what earlier runs wrote: the request, whether it was
built, seconds, dollars, repair rounds, the check's summary and remaining issues, the screenshot's path, and the
token usage of every Claude call (so prompt caching can be seen working: repair rounds should read the prefix
from cache). This tool measures; whether an app is good is for a person to judge from the screenshot and by
opening ``OUT/apps/<slug>/index.html``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ENV_FILE = Path.home() / ".config" / "cc-buddy-bridge" / "env"
PASSED_ENV = ("ANTHROPIC_API_KEY", "CC_BUDDY_TIMEZONE", "CC_BUDDY_LOCATION")
# The Claude calls of the build running in this task, so concurrent builds (--jobs) keep their own.
CALLS: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar("calls")


def owner_env() -> dict[str, str]:
    """The key and the owner's clock/location settings: the shell's first, then the env file's. Never printed."""
    from cc_buddy_bridge.envfile import parse_env_file

    try:
        file = parse_env_file(ENV_FILE.read_text())
    except OSError:
        file = {}
    return {k: os.environ.get(k) or file.get(k, "") for k in PASSED_ENV}


def usage_row(usage: Any) -> dict[str, int]:
    def n(name: str) -> int:
        v = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(v or 0)

    return {k: n(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                              "cache_creation_input_tokens")}


async def build_one(maker: Any, out: Path, request: str, edit: str) -> dict[str, Any]:
    from cc_buddy_bridge import apps_maker

    stages: list[str] = []
    t0 = time.perf_counter()
    label = f"--edit {edit}" if edit else request[:60]

    def progress(stage: str, n: int) -> None:
        if not stages or stages[-1] != stage:
            stages.append(stage)
            extra = f" ({n} problem{'s' if n != 1 else ''})" if stage == "fixing" else ""
            print(f"  [{time.perf_counter() - t0:5.0f} s] {label}: {stage}{extra}", file=sys.stderr, flush=True)

    calls: list[dict[str, Any]] = []
    CALLS.set(calls)
    try:
        made = await (maker.edit(edit, request, progress) if edit else maker.make(request, progress))
    except Exception as e:  # noqa: BLE001 — one failed build is a row in the report, not the end of the run
        return {"request": request, "edit": edit, "built": False, "reason": f"{type(e).__name__}: {str(e)[:300]}",
                "seconds": round(time.perf_counter() - t0, 1), "calls": calls}
    row: dict[str, Any] = {
        "request": request, "edit": edit, "built": made.ok, "reason": made.reason,
        "seconds": round(made.secs, 1), "usd": round(made.usd, 4), "rounds": made.rounds,
        "check": made.check.summary() if made.check else "not checked",
        "check_seconds": round(made.check.secs, 1) if made.check else None,
        "issues": made.issues, "stages": stages, "calls": calls, "screenshot": None,
    }
    if made.app is not None:
        row.update({"slug": made.app.slug, "title": made.app.title, "icon": made.app.icon,
                    "description": made.app.description, "versions": made.app.versions,
                    "html": str(out / "apps" / made.app.slug / "index.html"),
                    "bytes": len((maker.store.html(made.app.slug) or "").encode()),
                    "static_issues": apps_maker.static_issues(maker.store.html(made.app.slug) or "")})
        if made.check is not None and made.check.screenshot:
            shot = out / "screenshots" / f"{made.app.slug}.jpg"
            shot.parent.mkdir(parents=True, exist_ok=True)
            shot.write_bytes(made.check.screenshot)
            row["screenshot"] = str(shot)
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="output folder: apps/, screenshots/ and report.json go here")
    ap.add_argument("requests", nargs="*", help="one request per app to build")
    ap.add_argument("--edit", nargs=2, metavar=("SLUG", "CHANGE"), help="change an app an earlier run built in OUT")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--max-repairs", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=1, help="builds at once")
    args = ap.parse_args()
    if not args.requests and not args.edit:
        ap.error("give at least one request, or --edit SLUG CHANGE")

    import anthropic

    from cc_buddy_bridge import apps_maker
    from cc_buddy_bridge.miniapp import cost_usd

    out = args.out.resolve()
    if out / "apps" == apps_maker.DEFAULT_ROOT.resolve() or out == apps_maker.DEFAULT_ROOT.resolve():
        ap.error("OUT would be the owner's real apps folder; pick another")
    env = owner_env()
    if not env["ANTHROPIC_API_KEY"]:
        print("No ANTHROPIC_API_KEY in the environment or in ~/.config/cc-buddy-bridge/env.", file=sys.stderr)
        return 2
    (out / "apps").mkdir(parents=True, exist_ok=True)

    real = apps_maker.claude_generate(args.model, args.effort,
                                      client=anthropic.AsyncAnthropic(api_key=env["ANTHROPIC_API_KEY"]))

    async def generate(messages: list[dict[str, Any]], progress: Any) -> Any:
        gen = await real(messages, progress)
        CALLS.get([]).append({**usage_row(gen.usage), "stop": gen.stop_reason})
        return gen

    spent: list[float] = []
    maker = apps_maker.AppMaker(apps_maker.AppStore(out / "apps"), generate,
                                cost=lambda u: cost_usd(args.model, u), spend=spent.append,
                                context=lambda: apps_maker.owner_context(env), max_repairs=args.max_repairs)
    jobs = [(r, "") for r in args.requests] + ([(args.edit[1], args.edit[0])] if args.edit else [])
    gate = asyncio.Semaphore(max(1, args.jobs))

    async def run(request: str, edit: str) -> dict[str, Any]:
        async with gate:
            return await build_one(maker, out, request, edit)

    t0 = time.perf_counter()
    rows = await asyncio.gather(*(run(r, e) for r, e in jobs))
    report_path = out / "report.json"
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        report = {"apps": []}
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    for row in rows:
        row.update({"at": stamp, "model": args.model, "effort": args.effort, "max_repairs": args.max_repairs})
    report["apps"] = list(report.get("apps", [])) + list(rows)
    report["total_usd"] = round(sum(a.get("usd", 0.0) or 0.0 for a in report["apps"]), 4)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    for row in rows:
        mark = "built" if row["built"] else "FAILED"
        print(f"{mark}: {row.get('icon', '')} {row.get('title', row['request'][:50])} in {row['seconds']} s, "
              f"${row.get('usd', 0):.3f}, {row.get('rounds', 0)} repair round(s), check {row.get('check', '-')}")
        for issue in row.get("issues", []):
            print(f"    - {issue}")
        if row.get("screenshot"):
            print(f"    screenshot: {row['screenshot']}")
        if not row["built"]:
            print(f"    reason: {row.get('reason')}")
    print(f"this run: ${sum(spent):.3f} in {time.perf_counter() - t0:.0f} s; report: {report_path}")
    return 0 if all(r["built"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
