# buddy's Mini App: your apps, and a chat with Claude

Ask buddy for an app, like "make me a habit tracker", and it builds one: a
small, real app that opens inside Telegram and keeps its data on your Mac. The
same Mini App has a chat with Claude, where answers stream in full and are
never cut at Telegram's 4096 characters.

Code:
- `bridge/src/cc_buddy_bridge/miniapp.py`: the server, sign-in check, spend
  cap, tunnel and pinned button.
- `apps_maker.py`: building, storing and serving apps.
- `miniapp_page.html`: the home screen.

Tests: `bridge/tests/test_miniapp.py`, `bridge/tests/test_apps_maker.py`.

## Opening it

The **menu button** next to the message box stays the `/` command list. The
Mini App is always one tap away three other ways:

- **A pinned message** at the top of the chat has an **Open buddy** button.
- **`/apps`**, the first command in the menu, sends the same button. Typing
  "my apps" or "open apps" does too, even while `claude on` is relaying.
- **After a build**, buddy sends an **Open ‹app›** button for the new app.

Telegram's menu button can be either the command list or one Mini App, never
both, which is why the Mini App gets the pinned message instead.

## Making an app

Ask in the chat ("make me a habit tracker with streaks", "build a workout
log"), or type it into **What should I build?** on the Apps tab.
`claude-opus-5-5` writes one self-contained HTML file. It takes about a
minute, and then the Open button arrives.

To change an app, say "add a weekly chart to my habit tracker", or tap
**Change** next to it on the Apps tab. Claude gets the current file and
returns the whole new one. Your saved data is kept, and the previous version
is saved as `index.prev.html`.

Each app lives in `~/.config/cc-buddy-bridge/apps/<name>/`:

| File | What it holds |
|---|---|
| `index.html` | The app |
| `index.prev.html` | The version before the last change |
| `meta.json` | Its title, dates, and what you asked for |
| `data.json` | Its saved data |

Apps save through `window.buddy`, which is added to every app:
`await buddy.load()` returns the last saved value, and
`await buddy.save(value)` stores any JSON up to 1 MB. Every load and save
carries Telegram's signed sign-in, checked like everything else below, so only
you can read or change an app's data.

Verified live on 2026-09-24: Opus 5.5 built a habit tracker through the
public address, the page was served with `window.buddy`, and saved data
round-tripped.

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
| `CC_BUDDY_MINIAPP_MODEL` | `claude-opus-5-5` | The Claude model, for both chat and building. |
| `CC_BUDDY_MINIAPP_EFFORT` | `medium` | Chat effort, `low` to `max`. Higher means deeper, slower and costlier answers. |
| `CC_BUDDY_MINIAPP_MAKE_EFFORT` | `high` | Effort when building or changing an app. |
| `CC_BUDDY_MINIAPP_DAILY_USD` | `5` | The daily spend cap in dollars, for chat and building together. |
| `CC_BUDDY_CLOUDFLARED` | found on `PATH` | The path to `cloudflared`. |

At start the daemon logs `miniapp: live at https://….trycloudflare.com; the
pinned Open button points there`.

## Why the Cloudflare tunnel

Telegram only opens Mini Apps from a public `https://` address. buddy runs on
your Mac, behind your home network, where the phone can't reach it directly.

The daemon serves everything on `127.0.0.1` only, and a **Cloudflare quick
tunnel** (`cloudflared tunnel --url`) gives it a public address. A quick tunnel
needs no account and no domain, but its address changes every time it starts.
So on each start the daemon edits the pinned message's button to the new
address, and when it stops the pinned message says the apps are offline.

For an address that never changes, you'd need a named Cloudflare tunnel on
your own domain, or Tailscale Funnel.

## Who can use it

Only the numeric ids in `CC_BUDDY_TELEGRAM_OWNER`. Every API call (chat,
build, list, load, save) must carry `initData` that Telegram signed for this
bot:

- **The signature:** the HMAC-SHA256 has to verify with the bot token
  (Telegram's `WebAppData` scheme).
- **Its age:** `auth_date` must be under 24 hours old and no more than 5
  minutes in the future.
- **Who it's from:** the user inside it has to be an owner.

Anything else gets `403`, and Claude is never called. The pages themselves
hold no data, so opening an address in a normal browser shows an app that
can't load or save anything.

## What it costs

After each answer or build, its cost is worked out from the usage the API
reports, at list price. For `claude-opus-5-5` that's $4 per million input
tokens, $20 per million output, $0.20 per million cache reads, and $5 per
million cache writes. A model that isn't in the price table is charged at the
highest price, so it can only make the cap stricter.

Each day's total is kept in `~/.config/cc-buddy-bridge/miniapp-spend.json`,
so a restart doesn't reset it. Once the cap is reached, chat and building both
stop until midnight. The header shows what's left today.

Measured on 2026-09-24:
- A short chat answer took 1.9 s and cost about $0.0008.

## What leaves the Mac

- **To Anthropic:** your questions and the conversation so far, what you ask
  to be built, and the current app's file when you change it. All of it goes
  over the Claude API with your key.
- **To Cloudflare:** a quick tunnel ends TLS at Cloudflare's edge, so traffic
  between your phone and the Mac, including app data, passes through
  Cloudflare's servers.
- **To Telegram:** the pinned message, the Open buttons and their addresses.
  What you do inside the Mini App doesn't go through Telegram.

The bot token and the API key never appear in the log. The Bot API URL is
rewritten to `bot<token>` by the process-wide scrubber (`hide_token`).

## Limits

- **One job at a time:** you get one chat answer or one build at a time.
- **Old buttons expire:** the address changes on every restart. The pinned
  button follows it, but an Open button in an older message leads nowhere
  once the address has changed, so use the pinned one or `/apps`.
- **Text only in chat:** no images or files yet.
- **Apps can't reach outside services:** an app can't call other APIs or send
  notifications. It works with what you enter and what it saves.
