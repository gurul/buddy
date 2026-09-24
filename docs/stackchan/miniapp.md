# buddy's Mini App: your apps

Ask buddy for an app, like "make me a habit tracker", and it builds one: a
small, real app that opens inside Telegram and keeps its data on your Mac.
Before you see it, buddy uses it on a phone-sized screen and fixes what broke.
You can change, undo, rename or delete an app from the chat or from the Apps
screen.

The Mini App had a Claude chat tab as well. It was removed on 2026-09-24 so the
Mini App is only your apps.

Code:
- `bridge/src/cc_buddy_bridge/miniapp.py`: the server, sign-in check, spend
  ledger, tunnel and pinned button.
- `apps_maker.py`: what Claude is told, the build and repair loop, and storing
  and serving apps.
- `app_check.py`: the phone check every build goes through.
- `miniapp_page.html`: the home screen.
- `bridge/tools/app_eval.py`: builds apps from the command line into a scratch
  folder and writes a report with cost, time and the check's result.

Tests: `bridge/tests/test_miniapp.py`, `bridge/tests/test_apps_maker.py`,
`bridge/tests/test_app_check.py`.

## Opening it

The **menu button** next to the message box stays the `/` command list. The
Mini App is always one tap away three other ways:

- **A pinned message** at the top of the chat has an **Open buddy** button.
- **`/apps`**, the first command in the menu, sends the same button. Typing
  "my apps" or "open apps" does too, even while `claude on` is relaying.
- **After a build**, buddy sends a picture of the new app with an
  **Open ‹app›** button under it.

Telegram's menu button can be either the command list or one Mini App, never
both, which is why the Mini App gets the pinned message instead.

## Making an app

