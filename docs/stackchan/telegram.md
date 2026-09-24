# Text buddy through Telegram

Until this, the only way words reached buddy was the microphone: a conversation
needs a live mic, so away from the desk there was no buddy at all. `telegram.py`
is a text door. From your phone you can chat with buddy, start a task on the
Mac, stop it, answer the question a task asks before it does something
consequential, and get a photo from the robot's camera.

It ships **off**. No evaluation of the text brain exists yet, so
`TELEGRAM_DEFAULT` is `False` and a test holds it there.

```
your phone ── Telegram ──▶ api.telegram.org ◀── long poll (outbound HTTPS) ── telegram.py in the daemon
                                                                                   │ accept(): owner id, private chat,
                                                                                   │ fresh, your own words — or dropped
                                                                                   ▼
                                                        text brain: gpt-6-astra, Responses API, store=False
                                                          tools: start_task · steer_task · stop_task · take_photo ·
                                                                 screenshot · send_file · list_files · look · look_around ·
                                                                 find · move_head · go_explore · set_sound · take_notes ·
                                                                 remember · think_hard · web_search (+ memory_search · memory_read ·
                                                                 forget_preview · forget_apply with CC_BUDDY_MEMORY=1)
                                                                                   │ start_task
                                                                                   ▼
                                              codex_computer.py → Codex app-server → installed cua_repl.js
```

There is no port to open, no webhook and no public URL. The daemon calls
Telegram; nothing calls the Mac.

Computer tasks now use Codex's existing Computer Use through the public app-server
protocol. `start_task`, `steer_task`, and `stop_task` keep their existing interface.
Codex commentary appears as steps in the task's progress message, and app permission requests wait
for an explicit reply. Native app-access prompts offer `yes` (once),
`allow for task`, or `always allow` when Codex permits those choices; `no` denies.
`always allow` saves the app in Codex's own Always-allowed apps list for future
tasks, including after Buddy restarts. Revoke saved grants in Codex desktop's
**Settings → Computer Use → Always-allowed apps**. Buddy does not keep a second
allowlist. Managed restrictions, app-risk warnings and sensitive-action approvals
remain in force; audio and unknown request types cannot gain an app-wide grant.
A timeout, unsupported scope or unrecognized answer does not approve. Unknown
permission forms are declined with an explanation.
The adapter never falls back to Buddy's old desktop worker. See
[the verified integration and limits](../codex-computer-use/README.md).

What else the Bot API offers, and how buddy could use it: [telegram-bot-api.md](telegram-bot-api.md).

## Rundown

Text `rundown`, `/rundown`, or `buddy: rundown` for today's **email, calendar,
Slack, and Obsidian todos**. This command addresses Buddy even while either relay
is selected. It loads the packaged `skills/rundown/SKILL.md` each time and obtains
fresh app data through the existing Composio session. Email includes today's inbox
and older unread items needing attention; Slack focuses on mentions, DMs, and
recent requests. Calendar queries use local midnight through the next midnight.

Todos come from open markdown checkboxes in `CC_BUDDY_VAULT` (the existing
Second Brain/Obsidian vault). Dated due/scheduled/start markers and today's daily
note identify today's items; overdue and undated items are separate. Completed
items, future items, hidden folders, archives and symlinks are excluded. The scan
is bounded to 5,000 files, 1 MiB per file and 200 tasks. `CC_BUDDY_SECOND_BRAIN=1` supplies the vault; Composio supplies connected
Gmail/Calendar/Slack accounts. A source that is not connected, or whose read failed
outright, is named rather than treated as empty. Result limits, pagination and
clipping are never mentioned: the rundown reports what it retrieved (owner,
2026-09-23). Rundown exposes only read/search tools and rejects mutations before execution.
It does not send messages, mark mail read, change calendar events or edit todos.
The retrieved content is summarized by Buddy's configured OpenAI text model.

## Receiving images

Send a Telegram photo or an image file, optionally with a caption. Still JPEG,
PNG, WebP and GIF are accepted, up to 10 MB and 25 megapixels. Each photo in an
album is delivered separately. Voice notes, animations and other documents are
not accepted. Owner/private-chat, freshness and forwarded-message checks apply
before downloading; bytes and decoded dimensions are validated before use.

