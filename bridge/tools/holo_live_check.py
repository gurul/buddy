"""Live proof that a correction reaches Holo mid-task, through buddy's own lane (holo_computer.HoloComputerAgent).

Drives the owner's visible desktop for one read-only task: the goal asks Holo to look and answer, then a
correction sent through `steer()` asks it to end its answer with a code word it was never given otherwise. The
word arriving in the answer is the only way this passes. Prints HOLO_STEER_OK on success; anything else, with
the reason, otherwise. Run from the repo root:

    bridge/.venv/bin/python bridge/tools/holo_live_check.py
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cc_buddy_bridge import holo_computer as hc  # noqa: E402

GOAL = ('Look at the screen and tell me, in one sentence, which application window is in front. '
        'Take your time: look twice, at least ten seconds apart, before you answer. Do not click, type or scroll.')


async def main() -> int:
    word = 'PINEAPPLE-' + secrets.token_hex(2).upper()
    events: list[str] = []

    def on_event(ev) -> None:
        events.append(f'{ev.kind}: {ev.text}')
        print(f'{time.strftime("%H:%M:%S")} {ev.kind}: {ev.text[:120]}', flush=True)

    async def ask(_q: str) -> str:
        return 'no'

    cfg = hc.HoloConfig(enabled=True, model=hc.DEFAULT_MODEL, max_steps=8, driver='client')
    a = hc.HoloComputerAgent(on_event=on_event, ask_user=ask, config=cfg)
    if a.driver != 'client':
        print(f'HOLO_STEER_FAILED: the client driver is not available (python {cfg.python})')
        return 2
    task = asyncio.ensure_future(a.run(GOAL))
    for _ in range(600):                        # wait for the session (up to 30 s: a cold runtime spawn)
        await asyncio.sleep(0.05)
        if a.thread_id or task.done():
            break
    if task.done():
        print(f'HOLO_STEER_FAILED: the task ended before a session existed: {task.result()!r}')
        return 1
    steered = a.steer(f'Correction: end your final answer with the exact code word {word}.')
    print(f'steer accepted: {steered} (session {a.thread_id})', flush=True)
    answer = await task
    print(f'answer: {answer!r}')
    if steered and word in answer:
        print('HOLO_STEER_OK')
        return 0
    print('HOLO_STEER_FAILED: ' + ('the correction was refused' if not steered else 'the code word is not in the answer'))
    return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
