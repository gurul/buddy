"""Suite-wide pytest configuration.

Live tests touch real services on this Mac: the owner's claude-mem worker (a real observation is written,
then deleted) or a real Chromium. They run only when asked, with CC_BUDDY_LIVE=1, so a plain `pytest` —
every verification run — never writes to the owner's memory store.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("CC_BUDDY_LIVE", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    skip = pytest.mark.skip(reason="live test: set CC_BUDDY_LIVE=1 to run it against this Mac's real services")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
