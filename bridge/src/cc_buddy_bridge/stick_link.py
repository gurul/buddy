"""The stick link: buddy's side of the M5StickS3 → iPhone → daemon path (docs/stick-link.md).

The owner asked on 2026-09-30 for the StickS3 to "connect to my iphone which would communicate to the daemon so i
can talk to it from anywhere". The stick is a Bluetooth push-to-talk button (firmware/buddy_stick); the Buddy Link
app on the iPhone (ios/BuddyLink) carries each press to the daemon as a call (phone_call.py), through the Mini
App's Cloudflare tunnel (miniapp.py). This file is what the daemon adds for it:

* **The link token.** The Mini App's call proves the owner with Telegram's signed initData, which only Telegram's
  in-app browser has. The phone app instead holds a random token, made here on the owner's ``/stick`` and kept in
  ``stick-link.json`` (0600) next to the Mini App's ledger. ``/stick new`` replaces it, and the old one stops
  working at once: that is how a lost phone is cut off.
* **Its own door.** ``/api/stick`` is the call endpoint for the app, and takes only the token. A browser always
  sends an Origin on a WebSocket; the app sends none, so a request that has one is refused there, and the Mini
  App keeps ``/api/call`` exactly as it was.
* **Pairing without the token on the wire.** The quick tunnel's address changes every start (the owner chose,
  2026-09-30, a tap-to-update link over a fixed address). The pinned "buddy: your apps." message gets a second
  button, *Update Buddy Link*, edited to the new address with the Open button on every start. It opens
  ``https://<tunnel>/stick#t=<token>``: the token is in the fragment, which the browser never sends, and the
  page (static, no secrets) hands the address and the token to the app as ``buddylink://pair?u=…&t=…``.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

PAIR_PATH = "/stick"
CALL_PATH = "/api/stick"
UPDATE_TEXT = "Update Buddy Link"
PAIR_LINE = ("Tap the button to pair Buddy Link, the stick's app, with this Mac. After that the stick works "
             "anywhere your phone has signal. When buddy restarts, the pinned message's Update Buddy Link button "
             "has the new address.")
NEW_LINE = "A new link token: the old one no longer works. Tap the button on the phone that should keep it."
OFF_LINE = "Buddy Link needs the Mini App's tunnel, and it isn't up right now."


class LinkToken:
    """The one token the Buddy Link app presents. Absent until the owner pairs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def current(self) -> str:
        try:
            token = json.loads(self.path.read_text()).get("token", "")
        except (OSError, ValueError, AttributeError):
            return ""
        return token if isinstance(token, str) and len(token) >= 32 else ""

    def _write(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"token": token, "created": int(time.time())}, f)
        os.replace(tmp, self.path)

    def ensure(self) -> str:
        return self.current() or self.rotate()

    def rotate(self) -> str:
        token = secrets.token_urlsafe(32)
        self._write(token)
        log.info("stick: a new link token (the old one, if any, no longer works)")
        return token

    def check(self, presented: str) -> bool:
        want = self.current()
        return bool(want) and bool(presented) and hmac.compare_digest(want.encode(), presented.encode())


def pair_url(public_url: str, token: str) -> str:
    """The tap-to-update link. The token rides in the fragment, which never leaves the phone."""
    return f"{public_url.rstrip('/')}{PAIR_PATH}#t={quote(token, safe='')}"


# The pairing page: static, holds nothing secret, reads the fragment and offers the app's link. A button rather
# than an automatic jump: Telegram's in-app browser only opens another app from a tap.
PAIR_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
PAIR_HEADERS = {"Content-Security-Policy": PAIR_CSP, "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff"}                  # no-store comes with every reply
PAIR_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Buddy Link</title>
<style>
  :root { color-scheme: light dark; --bg: #faf7f2; --fg: #1d1b18; --muted: #6b655c; --accent: #d9774b; }
  @media (prefers-color-scheme: dark) { :root { --bg: #161412; --fg: #f1ece4; --muted: #a39b8f; } }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
         font: 17px/1.45 -apple-system, system-ui, sans-serif; }
  main { max-width: 22rem; padding: 24px 16px; text-align: center; }
  h1 { font-size: 1.5rem; margin: 0 0 .5rem; }
  p { color: var(--muted); margin: 0 0 1.5rem; }
  a.btn { display: inline-block; padding: 14px 26px; border-radius: 14px; background: var(--accent); color: #fff;
          font-weight: 600; text-decoration: none; }
  a.btn:focus-visible { outline: 3px solid var(--fg); outline-offset: 3px; }
</style></head>
<body><main>
  <h1>Buddy Link</h1>
  <p id="say">Opening the app that carries your stick to buddy.</p>
  <a class="btn" id="go" href="#">Open in Buddy Link</a>
</main>
<script>
  var t = new URLSearchParams(location.hash.slice(1)).get("t") || "";
  history.replaceState(null, "", location.pathname);
  var go = document.getElementById("go");
  if (!t) {
    document.getElementById("say").textContent = "This link is missing its token. Send /stick to buddy again.";
    go.remove();
  } else {
    go.href = "buddylink://pair?u=" + encodeURIComponent(location.origin) + "&t=" + encodeURIComponent(t);
  }
</script>
</body></html>
"""
