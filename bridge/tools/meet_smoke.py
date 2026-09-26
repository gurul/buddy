"""Live run of the Meet notetaker (meet.py) outside the daemon: join a real call from the owner's Chrome, print
every text buddy would send, and leave on Ctrl-C (the notes are written either way). Prints MEET_SMOKE_OK only
when buddy got into the call, heard at least one caption line, and wrote the notes file.

Usage:
  .venv/bin/python tools/meet_smoke.py https://meet.google.com/abc-defg-hij
  .venv/bin/python tools/meet_smoke.py "6pm"          # the owner's calendar, through Composio
  .venv/bin/python tools/meet_smoke.py --calendar      # only list today's events with a Meet link

Needs Chrome running with remote debugging on (chrome://inspect/#remote-debugging) and the daemon's env file.
Chrome's "Allow remote debugging?" is pressed by buddy when CC_BUDDY_CHROME_ACCESS=allow, else press it.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

from cc_buddy_bridge import chrome_consent, composio_tools, envfile, meet
from cc_buddy_bridge import telegram as telegram_mod


def calendar(env: dict[str, str]):
    tg = telegram_mod.configured(env)
    cfg = composio_tools.configured(env, tg.owner_ids)
    if not cfg.enabled:
        return None
    bridge = composio_tools.ComposioBridge(cfg)
    bridge.start()
    return meet.calendar_lister(bridge)


async def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    import os

    envfile.load_env_file()                     # the daemon's env file, never over what is already set
    env = dict(os.environ)
    ask = " ".join(argv).strip()
    lister = calendar(env) if not meet.parse_link(ask)[0] else None
    if ask == "--calendar":
        if lister is None:
            print("no calendar (CC_BUDDY_COMPOSIO)")
            return 1
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for e in meet.events_in(lister(start, start + timedelta(days=1))):
            print(e.get("summary"), (e.get("start") or {}).get("dateTime"), meet.event_link(e))
        return 0
    broker = chrome_consent.ConsentBroker(None, auto_allow=chrome_consent.access_preference() == "allow")
    store = Path(env.get("CC_BUDDY_MEMORY_DIR") or "~/.config/cc-buddy-bridge/memory").expanduser()
    root = store / "transcripts" / "meetings"
    m = meet.make_meeter(None, root, answer=broker.answer_own_connection, environ=env)
    if m is None:
        print("meet is off here (CC_BUDDY_MEET / CC_BUDDY_BROWSER_ATTACH / Playwright)")
        return 1
    m.list_events = lister

    async def notify(text: str, about: str = "") -> None:
        print(f"\n[buddy would text] {text}\n", flush=True)

    m.notify = notify
    r = await m.join(ask)
    print(r, flush=True)
    if not r.get("ok"):
        return 1
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, m.leave)
    loop.add_signal_handler(signal.SIGTERM, m.leave)
    state = await m.task
    print(f"ended: {state.ended}; lines: {state.lines}; notes: {state.notes}; clicks: {state.clicks}")
    if state.joined_at is not None and state.lines > 0 and state.notes and state.notes.exists():
        print("MEET_SMOKE_OK")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
