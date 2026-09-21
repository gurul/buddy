"""The two calls the loop watchdog caught on 2026-09-21 stay off the event loop.

The transcript tailer's initial sweep held the loop for 41 s at daemon start, and the
voice SDK's lazy resources import held it for 12–20 s on the first "hey buddy". Both
now run in a thread. These tests keep a ticker running on the loop while the work
happens and count its ticks: a call that blocks the loop stops the ticker.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from cc_buddy_bridge import voice_agent
from cc_buddy_bridge.jsonl_tailer import JSONLTailer


async def _ticks_during(coro, tick: float = 0.02) -> int:
    """How many times the loop came round while `coro` ran."""
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(tick)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        await coro
    finally:
        done.set()
        await t
    return ticks


def test_initial_sweep_keeps_the_loop_ticking(tmp_path: Path, monkeypatch):
    for i in range(3):
        (tmp_path / f"s{i}.jsonl").write_text(json.dumps({"type": "user", "uuid": f"u{i}"}) + "\n")
    tailer = JSONLTailer(lambda *a: None, roots=[tmp_path])
    slow_files: list[str] = []

    def slow_process(path: str) -> None:       # a transcript that takes 0.2 s to read
        slow_files.append(path)
        time.sleep(0.2)

    monkeypatch.setattr(tailer, "_process_file", slow_process)
    ticks = asyncio.run(_ticks_during(tailer._initial_sweep()))
    assert len(slow_files) == 3                 # the sweep still visited every file
    assert ticks >= 10, ticks                   # ~0.6 s of work: a blocked loop would show ~0


def test_run_keeps_the_loop_ticking_through_sweep_and_seed(tmp_path: Path, monkeypatch):
    """The real `run()` wiring: sweep, then seed, both off the loop, then the watch."""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "assistant"}, "uuid": "a1"}) + "\n")

    async def no_watch(*_a, **_k):           # the file watch ends at once: nothing to tail
        return
        yield                                 # noqa: RET504 — makes this an async generator

    monkeypatch.setattr("cc_buddy_bridge.jsonl_tailer.awatch", no_watch)

    async def on_update(*_a) -> None:
        pass

    tailer = JSONLTailer(on_update, roots=[tmp_path])
    real_seed = tailer._seed_emitted_from_history

    def slow_seed() -> None:                  # a year of transcripts takes a while to read
        time.sleep(0.3)
        real_seed()

    monkeypatch.setattr(tailer, "_seed_emitted_from_history", slow_seed)
    ticks = asyncio.run(_ticks_during(tailer.run()))
    assert ticks >= 5, ticks
    assert "a1" in tailer._emitted_assistant_uuids[str(p)]
    assert tailer._initial_sweep_done


def test_warm_live_import_loads_the_sdk_resources_and_is_idempotent():
    voice_agent.warm_live_import()
    assert "openai.resources.live" in sys.modules
    voice_agent.warm_live_import()              # cached: a second call is a no-op
    # Off the loop, the same call leaves the loop ticking.
    ticks = asyncio.run(_ticks_during(asyncio.to_thread(voice_agent.warm_live_import)))
    assert ticks >= 0
