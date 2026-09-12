"""WorkerClient against a real child process (fake_worker_child.py): ready and
observe, the restart-once-then-WorkerDead policy on a stuck or dead child,
the fail-safe close, the release seam and the large-line limit."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cc_buddy_bridge.computer_agent import FailSafe, WorkerClient, WorkerDead

FAKE_CHILD = Path(__file__).with_name("fake_worker_child.py")


class Bench:
    def __init__(self) -> None:
        self.released = 0
        self.client = WorkerClient(timeout_secs=0.5, python=sys.executable, release=self.release,
                                   args=(str(FAKE_CHILD),))

    async def release(self) -> None:
        self.released += 1


def test_start_reads_ready_and_observe_returns_items() -> None:
    async def go() -> None:
        b = Bench()
        ready = await b.client.start()
        try:
            assert ready["width"] == 100 and b.client.ready is ready
            items = await b.client.observe()
            assert [i["type"] for i in items] == ["input_image", "input_text"]
            assert items[1]["text"].startswith("frontmost: Warp")
            assert await b.client.execute("log(1)") == [{"type": "input_text", "text": "ran log(1)"}]
        finally:
            await b.client.close()
        assert b.client.proc is None and b.released == 1
    asyncio.run(go())


def test_timeout_restarts_once_then_dies() -> None:
    async def go() -> None:
        b = Bench()
        await b.client.start()
        pid = b.client.proc.pid
        out = await b.client.execute("hang")
        assert out[0]["type"] == "input_text" and "was restarted" in out[0]["text"]
        assert "0 s deadline" in out[0]["text"]
        assert any(o["type"] == "input_image" for o in out)
        assert b.client.restarts == 1 and b.client.proc is not None and b.client.proc.pid != pid
        assert await b.client.execute("still here") == [{"type": "input_text", "text": "ran still here"}]
        with pytest.raises(WorkerDead, match="twice"):
            await b.client.execute("hang")
        assert b.client.proc is None and b.released == 2
    asyncio.run(go())


def test_exit_is_a_restart_and_failsafe_closes() -> None:
    async def go() -> None:
        b = Bench()
        await b.client.start()
        out = await b.client.execute("exit")
        assert "desktop helper stopped" in out[0]["text"] and "was restarted" in out[0]["text"]
        assert b.client.restarts == 1 and b.released == 1
        with pytest.raises(FailSafe):
            await b.client.execute("corner")
        assert b.client.proc is None and b.released == 2
    asyncio.run(go())


def test_large_reply_line_is_accepted() -> None:
    async def go() -> None:
        b = Bench()
        await b.client.start()
        try:
            out = await b.client.execute("big")
            assert len(out[0]["text"]) == 100 * 1024
        finally:
            await b.client.close()
    asyncio.run(go())
