"""The owner approves buddy's connection to their Chrome from the phone.

Chrome asks "Allow remote debugging?" for every new connection to the owner's real Chrome (browser_lane.py,
attach mode) — the only lock between their logged-in browser and any program on the Mac. The owner is often
away from the desk (owner, 2026-09-23: "i won't be on computer to approve … text through telegram if it's
ok"), so the dialog's answer comes from them over Telegram and buddy presses the button they chose:

* It is only ever used while buddy ITSELF is connecting (``BrowserLane.connect``): the dialog it answers is
  the one buddy's own connection raised, never a prompt some other program caused on its own.
* It acts only on a dialog that reads "Allow remote debugging?" and has an Allow button — Chrome's own.
* Allow is pressed only on the owner's clear yes (consent.approves: fail-closed). No, anything unclear, no
  answer in time, or no way to ask (Telegram off): Cancel is pressed — never a dialog left hanging — and the
  task goes to Codex, which already drives the owner's Chrome without this prompt.

The buttons are Chrome web-UI controls: macOS Accessibility's AXPress presses them (a synthetic click does
not), through System Events, which the daemon already has Accessibility permission for.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from . import consent

log = logging.getLogger(__name__)

DIALOG_TEXT = "Allow remote debugging?"
APPEAR_SECS = 10.0            # how long after buddy starts connecting the dialog may take to show
POLL_SECS = 0.3
ASK_TIMEOUT_SECS = 180.0      # how long the owner has to answer on the phone
QUESTION = ("buddy wants to control your Chrome (your logged-in browser) for a task: Chrome is asking "
            "\"Allow remote debugging?\". Allow it? yes / no")

_FIND = '''
on run argv
  set wanted to item 1 of argv
  tell application "System Events"
    if not (exists process "Google Chrome") then return "none"
    tell process "Google Chrome"
      repeat with w in windows
        set els to {}
        try
          set els to entire contents of w
        end try
        set seen to false
        set hasAllow to false
        repeat with e in els
          try
            set r to role of e as string
            if r is "AXStaticText" and (value of e as string) is wanted then set seen to true
            if r is "AXButton" and (name of e as string) is "Allow" then set hasAllow to true
          end try
        end repeat
        if seen and hasAllow then return "found"
      end repeat
    end tell
  end tell
  return "none"
end run
'''

_PRESS = '''
on run argv
  set wanted to item 1 of argv
  set label to item 2 of argv
  tell application "System Events"
    tell process "Google Chrome"
      repeat with w in windows
        set els to {}
        try
          set els to entire contents of w
        end try
        set seen to false
        repeat with e in els
          try
            if (role of e as string) is "AXStaticText" and (value of e as string) is wanted then set seen to true
          end try
        end repeat
        if seen then
          repeat with e in els
            try
              if (role of e as string) is "AXButton" and (name of e as string) is label then
                perform action "AXPress" of e
                return "pressed"
              end if
            end try
          end repeat
        end if
      end repeat
    end tell
  end tell
  return "none"
end run
'''


async def _osascript(script: str, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec("osascript", "-e", script, *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("chrome-consent: osascript failed (%s)", err.decode(errors="replace").strip()[:160])
        return "error"
    return out.decode(errors="replace").strip()


async def dialog_showing() -> bool:
    return await _osascript(_FIND, DIALOG_TEXT) == "found"


async def press(label: str) -> bool:
    """Press Allow or Cancel on Chrome's own remote-debugging dialog. False when no such dialog is showing."""
    if label not in ("Allow", "Cancel"):
        raise ValueError(label)
    return await _osascript(_PRESS, DIALOG_TEXT, label) == "pressed"


class ConsentBroker:
    """Answers the Chrome dialog raised by buddy's own connection with the owner's Telegram yes or no.

    ``ask_owner(question) -> reply`` (Telegram), or None when there is no way to ask: then the dialog is
    cancelled. ``showing``/``pressing`` are the Accessibility seams (tests fake them)."""

    def __init__(self, ask_owner: Optional[Callable[[str], Awaitable[str]]], *,
                 showing: Callable[[], Awaitable[bool]] = dialog_showing,
                 pressing: Callable[[str], Awaitable[bool]] = press,
                 appear_secs: float = APPEAR_SECS, ask_timeout_secs: float = ASK_TIMEOUT_SECS,
                 poll_secs: float = POLL_SECS) -> None:
        self._ask, self._showing, self._pressing = ask_owner, showing, pressing
        self._appear, self._ask_timeout, self._poll = appear_secs, ask_timeout_secs, poll_secs
        self.last: str = ""                      # what happened last: allowed | declined | no_dialog | …

    async def answer_own_connection(self) -> str:
        """Called while buddy's own connect is in flight. Waits for Chrome's dialog, asks the owner, presses
        the button. Returns allowed | declined | unanswered | no_dialog | no_way_to_ask."""
        deadline = time.monotonic() + self._appear
        while not await self._showing():
            if time.monotonic() >= deadline:
                self.last = "no_dialog"          # already allowed on this connection, or Chrome did not ask
                return self.last
            await asyncio.sleep(self._poll)
        if self._ask is None:
            await self._pressing("Cancel")
            self.last = "no_way_to_ask"
            return self.last
        try:
            reply = await asyncio.wait_for(self._ask(QUESTION), timeout=self._ask_timeout)
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 — no answer is a no
            log.info("chrome-consent: no answer from the owner (%s); cancelling", type(e).__name__)
            await self._pressing("Cancel")
            self.last = "unanswered"
            return self.last
        if consent.approves(reply):
            ok = await self._pressing("Allow")
            self.last = "allowed" if ok else "dialog_gone"
        else:
            await self._pressing("Cancel")
            self.last = "declined"
        log.info("chrome-consent: %s", self.last)
        return self.last