With `claude on` or a selected Codex folder chat, the caption and a local image path
are sent to that same session with an instruction to read the image. The image
is not silently routed to Buddy. This uses the existing text relay and the
recipient's image-reading tool, with its normal file permissions; it does not
add a private attachment endpoint. Claude's official
[image workflow](https://code.claude.com/docs/en/common-workflows#work-with-images)
documents passing a local image path. Codex can inspect it using `view_image`.
These relays target sessions running on this Mac; remote sessions cannot read
its temporary files. `buddy: <caption>` addresses Buddy instead.

A relayed image gets a 👀 reaction when it arrives and a 👍 in its place once it
reached Claude or Codex (owner, 2026-09-23). If it is not delivered, the 👀 comes
off and a line says why. If reactions fail, the image still goes, and the usual
line (`Typed.`) says so; a 👀 whose 👍 could not be set is taken off, so it
never still says "on its way".

Without a relay, image bytes go directly to Buddy's configured OpenAI model as
an `input_image` data URL. The Telegram download URL/token never goes to the
model. Image bytes are not included in Buddy's text conversation history; a
later independent Buddy turn needs the image resent. An image caption cannot
answer a pending permission question or run chat commands such as `stop`.
Answer the question in a separate text and resend the image afterward.

Relay images are stored in a private `buddy-telegram-images-<uid>` directory
under the Mac's temporary directory, with owner-only permissions. Files older
than 24 hours are removed on the next image save, with a 100-file limit. They
remain available long enough for queued turns to read them. Relay confirmation
means the message was submitted, not that the recipient has inspected it.

## Set it up

1. In Telegram, talk to **@BotFather**: `/newbot`, pick a name, copy the token.
2. Put the token in `~/.config/cc-buddy-bridge/env` (mode 600):
   ```
   CC_BUDDY_TELEGRAM_TOKEN=123456:AA…
   ```
3. Send your new bot any message from your phone, then find your numeric id:
   ```
   cc-buddy-bridge telegram-check
   ```
   It checks the token and prints `user id <number> (<first name>)` for whoever
   has messaged the bot. It prints ids and first names only, never what anyone
   wrote, and it reads without acknowledging, so the daemon still sees those
   messages later. Run it while the daemon's door is off: Telegram allows one
   poller per bot and answers a second with `409`.
4. Add your id and the switch, then restart the daemon:
   ```
   CC_BUDDY_TELEGRAM_OWNER=<your id>
   CC_BUDDY_TELEGRAM=1
   ```
   The log says `telegram: listening for 1 owner id(s)`.

All three are required. The switch without a token is off; the switch and a
token without an owner id is off. A door with no allowlist never opens.

| Variable | Default | What it does |
|---|---|---|
| `CC_BUDDY_TELEGRAM` | `0` | The switch. |
| `CC_BUDDY_TELEGRAM_TOKEN` | unset | The bot token from @BotFather. Never logged. |
| `CC_BUDDY_TELEGRAM_OWNER` | unset | Numeric user ids allowed to text buddy, comma-separated. A `@username` is ignored: it can be changed and re-registered, a number cannot. |
| `CC_BUDDY_TELEGRAM_MODEL` | `gpt-6-astra` | The text brain. |
| `CC_BUDDY_TELEGRAM_ASK` | `0` | `1`: with the Claude relay on, every tool call is asked in the chat, not only the always-ask ones (never in bypass mode). |
| `CC_BUDDY_TELEGRAM_DRAFTS` | `1` | `0`: while Claude works on a relayed line, show "typing…" instead of the "Thinking…" bubble. |
| `CC_BUDDY_TELEGRAM_EFFORT` | `low` | Its reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). Hard questions go to `think_hard` instead. |
| `CC_BUDDY_WEB_SEARCH` | `openai` | How every brain searches the web ([below](#web-search)): `openrouter-exa`, `openai` (the hosted tool), `off`. |
| `CC_BUDDY_WEB_SEARCH_MODEL` | `openai/gpt-5.4-nano` | The OpenRouter model that carries the Exa results back (the cheapest with the web plugin, 2026-09-21). |
| `CC_BUDDY_WEB_SEARCH_RESULTS` | `5` | Results per search, 1 to 10 (Exa's first price tier). |
| `CC_BUDDY_COMPOSIO` | `0` | `1`: the owner's apps through Composio ([below](#the-apps-composio)). Needs `COMPOSIO_API_KEY` in the env file. |
| `CC_BUDDY_CODEX_BIN` | desktop app bundled Codex, then `codex` on PATH | Executable for computer-task delegation. Computer Use must be enabled in its installed plugins. |
| `CC_BUDDY_COMPOSIO_POLICY` | `gmail=read,googlecalendar=write,googledrive=ask` | What a WRITING app call may do, per toolkit: `read` refuses it, `write` runs it, `ask` is your yes/no in the chat (the default for any toolkit not named). Reads always run. |
| `CC_BUDDY_COMPOSIO_STATE` | `~/.config/cc-buddy-bridge/composio.json` | Where the session id is kept between restarts. |
| `CC_BUDDY_COMPOSIO_TIMEOUT_SECS` | `60` | One app call's timeout. |
| `CC_BUDDY_SECOND_BRAIN` | `0` | `1`: your own notes, todos and journals as a local markdown vault, captured from this chat ([second-brain.md](second-brain.md)). |
| `CC_BUDDY_VAULT` | `~/Documents/Second Brain` | The vault's folder (open it in Obsidian). |
| `CC_BUDDY_COMMAND_RISK` | `shadow` | The Auto Mode gate behind the Claude relay ([below](#the-auto-mode-gate-jev-judges-a-relayed-command)): `off`, `shadow` (judged and logged, never acted on), `ask` (a risky verdict is your yes/no). |
| `CC_BUDDY_CODE_ROOT` | `~/Documents` | Where `new claude` looks for project folders. |
| `CC_BUDDY_CODE_AREAS` | `personal,work` | The first question's answers: folders under the root, comma separated. |
| `CC_BUDDY_CLAUDE_TERMINAL` | `warp` if installed | `warp` or `terminal` (Terminal.app): where a new session opens. |

## Questions and permission prompts from Claude Code

With `claude on`, what Claude Code asks you reaches the chat in full, not just
as "Claude is waiting on you":

- **A question with choices** (AskUserQuestion) arrives with its options
  numbered: "Quit all? (1. Keep terminals / 2. Everything)". Tap an option's
  button, or reply with its number; either way the relay types the number into
  the dialog. One question gets buttons; several questions in one call are
  answered by number, one after another. The buttons stop working once you
  type to Claude, the turn ends, or the relay is switched. buddy's PreToolUse
  hook covers `AskUserQuestion` for this, and it only relays the question. A
  hook never answers it.
- **A permission dialog for any tool** (Edit, Write, WebFetch, an MCP tool)
  becomes a yes/no in the chat, through the `PermissionRequest` hook. It comes
  with **Allow** (green) and **Deny** (red) buttons. A tap answers it, and so
  does a short typed reply that is only a yes or a no ("yes", "go ahead",
  "no, leave it"). Anything else you type while it waits goes to Claude as
  usual, and the prompt keeps waiting. That includes a sentence that starts
  with a yes-word, like "ok, also update the README": it is a message for
  Claude, not an Allow. So is "buddy: yes": it goes to buddy and allows
  nothing. This holds from the moment the prompt is sent. After the answer, or after 240 s with
  none, the message changes to say "Allowed.", "Denied." or that the dialog on
  the Mac decides, and the buttons go. If you don't answer, the dialog stays on
  the Mac as before. If Telegram refuses the buttons, the prompt is plain text
  and your next message is the answer, as before. With the relay off, the hook
  does nothing. Turning the relay off, or moving it to another session, while
  a prompt waits ends that prompt: the dialog on the Mac decides, the message
  says so, and your next message goes to buddy, not to the old prompt.
- **How a reply is read** (`consent.py`). One rule covers every yes/no that
  gates an action: permission prompts, app actions and auto-browser approvals.
  It fails closed. A reply is a yes only if its first word is a yes-word
  ("yes", "y", "ok", "sure", "allow", "do it", "go ahead"…) *and* nothing in
  it takes that back, so "yeah no", "ok wait" and "sure, but don't" are not
  yeses. It is a no if its first word is a no-word. Anything else approves
  nothing, and a permission prompt goes back to the dialog on the Mac.
- The general "Claude is waiting on you" notice is skipped for 20 s after the
  question itself was sent, so you don't get it twice.

`cc-buddy-bridge install` registers both hooks. Claude Code sessions that were
already open pick them up when they restart.

## Start a coding session

`new claude` (also `start claude`, `claude new`, `/newclaude`) opens a new
terminal window on the Mac with a coding agent running in one of your project
folders. The steps are code, with no model call, and each one is a short text:

1. **Personal or Work?** Recent sessions are listed underneath as numbers, and
   a folder name works here too. Most of the time you already know the folder.
2. **General, or which folder?** `general` opens the area itself, a name opens
   that folder, and `list` shows the folders with a number for each. Numbered
   worktrees (`era-maker-213`) are folded under their base folder. Names are
   matched loosely, so `era maker` finds `era-maker`. If more than one folder
   matches, you get a numbered choice.

Every question comes with **tap buttons** under it (an inline keyboard):
`Personal` / `Work` and the recent sessions, then `List` / `General`, then one
button per folder. A tap does exactly what typing the button's text does, such
as `2. era-maker`, and the number picks. Only the latest question's buttons
work: a tap on an older one says "This button has expired." Typing still
works. If Telegram refuses inline buttons, the question comes with the old
one-time reply keyboard instead.

You can say everything in one message: `new claude work era hub api`. You can
also just ask in plain words ("open up era maker in work"), and the text brain
walks the same steps through its `start_coding_session` tool. `cancel` ends the
steps, and an unanswered question is dropped after five minutes.

**Which program starts** comes from the area, so buddy never asks: personal runs
`claude --dangerously-skip-permissions` and work runs
`era-code claude --dangerously-skip-permissions`. To switch for one session, say
the other one explicitly: "…but in claude instead of era code", "buddy in
era-code", "atlas with claude".

In Warp, buddy rewrites one launch configuration,
`~/.warp/launch_configurations/buddy-claude.yaml`, and opens it with
`warp://launch/buddy-claude`. Warp finds a configuration by its `name:`, not
by its file path. With `CC_BUDDY_CLAUDE_TERMINAL=terminal`, Terminal.app runs
`cd <folder> && <command>` instead. The folder is always one taken from the
listing, never text from the chat, and it is shell-quoted. After the window
opens, `claude on` connects the chat to it as usual.

`claude on` itself starts from the sessions that are running (the daemon knows
each one from its hooks). A session whose terminal was closed or crashed never
says goodbye, so before it lists them buddy checks the Mac: it looks for a
running `claude` process in each session's folder (`ps` and `lsof`, about
30 ms, run off the daemon's event loop; a message you send while it runs
waits and is routed right after). An npm install started through its `claude`
shim (`node …/bin/claude`) counts as a `claude` process too. A session with
no process there is left out and forgotten. If that
check fails or finds no `claude` at all, every session is listed, as before.
A session that started in the last 30 s, or one with a question still waiting,
is always listed.

- **One session:** the chat joins it at once.
- **Several:** "Which Claude session?" with a button per folder
  (`buddy`, `era-maker`) and `New claude`. A tap is the same as typing
  `claude on buddy` or `new claude`. The chat then
  follows only the session you picked: another session's text stays on the Mac.
- **None:** it walks the steps above, opens the terminal, and joins that session
  when it opens.

`claude on <name>` picks a running session by folder name. If no running session
matches, it starts that folder.

## Web search

Voice, Telegram and `think_hard` use OpenAI's built-in `web_search` tool by
default, including when an OpenRouter key is present. This restores the GPT
search behavior from before commit `881fa36`, at the owner's request after
Exa could not provide a live Seattle time reading.

`CC_BUDDY_WEB_SEARCH=openai` explicitly selects hosted GPT search; `off`
disables search. Exa remains an opt-in through `openrouter-exa`, which also
requires `OPENROUTER_API_KEY`. Only that mode uses the search model/results
settings above and returns a summarized answer through a function tool.
Restart the daemon after changing the setting so new voice sessions pick it up.

## The apps: Composio

Owner's decision, 2026-09-21: Gmail, Google Calendar, Google Drive and the rest
are reachable by API through [Composio](https://composio.dev) in seconds, where a
Mac task drives the screen for minutes. `composio_tools.py` keeps one Composio
*session* per owner (its user id is `telegram-<your id>`, so the connected
accounts are yours, never a chat's; the session id is persisted and resumed on
restart) and lends the session's meta tools to the text brain beside buddy's own:
`COMPOSIO_SEARCH_TOOLS` finds the right tool for a use case,
`COMPOSIO_MULTI_EXECUTE_TOOL` runs it, `COMPOSIO_MANAGE_CONNECTIONS` hands you a
sign-in link for an app you have not connected. The brain is told: a job inside
one of your apps is done there, not on the Mac.

**What may run, by code, before any call (`composio_tools.decide`):** a call that
only reads (`GMAIL_FETCH_EMAILS`, `GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS`,
`GOOGLEDRIVE_FIND_FILE`: a reading verb among the slug's words and no writing
verb) runs at once. A call that writes follows its toolkit's policy: **Gmail is
read only** (a send, reply, label or delete is refused, never asked; the brain
says so), **the calendar may write** (an event is created without a question),
and **everything else asks you first** in this chat, as a one-line "Run
SLACK_SEND_MESSAGE with to: …, subject: …? yes / no?" with **Allow** and **Deny**
buttons. A tap answers it, or a typed yes or no. With the relay off, your next
message answers it, as before. With the relay on, other text goes to Claude and
nothing runs until you answer. A call mixing toolkits takes the strictest. `CC_BUDDY_COMPOSIO_POLICY`
changes any of this. The remote code tools (Composio's sandbox bash and
workbench) always ask.

Proven live 2026-09-21 from this code path: Gmail, Google Calendar and Google
Drive connected; `GMAIL_FETCH_EMAILS`, `GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS`
and `GOOGLEDRIVE_FIND_FILE` each returned a real result with a Composio log id.
Install: `pip install -e ".[composio]"` (composio 0.22.0, composio-openai 0.22.0;
`composio-core` is deprecated). The key lives in the env file, never in source.

## The second brain

Owner's decision, 2026-09-21: "the second brain system is for my personal notes
and things of that sort I'll text to Telegram". A text is a note in seconds, in a
local markdown vault Obsidian opens, organised PARA+, with agent workflows (plan
my day, weekly review, triage my inbox, distill this) and context packs compiled
from it. Ask to add to, edit, remove an item from, or check off an existing note
or list; `edit_note` updates the same file after a versioned read, and
`undo_note` can restore its previous contents. Ambiguous matches and changed
versions require another read or clarification. The whole design, the capture rules and the pack format are in
[second-brain.md](second-brain.md). Ships off behind `CC_BUDDY_SECOND_BRAIN`.

## The Auto Mode gate: Jev judges a relayed command

While the Claude relay is on the daemon is bypass, and the regex list
(`matchers.py`) was the only thing that could stop a Bash command; on the owner's
Mac that list is empty (`matchers.toml`, 2026-09-05), so nothing could. The Jev
Engineering article (0xmovez, 2026-09-18) names the missing piece: a cheap
classifier that judges every tool call for risk before it runs. `typed_ask.py`
asks Jev four absolute yes/no questions about the command in one request, in its
own idiom: does it destroy existing data, does it leave the project or change the
system, does it publish or send, does it read secrets. Obvious secrets (a bearer
token, an `sk-`/`ak_`/`ghp_` key, a `KEY=value`, a `--password`) are redacted
before the command leaves, and the folder's name goes, never its path.

`CC_BUDDY_COMMAND_RISK`: `off` is the relay as it was; `shadow` (the default)
judges every relayed Bash command and writes the verdict beside the regex class
in the audit log (`source: jev_shadow`) without acting on it; `ask` turns a risky
verdict into your yes/no in the chat with Jev's reason ("[Jev: destroys data,
publishes or sends]"), allows a safe one, and allows a failed one (logged as
`jev_error`). Silence still defers to Claude Code's own flow, never denies. The
regex always-ask class is asked first and never sent to Jev.

The ship decision is `tools/command_risk_eval.py --check-default` on
`tests/fixtures/commands/` (74 tuning commands, 59 holdout, both author-written
so the holdout is a tuning set by this project's own rule). 2026-09-21: the
judgement held (0 risky commands judged safe on both sets; 1 of 39 and 2 of 29
harmless ones judged risky) and the clock did not (p50 1.5 s, p90 2.1 s through
OpenRouter's alpha endpoint against a one-second bar), so it ships `shadow`.
Flip it to `ask` if two seconds a command is a price you will pay.

## What you can text

- **Anything you would say at the desk.** Buddy answers in a line or three, with
  the earlier turns of this chat as context (the last 24) and, with memory on,
  your profile and today's talk on both channels ([Memory](#memory)).
- **A task for the Mac** — "open the calculator", "play my focus playlist". When
  the request is ambiguous (which account, what to look for once the page is
  open), buddy asks one question in the chat first and starts nothing on a
  guess; the planner has the same rule (`computer_agent.py` rule 11: the goal
  as given is the whole task, and a scan with no end in sight becomes a
  question, not more scrolling — live 2026-09-21, "open Amazon" turned into
  2.5 minutes of reading orders). While a task runs, your messages about it
  steer it. It
  runs the same `ComputerAgent` the voice starts, through the same reflex → lane
  → planner tiers ([routing](routing.md)). Buddy replies "On it" at once and
  texts the result when there is one. Starting a task costs one model call, not
  two: measured live on 2026-09-21, the second call that only produced "On it!"
  cost 1.9 s, so code says it instead.
- **One progress message per task.** The "On it" message has a red **Stop**
  button under it. Each step the task reports is added to that same message
  (it is edited, at most once a second), so a busy task no longer fills the
  chat. Steps are never cut short. When the message gets near Telegram's
  4096-character limit, the oldest steps scroll off; a step you never saw goes
  as its own message first. A step too long for the message comes whole as its
  own message, and the progress message says "(a long step, sent in full
  below)". A tap on Stop stops that task, as texting `stop` does. It stops the
  task even inside a Codex chat, where the typed word would interrupt Codex
  instead. When the task ends, the message says "Finished" or "Stopped" and
  the button goes. The result comes as a new message, so your phone notifies,
  and it is a reply to the message that asked for the task. If Telegram will
  not edit the message, each step comes as its own message, as Codex steps did
  before (steps from other providers reach the chat only since 2026-09-23). Steps
  still waiting when the work ends come as one message before the result, so
  the last step is never lost. If it will not take the reply link, the result
  comes without it. A restart closes an open progress message ("Stopped." for
  a task, "Closed." for a Codex turn) so no dead Stop button is left behind.
- **The `/` menu.** At startup buddy sets its code words as bot commands in
  your own chat only (`setMyCommands`, scoped to your chat): `/claude_on`,
  `/claude_off`, `/new_claude`, `/codex`, `/rundown`, `/screenshot`,
  `/stealth`, `/wake` and `/stop`. Each works exactly like the typed word. If
  Telegram refuses the menu, the words still work when typed.
- **"typing…"** shows while buddy works on a reply, for the whole turn, not only
  the first 5 seconds. It is sent again every 4 s and stops when the reply goes
  out. `think_hard` keeps it up for up to 5 minutes. It pauses while buddy waits
  for your answer to a question.
- **`stop`** (or `cancel`, `/stop`, `/cancel`, or the **Stop** button) — stops
  the running task. This is code, not a model call: it works when the model is
  down or mid-turn.
- **A photo** — "send me a picture of my desk". The robot snaps, the diary keeps
  and captions it as it does for the voice, and the picture arrives in the chat.
- **The screen** — "screenshot", "show me the screen", "what's on the screen".
  A message that is only that is answered by code, no model call, mid-task or
  not (like `stop`). For Codex browser tasks, it sends the last captured view
  from that task's browser tab, labeled with its tab ID. Completed browser tasks
  automatically include this picture. A missing capture is reported; the desktop
  is never substituted. For other tasks, macOS `screencapture` sends the screen
  as a photo and requires Screen Recording for the daemon's Python. Longer
  requests go through the brain's `screenshot` tool. Native tasks that ask to
  *see* something include the desktop picture when they finish.
- **Website access** — `CC_BUDDY_CODEX_SITE_ACCESS=allow` accepts the runtime's
  ordinary site-access prompts without another Telegram question. Default `ask`.
  Native-app grants, strict safety reviews, uploads, full CDP, authentication
  handoffs, and other action approvals are separate.
- **A file** — "send me the report on my Desktop", "what's the newest thing in
  Downloads". `send_file` sends any regular file under your home folder as a
  Telegram document (50 MB limit); `list_files` lists a folder newest first so
  buddy can find the one you mean. Never a hidden path: `~/.ssh`, `~/.config`,
  `~/.aws` and every dotfile are refused after symlinks are followed, and
  nothing outside your home can be named at all. The log gets the size, never
  the name.
- **The robot itself.** A text is a word said to buddy: "look left", "look at
  me", "what do you see", "find my mug", "look around", "start taking notes" /
  "stop taking notes", "go explore", "mute", "remember that …" each go straight
  to the same tool the voice has (`move_head`, `look`, `find`, `look_around`,
  `take_notes`, `go_explore`, `set_sound`, `remember`). Only `lesson` (the
  learning workspace, bound to the microphone) stays voice-only.
- **Receipts are reactions.** Where buddy used to answer with a one-line
  acknowledgement, it now reacts to your message (owner, 2026-09-23): ✍ for a
  note saved to the second brain, 🏆 for a fact starred with "remember that …",
  👍 for a line typed into Claude, a message that steers a running Codex
  turn, or one that starts a Codex turn (its progress message says "Sent to
  Codex." as well). A turn that only saved or starred makes no second model call.
  A message holds one reaction, so a turn that both starred a fact and saved a
  note gets the 🏆 and says where the note went in words. If the
  reaction fails, the line it stands for is sent instead ("Saved to …",
  "Starred for good.", `Typed.`, "Sent to Codex."). Anything that carries
  information is still a message.
- **What the robot shows.** While the chat drives a task the robot acts it
  out as it does for the voice: the phase on its face, each progress line and
  the result as a caption on its screen. Stealth hides only the robot: the
  progress message in the chat still updates.
- **`stealth mode`** — a code word (also "play dead", "act asleep"); "wake
  up" / "stealth off" ends it. Asleep, the robot's face goes idle and stays
  there, nothing is shown on its screen, and the head, `find`, `look_around`
  and `go_explore` refuse ("stealth mode: the robot is playing asleep"); the
  camera may still `look`, tasks and files still work. Zero model calls to
  enter or leave it.
- **`codex on` / `codex off`** — `codex on` sends only the names of accessible
  local folders saved in Codex, with debrief folders hidden, and a button for
  each: a tap is the same as typing `codex use <folder>`. Duplicate names show full paths, on the
  buttons too (`~/work/buddy`, `~/personal/buddy`). No task titles,
  numbers, IDs or old prompts appear. `codex buddy` or `codex use buddy` starts a
  **fresh chat** in that folder. Each folder selection creates a new conversation.
  Full folder paths also work; unknown or inaccessible folders cannot start a chat.
  The Mac needs to be awake, but no existing task or open app window is required.

  Plain messages (or `codex: message`) continue the new chat, retaining its context.
  During a running turn they steer it, and a 👍 on your message confirms the steer. Each message that starts a turn gets one
  progress message under **Codex** ("Sent to Codex.", then Codex's public steps,
  edited in place, whole, with the same scroll-off rule as a task) with a
  **Stop** button that interrupts that turn, like `stop`. When the turn ends,
  the progress message says "Finished", or "Stopped" if you stopped it. If Codex
  refuses the stop (its turn is still starting), the Stop button comes back. If
  the message never reaches Codex, or the chat disconnects, the progress
  message says so ("Codex did not take it.", "Closed.") and loses its button.
  A step Codex writes just before the turn ends arrives before the answer. The final
  answer is a new message replying to yours. Reasoning and tool output stay out
  of Telegram. Browser tasks
  send the captured image from their own tab using the same validation as Buddy's
  computer tasks. `codex status` reports the folder, `stop` interrupts the turn
  while keeping the chat, and `codex off` stops active work and closes the session.
  `buddy: ...`, screenshot and stealth commands still address Buddy. Switching to
  `claude on` closes the Codex session. Another owner chat cannot control it.

  If startup fails, the next ordinary message goes to Buddy. A failed work message
  is never replayed through Buddy or automatically retried. After restart, select
  a folder again for a fresh chat. Codex stores the conversation in its own thread
  storage; Buddy keeps no extra raw-chat log. Diagnostics record connection stages
  and errors without message text. Earlier raw Telegram texts cannot be recovered
  from those logs.

  Implementation: `codex_chat.py` owns a new persistent thread through the public
  `codex app-server` JSON protocol. It reads saved local project roots from
  `~/.codex/.codex-global-state.json`, deduplicates them and checks filesystem
  access. It does not select or resume any existing desktop task. Routine file
  work is scoped to the selected workspace; approval policy is `on-request`.
  Command approvals show the command and folder in Telegram and require an exact
  affirmative reply. Unsupported permission forms are declined. Native app
  persistence and ordinary website access use the existing Computer Use adapter.
  The model uses Codex's configured default. Desktop-only app tools and the
  built-in browser are unavailable in this standalone runtime; Chrome is the
  default task browser. This does not bypass ChatGPT's native UI restriction.

- **`claude on` / `claude off`** — the Claude Code relay, explicit only. While
  on: the chat shows what the terminal prints in white, as it happens: what
  Claude says (the text blocks of each assistant message, from the transcript
  tailer, under a bold "Claude" title with the repo's folder name beneath it,
  its paragraphs, bullets and code kept), batched every 1.2 s into one message;
  a message over 1500 characters is cut at a paragraph and says the rest is in
  the terminal. The gray
  lines stay on the Mac: thinking is never read, and a tool call and the tail
  of its result are not forwarded (owner, 2026-09-21). "Claude is waiting on
  you" arrives when a session blocks on you, and **what you text goes into
  the session's terminal**: the chat is the terminal. Plain text is raised into that
  session (`focus_terminal.py`) and typed with Return through System Events.
  A 👍 reaction on your message says it went in; buddy sends no "typed" line
  (owner, 2026-09-23), and only if the reaction fails does a short `Typed.`
  arrive instead. Then a "Thinking…" bubble shows in the chat while Claude
  works (`sendMessageDraft` with empty text). It comes back after each of
  Claude's messages and stops when Claude asks you something, waits on you,
  or ends its turn (the Stop hook), or 5 minutes after Claude last said
  anything. Claude's words never go into the bubble: each one is a real
  message, as before. If Telegram refuses the bubble once, buddy uses
  "typing…" from then on, which stops at Claude's first words.
  `CC_BUDDY_TELEGRAM_DRAFTS=0` keeps "typing…" from the start;
  `buddy: <text>` talks to buddy instead, and buddy's code words (`stop`,
  `screenshot`, `stealth mode`, `claude off`) still work. **The relay is
  bypass**: while it is on, the daemon's pretooluse hook allows a tool call
  (and an out-of-repo Read) without asking, the way `bypassPermissions` would,
  because a prompt on the Mac has nobody at it. What Claude needs from you
  still arrives: a question it asks (`AskUserQuestion`, under a "Claude asks"
  title: "Which database? (Postgres / SQLite)"), and a command on your own
  always-ask list (`rm`, `sudo`; `matchers.py`) as a yes/no with Allow and
  Deny buttons, titled "Claude asks to run Bash" with the command as a code
  block. A tap or a short typed yes or no answers it; any other text, even
  one starting with "ok" or "no", still goes to Claude.
  Silence there defers to Claude Code's own flow, never denies. With
  `CC_BUDDY_TELEGRAM_ASK=1` every call is asked that way. Off by default and
  off again after "claude off": nothing from the terminal leaves the Mac
  until you ask, and the Mac asks as it always did.
- **One shape for every message** (`telegram_format.py`). A message that is
  not buddy's own reply opens with a bold title that says whose it is or what
  it is: "Task result" or "Task failed" with the goal in italics under it, "The
  task asks", "Before I do that" (an app action to confirm), "Claude" with the
  repo under it, "Claude asks", "Claude is waiting on you", "Claude asks to
  run Bash". Then a blank line, then the body in short paragraphs: a model's
  markdown becomes Telegram's own bold, italics, `•` bullets, `code` and code
  blocks, never raw stars and hashes. Everything goes in HTML parse mode with
  `&`, `<` and `>` escaped in content; a message over 4096 characters is split
  on a paragraph boundary with its tags closed and reopened at the cut; a
  piece Telegram refuses to parse is sent again as plain text, so nothing is
  lost to markup. Buddy's own replies and one-liners ("Stopped.", "On it.")
  carry no title: in a private chat they are already buddy's.
- **No emoji, no dashes.** Every outgoing message is stripped in code
  (`telegram_format.plain`): emoji blocks and their joiners go, an em or en
  dash between words becomes a comma. The prompt says so too; the code makes
  it true.
- **An answer.** When a task needs a yes before something consequential, the
  question arrives in the chat. Your next message is the answer, and only the
  answer. A question without buttons opens the reply box on itself, so the
  answer is clearly a reply to it. No reply in three minutes reads as no. A question whose answers are
  known also has buttons: **Allow** / **Deny** for a yes/no (app actions,
  Chrome access, a Codex command), **Yes** / **No** for "Should I go ahead…",
  and Codex's app-access choices (**Allow once**, **Allow for this task**,
  **Always allow**, **Deny**). A tap is the same answer as typing it. While the
  Claude or Codex relay is on, a question with buttons takes only a tap, a
  plain yes or no, or a button's words typed ("allow for task"). A question
  whose asker goes away first (a stopped task, a Chrome dialog answered at the
  Mac) ends saying "Closed without an answer here.", never that silence was a
  no. A question longer than one Telegram message loses its buttons when
  answered and the outcome comes as a short message below it. Other text
  goes to Claude or Codex, and the question keeps waiting. With no relay on,
  your next message is still the answer, and an image is refused until you
  answer. A typed yes or no in any wording ("ok", "nope") is handed on as the
  buttons' own yes or no, so it does what the tap does. A tap on a picker
  (the new-session tree, say) is never the answer to a different question
  that happens to be waiting. A task's question asked while a Claude
  permission prompt waits is answered first; the prompt then takes a typed
  yes again. After the answer the question changes to say what was chosen,
  and its buttons go.

One agent drives the mouse at a time. While a spoken conversation is open, or a
task it started is running, a texted task is refused ("the Mac is theirs until
that conversation ends"); while a texted task runs, the voice refuses to start
another. Touching the robot to cancel a task works for a texted task too.

After ten quiet minutes the chat is over: its turns are cleared from RAM and its
transcript gets a close marker. Nothing is handed anywhere, because with memory on
every line was already written down the moment it was typed or sent.

## Memory

With `CC_BUDDY_MEMORY=1` the text brain shares one memory with the voice. The full
design is in [memory.md](memory.md); what matters for the chat:

- **Every line is in the day's transcript as it happens**
  (`~/.config/cc-buddy-bridge/memory/transcripts/<day>.jsonl`): what you typed,
  buddy's replies, commands, relayed Claude lines, image turns and the tool results
  buddy answered from. A restart or a failed model call loses nothing, and a voice
  conversation a minute later sees this chat.
- **Every turn's prompt carries** the profile (`records/profile.md`, re-read each
  turn, up to 6,000 characters, with the stars the nightly dream has not absorbed
  yet on top) and today's lines on both channels (up to 32,000 characters, minus
  what this chat's history already shows). The opening brief rides in the turn note.
- **Four memory tools:** `memory_search` (record lines, mem0 memories found by
  meaning, and the words said, each dated), `memory_read` (a record by id, or a day
  or a stretch of one), and the two-step forget, `forget_preview` then
  `forget_apply`, which runs only after you confirm in a new message.
- **"remember that …"** goes through the `remember` tool into
  `records/starred.md`, and counts from your next message. A 🏆 on your message is
  the confirmation.
- **The brain never writes what it knows.** The records and the mem0 index are
  rewritten once a night by the dream, from the transcript.

With memory off there is no transcript, no profile, no memory tool, and the prompt
is what it was before memory existed.

## The rules, and why they are code

This door reaches a Mac with full computer control. None of these is a prompt
instruction; each is a branch in `telegram.accept` or the inlet, with a test.

| Rule | Why |
|---|---|
| Only an owner id, in a private chat, reaches a model. Anyone else gets no reply at all. | A reply tells a stranger the bot is alive. A group the owner is in carries other people's words. |
| A forwarded message never reaches a model (you get one fixed line back). | It is the easiest way to put someone else's instructions in front of an agent that can click. |
| A message older than two minutes when it arrives is dropped. | Telegram holds undelivered messages for a day. A task texted while the daemon was down must not run when it comes back. |
| Only your next message can answer a task's question. | "Only the human approves" ([routing](routing.md)) has to hold over chat too. |
| A button tap acts only when it is from an owner id, in that owner's private chat, on a button this run of the daemon made and still needs. Every tap is answered; a stranger's tap is not. The owner's own tap outside their private chat is answered empty and does nothing. | A button's data is only a short key, and what it means stays on the Mac. A button from before a restart, or one already used, says "This button has expired." and does nothing. |
| Edited messages, channel posts, other bots, stickers and voice notes reach no model. | Only new text from the owner is a request. |
| A file leaves only from your home folder, never from a hidden path, symlinks followed first. | A chat that can reach a Mac must never be a way to read its secrets. |
| What you wrote is never logged; neither is the token. | The log gets counts, seconds and numeric ids. The token is part of every Bot API URL, so HTTP errors are rewritten before they are raised (`BotApiError` never carries a URL) and every log record in the process is checked for the token as it is made (`hide_token`) — httpx logs each request line at INFO. |
| `401`, `404` or `409` from Telegram stops the door with one log line. | A wrong token or a second poller will not fix itself; spinning on it helps nobody. Anything else backs off (1 s doubling to 60 s) and resumes. |

A stranger's first message produces one log line — `telegram: dropped a message
(stranger) from user id N` — once per id, which is also a second way to find
your own number.

## What leaves the Mac

Your messages and buddy's replies pass through Telegram's servers (bot chats are
not end-to-end encrypted). Each turn is one or more Responses API calls with
`store=False`: the turn is stateless, the model's own tool calls and encrypted
reasoning are sent back each round instead of a `previous_response_id`, and
Responses API conversation state is not stored. This is not a guarantee of zero
data retention under the provider's other policies. A texted task sends screenshots to
the planner exactly as a spoken one does. The Ask Claude Mini App has its own path: questions go
to Anthropic, and traffic passes through a Cloudflare tunnel
([the Mini App](miniapp.md#what-leaves-the-mac)).

## Not done

- No evaluation set for the text brain exists, so it ships off (`GATES.md`).
- Voice notes, animations and non-image incoming documents are not supported.
- One owner conversation. Several owner ids share one chat history.
