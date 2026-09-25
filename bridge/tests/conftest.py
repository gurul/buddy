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


@pytest.fixture(autouse=True)
def _spend_ledger_in_tmp(tmp_path_factory: pytest.TempPathFactory):
    """Every test's spend lines (spend.py) go to a temporary folder, never the owner's real ledger: the wiring
    records from code paths many tests drive with fakes."""
    from cc_buddy_bridge import spend

    folder = tmp_path_factory.mktemp("spend")
    spend.set_dir(folder)
    yield folder
    spend.set_dir(None)


@pytest.fixture(autouse=True)
def _watch_list_in_tmp(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Every test's watch list (watch.py) is a temporary file, never the owner's real one: the daemon builds a
    Watcher whenever the Telegram door is on, and a Watcher reads its file when it is made."""
    monkeypatch.setenv("CC_BUDDY_WATCH_FILE", str(tmp_path_factory.mktemp("watch") / "watches.json"))