Ask in the chat ("make me a habit tracker with streaks", "build a workout
log"), or type it into **What should I build?** on the Apps tab.
`claude-opus-5-5` writes one self-contained HTML file. Its instructions cover
Telegram's Mini App runtime, how to save data with `window.buddy`, a design
system, and a small reference app that itself passes the phone check. Each
request also carries today's date, the local time and time zone
(`CC_BUDDY_TIMEZONE`), and a guess at units and currency from
`CC_BUDDY_LOCATION`.

A build is a loop:

1. **Write.** Claude writes the file.
2. **Check in code.** The file must call `buddy.load` and `buddy.save`, have a
   `<title>`, and load nothing except from `cdn.jsdelivr.net` and
   `telegram.org`. A library from jsdelivr must be an npm package at an exact
   version (`/npm/chart.js@4.4.1/…`): a range, `@latest`, no version or a
   GitHub path (`/gh/…`) can change after the app was tested. This one is a
   gate, not a hint: a version that loads anything else is never saved.
3. **Use it on a phone.** The phone check (below) opens the app and uses it.
4. **Repair.** If anything went wrong, the problems go back to Claude as a
   numbered list, each with the tap that caused it, and Claude returns the
   whole fixed file. There are at most 2 repair rounds.
5. **Keep the best.** The version with the fewest problems is saved. A version
   the phone check really ran on always beats one it could not run on.

On the Apps tab the progress line shows the stage in words, with a clock:
"Thinking it through…", "Writing it… 12k" (characters so far), "Testing it on
a phone…", "Fixing 2 problems…". When it is done, the line says "Ready: ‹app›.
Tested on a phone.", or names the first thing that may still not work. A clean
app opens by itself when you are still on the page and not in the middle of
something else (a change picked for another app, or words typed for the next
build); otherwise an **Open ‹app›** button waits under the line, with **Undo
this change** beside it when a change may have broken something. What you
typed for another app while this one was building stays in the box.

If the page goes away mid-build (the phone locks, you close Telegram), the
build goes on and its result comes to the chat instead. Opened again, the page
shows "Still building …" with the clock, holds **Build it** and the app's
**…** until it is done, then says the result is in the chat.

From the chat, buddy sends one short line when the new app is being tested
and one per repair round, then the picture. The picture's caption says it is a
test run with sample entries, and names the first problem left, if any. A
failed build says why (for example, the API key was refused), and a failed
change says the app is unchanged.

Measured on 2026-09-24, the first three with `tools/app_eval.py` and the last
through the Apps tab's own route (`/api/make`). All passed the check on the
first round; no repair round was needed:

| Build | Time | Cost | Phone check |
|---|---|---|---|
| New habit tracker | 226 s | $0.59 | 12 taps, 1 save (an earlier checker) |
| New habit tracker | 267 s | $0.66 | 25 taps, 8 saves |
| Change: "add a monthly calendar view for each habit, and a best-streak number" | 183 s | $0.58 | 18 taps, 8 saves |
| New tip splitter | 156 s | $0.41 | 11 taps, 11 saves |

## Changing, undoing, renaming and deleting

On the Apps tab, each app shows its icon and a short description. Tap **…**
next to it for:

- **Change.** The box at the top becomes "What should change?". Claude gets
  the current file, what you asked of the app before, and a shortened sample
  of its saved data, and returns the whole new file. The new version must open
  your existing data as it is; the phone check starts from that data, so a
  change that breaks it is caught.
- **Rename.** Only the name changes. A renamed app keeps your name through
  later changes. Its address stays the same.
- **Undo last change.** Shown only when there is an earlier version. The app
  goes back to the version before its last change. The version you had is not
  kept, so the page asks first. Saved data is not touched. If your data was
  moved to a newer shape since that change, the question says entries saved
  since then may not open in the older version (changing the app again brings
  them back). While an undo is on its way the sheet's buttons wait, and the
  server refuses a second undo sent for the same version.
- **Delete.** The page asks first (Telegram's popup, with a red **Delete**),
  then moves the app's whole folder to buddy's trash,
  `~/.config/cc-buddy-bridge/apps/.trash/<name>-<date>-<time>/`. Nothing is
  erased: **Undo** next to "Deleted ‹app›." brings it back with its data, for
  15 seconds; after that, ask in the chat ("bring back the reading list").

The same things work from the chat: "add a weekly chart to my habit tracker",
"undo the last change to the habit tracker", "rename the workout log to Gym",
"delete the reading list", "bring back the reading list". From the chat,
undo, rename and delete happen without a question, but only on an app the
words name for sure: its exact name, or the only app whose name has every word
you used. "Delete the water app" with a Water Log and a Water Plants does
nothing, and buddy asks which one. A change ("add a chart to the water log")
still takes the closest match, since the old version stays for undo.

While an app is being changed, delete, rename and undo for that app are
refused until the change is done, whether the change was started from the chat
or from the Apps tab.

Each app lives in `~/.config/cc-buddy-bridge/apps/<name>/`:

| File | What it holds |
|---|---|
| `index.html` | The app |
| `versions/<n>.html` | Up to 10 earlier versions, newest has the highest number |
| `meta.json` | Its name, icon, description, dates, what you asked for, and the data version (`v`) each earlier version was written for |
| `data.json` | Its saved data |

Apps save through `window.buddy`, which is added to every app:
`await buddy.load()` returns the last saved value, and
`await buddy.save(value)` stores any JSON up to 1 MB. Every load and save
carries the app's own key (see "An app is fenced in" below), so an app reads
and writes its own data and nobody else's.

Telegram's Back button always leads somewhere: inside an app it goes back
through the app's own screens, and on the app's first screen it goes back to
buddy's list of apps, where Change, Undo and the other apps are.

## The phone check

`app_check.py` opens the app in headless Chromium at an iPhone's size (390 by
844, with touch), sandboxed exactly as the server serves it, so browser storage
fails there as it does on the phone. It stands in for Telegram (theme colors, MainButton,
BackButton, haptics, and popups that answer yes) and for `window.buddy`, whose
storage is kept in Python so a reload cannot lose it. Then it uses the app
the way a person would in the first minute:

- It fills every visible field, and taps every button, checkbox and tab it can
  reach (at most 40 taps, and at most 3 of a row of look-alike buttons). It
  presses the MainButton when a view is used up, and Back to leave it. Delete
  and reset buttons come last, after the rest of the view has been used.
- It opens the app again with the data it saved, and takes the picture that
  is sent to the chat. Then it opens it once more at a small Android phone's
  width (360).

What counts as a problem is what a person would see: an error on the page, a
tap that reloads the whole page, a request to a host the app may not reach, a
library that didn't load, a page wider than the phone or laid out for a
desktop, an app that never saves after being used, an app that freezes
(nothing finishes within 60 s), an app that crashes the browser tab (memory
that grows without end), and an app that opens a WebSocket. A crash keeps
everything found before it. Only a checker that could not start at all counts
as "not tested".

It needs Playwright and Chromium in the daemon's environment. Without them the
check reports "not tested" and the build is saved after the code checks only.
A correct small app takes about 3.5 s to check; the habit trackers above took
6 to 9 s.

## Turning it on

```sh
brew install cloudflared
pip install -e "bridge[miniapp]"           # the Anthropic SDK, and Playwright for the phone check
python -m playwright install chromium
```

Then add these to `~/.config/cc-buddy-bridge/env` and restart the daemon:

| Setting | Default | What it does |
|---|---|---|
| `CC_BUDDY_MINIAPP` | `0` | `1` turns it on. It also needs the Telegram door and `ANTHROPIC_API_KEY`. |
| `ANTHROPIC_API_KEY` | none | Your Claude API key. It stays on the Mac and never reaches the phone. |
| `CC_BUDDY_MINIAPP_MODEL` | `claude-opus-5-5` | The Claude model for building. |
| `CC_BUDDY_MINIAPP_MAKE_EFFORT` | `high` | Effort when building or changing an app. |
| `CC_BUDDY_MINIAPP_DAILY_USD` | none | Unset or `0`: no cap, spend is only tracked. A number above 0 is a daily cap on building. |
| `CC_BUDDY_TIMEZONE`, `CC_BUDDY_LOCATION` | the Mac's time zone; none | Used in each build's context (dates, units, currency). |
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

Only the numeric ids in `CC_BUDDY_TELEGRAM_OWNER`. Every API call (build,
list, load, save, delete, rename, undo) must carry `initData` that
Telegram signed for this bot:

- **The signature:** the HMAC-SHA256 has to verify with the bot token
  (Telegram's `WebAppData` scheme).
- **Its age:** `auth_date` must be under 24 hours old and no more than 5
  minutes in the future.
- **Who it's from:** the user inside it has to be an owner.

Anything else gets `403`, and Claude is never called. A call from a page
that is not buddy's home page (a sandboxed app, or another site) gets `403`
too, even with a valid `initData`.

### An app is fenced in

An app is code Claude wrote, plus any library it loads from jsdelivr, so it
never gets your `initData`, which answers for everything above:

- **Its own key.** An app opens as `/apps/<name>/?t=<key>`. The key is signed
  with the bot token, bound to that one app, and lasts 24 hours. It opens that
  app's page and answers its load and save, and nothing else: not the list of
  apps, not another app's data, not build, delete or the chat.
- **Sandboxed.** The page is served with a Content-Security-Policy `sandbox`
  (no `allow-same-origin`), so it runs in an opaque origin: it cannot read the
  home page's storage (your chat history) or call the API as the home page.
  The same policy lets it load only inline code, Telegram's script, `buddy.js`
  and `cdn.jsdelivr.net/npm/`, and connect only to its own load and save.
- **Opened through the home page.** An Open button in the chat points at
  `/?open=<name>`. The home page gets Telegram's signed sign-in, then opens the
  app with a fresh key, passing on Telegram's version, platform, theme and
  your first name, but nothing signed.
- **No key, no page.** `/apps/<name>/` without a valid key answers the same
  for every name (a redirect to `/?open=<name>`), so a link someone saw shows
  neither the app nor whether it exists. Old Open buttons keep working through
  that redirect.

## What it costs

There is no spend limit unless you set one. Owner, 2026-09-24: "no claude
limit, just track spend".

After each answer and each build round, its cost is worked out from the usage
the API reports, at list price. For `claude-opus-5-5` that's $4 per million
input tokens, $20 per million output, $0.20 per million cache reads, and for
cache writes $5 per million when the cache lasts 5 minutes or $8 per million
when it lasts an hour (the build prompt's cache). A model that isn't in the price table is charged at
the highest price, so it is never under-counted.

Every day's total is kept in `~/.config/cc-buddy-bridge/miniapp-spend.json`
(the last 400 days), so a restart loses nothing. The header shows
"$X.XX spent today". With `CC_BUDDY_MINIAPP_DAILY_USD` set above 0, building
stops for the day once that total is reached, and the header shows
"$X.XX of $Y.YY spent today".

A line in the chat marks each of $5, $20, $50, $100, $200, $500 and $1000
that a day's spend passes ("Claude has cost $21.40 today … Nothing is
capped"). It is a note, not a limit, so a runaway (a leaked key, a loop) is
seen the same day.

The build instructions are cached: a change made within 5 minutes of another
build reads about 7,700 tokens from cache instead of paying for them again.

Measured on 2026-09-24:
- A build or change cost $0.41 to $0.66 (the table above).

## What leaves the Mac

- **To Anthropic:** what you ask to be built, and, when you change an app, its current file, what you asked
  of it before, and a shortened sample of its saved data. When a build is
  repaired, the problems the phone check found go too. All of it goes over the
  Claude API with your key.
- **To Cloudflare:** a quick tunnel ends TLS at Cloudflare's edge, so traffic
  between your phone and the Mac, including app data, passes through
  Cloudflare's servers.
- **To Telegram:** the pinned message, the Open buttons and their addresses,
  and the picture of each finished app. The phone check takes it, so it shows
  the made-up entries the check typed in and, when an app was changed, your
  own saved data too.
  What you do inside the Mini App doesn't go through Telegram.
- **The phone check** runs on the Mac. The only requests it lets out are to
  `cdn.jsdelivr.net`, when an app loads a library from there. Every HTTP
  request goes through the check's own router, WebSockets are answered and
  closed by the check, Chromium resolves no other name (an IP address is
  refused too), and WebRTC is kept off the network. When an app is changed,
  the check runs it with your real saved data, so none of that can leave.

The bot token and the API key never appear in the log. The Bot API URL is
rewritten to `bot<token>` by the process-wide scrubber (`hide_token`).

## Limits

- **One build at a time:** a second build waits until the first is done.
- **Closing the page doesn't stop a build:** it finishes, is saved, shows up
  in Your apps, and its result comes to the chat.
- **Old buttons expire:** the address changes on every restart. The pinned
  button follows it, but an Open button in an older message leads nowhere
  once the address has changed, so use the pinned one or `/apps`.
- **An app left open for over 24 hours** stops saving (its key expired);
  opening it again from buddy's list gives it a new one.
- **Stopping the daemon** cuts a build that is still running; the stop waits
  at most 2 seconds for open connections.
- **Apps can't reach outside services:** an app can't call other APIs or send
  notifications. It works with what you enter and what it saves.
- **Undo is one step at a time,** and at most 10 earlier versions are kept.
