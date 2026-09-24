# Ask Claude: the Telegram Mini App

The bot's menu button (left of the message box) opens **Ask Claude**, a chat
with Claude inside Telegram. Answers stream in as they're written and are never
cut. A chat message stops at 4096 characters; this page doesn't.

Code: `bridge/src/cc_buddy_bridge/miniapp.py` (server, auth, spend, tunnel,
menu button) and `miniapp_page.html` (the page). Tests:
`bridge/tests/test_miniapp.py`.

## Turning it on

```sh
brew install cloudflared
pip install -e "bridge[miniapp]"          # the Anthropic SDK
```

Then add these to `~/.config/cc-buddy-bridge/env` and restart the daemon:

| Setting | Default | What it does |
|---|---|---|
| `CC_BUDDY_MINIAPP` | `0` | `1` turns it on. It also needs the Telegram door and `ANTHROPIC_API_KEY`. |
| `ANTHROPIC_API_KEY` | none | Your Claude API key. It stays on the Mac and never reaches the phone. |
| `CC_BUDDY_MINIAPP_MODEL` | `claude-opus-5-5` | The Claude model. |
| `CC_BUDDY_MINIAPP_EFFORT` | `medium` | `low` to `max`. How hard it thinks, and so how long and how costly each answer is. |
| `CC_BUDDY_MINIAPP_DAILY_USD` | `5` | The daily spend cap in dollars. |
| `CC_BUDDY_CLOUDFLARED` | found on `PATH` | The path to `cloudflared`. |

At start the daemon logs `miniapp: live at https://….trycloudflare.com; menu
button "Ask Claude" points there`.

## How it works

1. The daemon serves the page and a small API on `127.0.0.1` only.
2. Telegram needs a public HTTPS address, so a **Cloudflare quick tunnel**
   (`cloudflared tunnel --url`) fronts it. A quick tunnel needs no account or
   domain. Its address changes every time it starts, so the daemon re-points
   each owner's menu button at the new address (`setChatMenuButton`) on every
   start, and again if the tunnel drops. When the daemon stops, the button goes
   back to the `/` command menu.
3. You ask a question. The page sends it, with the rest of the conversation,
   plus Telegram's signed `initData`.
4. The server checks the signature, then asks Claude through the Anthropic SDK
   and streams the answer back as server-sent events.

The conversation is kept **on the phone** (the page's local storage), and
"New chat" clears it. The server keeps no conversation.

## Who can use it

Only the numeric ids in `CC_BUDDY_TELEGRAM_OWNER`. Every API call must carry
`initData` that Telegram signed for this bot:

- **The signature:** the HMAC-SHA256 has to verify with the bot token
  (Telegram's `WebAppData` scheme).
- **Its age:** `auth_date` must be under 24 hours old and no more than 5
  minutes in the future.
- **Who it's from:** the user inside it has to be an owner.

Anything else gets `403` and Claude is never called. The page itself holds
nothing, so opening the address in a browser shows an empty chat that can't
send.

## What it costs

After each answer, its cost is worked out from the usage the API reports, at
list price. For `claude-opus-5-5` that's $4 per million input tokens, $20 per
million output, $0.20 per million cache reads, and $5 per million cache writes.
A model that isn't in the price table is charged at the highest price, so it
can only make the cap stricter.

Each day's total is kept in `~/.config/cc-buddy-bridge/miniapp-spend.json`,
so a restart doesn't reset it. Once the cap is reached the app says so and
calls Claude no more until midnight. The header shows what's left today.

Verified live on 2026-09-24: a short answer through the public address arrived
in 1.9 s (first token at 1.8 s) and cost about $0.0008.

## What leaves the Mac

- **To Anthropic:** your questions and the conversation so far, over the
  Claude API with your key.
- **To Cloudflare:** a quick tunnel ends TLS at Cloudflare's edge, so the
  traffic between your phone and the Mac passes through Cloudflare's servers.
- **To Telegram:** only the menu button's address. The chat inside the Mini App
  doesn't go through Telegram's servers.

The bot token and the API key never appear in the log. The Bot API URL is
rewritten to `bot<token>` by the process-wide scrubber (`hide_token`).

## Limits

- Text only: no images or files yet.
- One answer at a time per owner. A second question while one is still
  streaming gets "Still answering the last question."
- The address changes on every restart. The menu button follows it, but an
  already-open Mini App from before the restart has to be closed and reopened.
