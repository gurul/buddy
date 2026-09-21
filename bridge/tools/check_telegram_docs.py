"""Gate: the Telegram door's docs say what the code reads.

The names are read out of telegram.py, not listed here, so a new CC_BUDDY_TELEGRAM* knob that nobody
documented fails this. Prints TELEGRAM_DOCS_OK only after every assertion holds.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "bridge/src/cc_buddy_bridge/telegram.py"
DOC = ROOT / "docs/stackchan/telegram.md"


def main() -> int:
    source, doc, readme = SOURCE.read_text(), DOC.read_text(), (ROOT / "README.md").read_text()
    names = sorted(set(re.findall(r"CC_BUDDY_TELEGRAM[A-Z_]*", source)))
    problems: list[str] = []
    if len(names) < 3:
        problems.append(f"found only {names} in telegram.py: the reader is broken, not the docs")
    problems += [f"{name} is read by telegram.py but not documented in {DOC.name}" for name in names
                 if not re.search(rf"`{name}`", doc)]
    if "telegram.py" not in readme or "docs/stackchan/telegram.md" not in readme:
        problems.append("README.md does not name telegram.py and link docs/stackchan/telegram.md")
    if "telegram-check" not in doc:
        problems.append("the setup command `cc-buddy-bridge telegram-check` is not in the doc")
    deps = tomllib.loads((ROOT / "bridge/pyproject.toml").read_text())["project"]["dependencies"]
    if re.search(r"^\s*import httpx|^\s*from httpx", source, re.M) and not any(d.startswith("httpx") for d in deps):
        problems.append("telegram.py imports httpx but pyproject.toml does not declare it")
    if not re.search(r"^TELEGRAM_DEFAULT = False\b", source, re.M):
        problems.append("TELEGRAM_DEFAULT is not False: the docs say it ships off")
    for line in problems:
        print("FAIL:", line)
    if problems:
        return 1
    print(f"{len(names)} names documented: {', '.join(names)}")
    print("TELEGRAM_DOCS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
