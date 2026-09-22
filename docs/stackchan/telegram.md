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
                                                                 remember · think_hard · web_search (+ memory_search · memory_get)
                                                                                   │ start_task
                                                                                   ▼
                                              computer_agent.py — the same agent, tiers and approval rules as the voice
```

There is no port to open, no webhook and no public URL. The daemon calls
Telegram; nothing calls the Mac.

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
| `CC_BUDDY_TELEGRAM_EFFORT` | `low` | Its reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). Hard questions go to `think_hard` instead. |
| `CC_BUDDY_WEB_SEARCH` | `openrouter-exa` with an `OPENROUTER_API_KEY`, else `openai` | How every brain searches the web ([below](#web-search-exa-through-openrouter)): `openrouter-exa`, `openai` (the hosted tool), `off`. |
| `CC_BUDDY_WEB_SEARCH_MODEL` | `openai/gpt-5.4-nano` | The OpenRouter model that carries the Exa results back (the cheapest with the web plugin, 2026-09-21). |
| `CC_BUDDY_WEB_SEARCH_RESULTS` | `5` | Results per search, 1 to 10 (Exa's first price tier). |
| `CC_BUDDY_COMPOSIO` | `0` | `1`: the owner's apps through Composio ([below](#the-apps-composio)). Needs `COMPOSIO_API_KEY` in the env file. |
| `CC_BUDDY_COMPOSIO_POLICY` | `gmail=read,googlecalendar=write,googledrive=ask` | What a WRITING app call may do, per toolkit: `read` refuses it, `write` runs it, `ask` is your yes/no in the chat (the default for any toolkit not named). Reads always run. |
| `CC_BUDDY_COMPOSIO_STATE` | `~/.config/cc-buddy-bridge/composio.json` | Where the session id is kept between restarts. |
| `CC_BUDDY_COMPOSIO_TIMEOUT_SECS` | `60` | One app call's timeout. |
| `CC_BUDDY_SECOND_BRAIN` | `0` | `1`: your own notes, todos and journals as a local markdown vault, captured from this chat ([second-brain.md](second-brain.md)). |
| `CC_BUDDY_VAULT` | `~/Documents/Second Brain` | The vault's folder (open it in Obsidian). |
| `CC_BUDDY_COMMAND_RISK` | `shadow` | The Auto Mode gate behind the Claude relay ([below](#the-auto-mode-gate-jev-judges-a-relayed-command)): `off`, `shadow` (judged and logged, never acted on), `ask` (a risky verdict is your yes/no). |

## Web search: Exa through OpenRouter

Owner's decision, 2026-09-21: "use openrouter exa, this is a must". Wherever buddy
searched the web with OpenAI's hosted tool (this text brain, `think_hard`, the
voice backend's delegation) it now offers its own `web_search` function tool
(`websearch.py`). A call is one OpenRouter chat completion on a cheap model with
the `web` plugin on the `exa` engine; the reply's `url_citation` annotations come
back as sources (title, url, snippet) beside a four-sentence answer, and the model
quotes from those. Exa is $0.007 a search for up to ten results plus the small
model's tokens (openrouter.ai/docs/features/web-search, 2026-09-21). Without an
OpenRouter key the hosted OpenAI search is offered instead, so nothing goes dark.
`think_hard` may search, read and search again, three rounds at most, before it
answers.

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
SLACK_SEND_MESSAGE with to: …, subject: …? yes / no?" that only your next message
answers. A call mixing toolkits takes the strictest. `CC_BUDDY_COMPOSIO_POLICY`
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
from it. The whole design, the capture rules and the pack format are in
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
  what it remembers of your conversations in its prompt (`recall.opening_brief`)
  and the earlier turns of this chat as context (the last 24).
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
- **`stop`** (or `cancel`, `/stop`, `/cancel`) — stops the running task. This is
  code, not a model call: it works when the model is down or mid-turn.
- **A photo** — "send me a picture of my desk". The robot snaps, the diary keeps
  and captions it as it does for the voice, and the picture arrives in the chat.
- **The screen** — "screenshot", "show me the screen", "what's on the screen".
  A message that is only that is answered by code, no model call, mid-task or
  not (like `stop`): macOS `screencapture` (the same call PyAutoGUI makes),
  sent as a photo. Longer requests go through the brain's `screenshot` tool. A task whose request asked
  to *see* something ("give me a screenshot of the headline") arrives with the
  screen it left, so you can check the result; a task that did not ask gets
  the words only. Needs Screen Recording for the daemon's python, which the
  desktop worker already has.
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
- **What the robot shows.** While the chat drives a task the robot acts it
  out as it does for the voice: the phase on its face, each progress line and
  the result as a caption on its screen.
- **`stealth mode`** — a code word (also "play dead", "act asleep"); "wake
  up" / "stealth off" ends it. Asleep, the robot's face goes idle and stays
  there, nothing is shown on its screen, and the head, `find`, `look_around`
  and `go_explore` refuse ("stealth mode: the robot is playing asleep"); the
  camera may still `look`, tasks and files still work. Zero model calls to
  enter or leave it.
- **`claude on` / `claude off`** — the Claude Code relay, explicit only. While
  on: the chat shows what the terminal prints in white, as it happens: what
  Claude says ("Claude: …", the text blocks of each assistant message, from
  the transcript tailer), batched every 1.2 s into one message. The gray
  lines stay on the Mac: thinking is never read, and a tool call and the tail
  of its result are not forwarded (owner, 2026-09-21). "Claude is waiting on
  you" arrives when a session blocks on you, and **what you text goes into
  the session's terminal**: the chat is the terminal. Plain text is raised into that
  session (`focus_terminal.py`) and typed with Return through System Events;
  `buddy: <text>` talks to buddy instead, and buddy's code words (`stop`,
  `screenshot`, `stealth mode`, `claude off`) still work. **The relay is
  bypass**: while it is on, the daemon's pretooluse hook allows a tool call
  (and an out-of-repo Read) without asking, the way `bypassPermissions` would,
  because a prompt on the Mac has nobody at it. What Claude needs from you
  still arrives: a question it asks (`AskUserQuestion`, shown as "Claude asks:
  Which database? (Postgres / SQLite)"), and a command on your own always-ask
  list (`rm`, `sudo`; `matchers.py`) as a yes/no that only your next message
  answers; silence there defers to Claude Code's own flow, never denies. With
  `CC_BUDDY_TELEGRAM_ASK=1` every call is asked that way. Off by default and
  off again after "claude off": nothing from the terminal leaves the Mac
  until you ask, and the Mac asks as it always did.
- **No emoji, no dashes.** Every outgoing message is stripped in code
  (`telegram.plain`): emoji blocks and their joiners go, an em or en dash
  between words becomes a comma. The prompt says so too; the code makes it
  true.
- **An answer.** When a task needs a yes before something consequential, the
  question arrives in the chat. Your next message is the answer, and only the
  answer. No reply in three minutes reads as no.

One agent drives the mouse at a time. While a spoken conversation is open, or a
task it started is running, a texted task is refused ("the Mac is theirs until
that conversation ends"); while a texted task runs, the voice refuses to start
another. Touching the robot to cancel a task works for a texted task too.

After ten quiet minutes the chat's turns are handed to the same conversation
memory a spoken conversation feeds (`chat_memory.py`), so tomorrow's "hey buddy"
knows what you texted about.

## The memory layer: records

A spoken conversation opens with one clause of memory (`recall.opening_brief`),
which is right at the desk. A text chat needs more: "what was that restaurant"
is a lookup, not a greeting. `records.py` is that layer, and it follows the shape
Instinct (the iMessage assistant) was found to use — reverse-engineered by
Dhravya Shah, 2026-09-20 — for one property: **the agent never writes its own
memory.**

```
~/.config/cc-buddy-bridge/debrief/          the store chat_memory.py already keeps, now a git repository
├── sessions/<day>/…                        distilled notes, one per conversation (spoken or texted)
├── <day>-<slug>.md                         the curated day
└── records/
    ├── profile.md                          the one-pager every text turn starts with
    ├── dining.md  cafe-nero.md  sam.md …   one typed record per thing
    └── .reconciled                         which days have been read
```

A record is a markdown file the owner can open and edit:

```
---
id: dining
type: preference
aliases: [food, lunch, restaurants, takeout]
updated: 2026-09-20
---
- Now prefers ramen for lunch (changed 2026-09-20; was pasta).
- Usual lunch spot is [[cafe-nero]] (said 2026-09-18).
```

Types are `preference`, `person`, `organization`, `project`, `place`, `routine`,
`conversation`. Facts carry their date; a wrong fact becomes a dated correction,
never a silent edit. `[[id]]` links records to each other. **Aliases are the
index**: search is keyword matching over ids, aliases and fact lines — no
vectors — so every record carries the words you might use for it.

What the text brain gets, all read-only:

- **The profile** in its instructions, every turn: three sections (life context,
  acting on your behalf, how you like to talk) and an index of the records with
  their aliases, so it knows what it can look up before it looks. Re-read from
  disk per turn, so a hand edit counts at once. Capped at 6,000 characters.
- **`memory_search(query)`** — the matching fact lines with their record ids.
- **`memory_get(id)`** — one whole record.

The only writer is the **nightly reconcile**: once a day's curated file exists
(`chat_memory.py` writes it the next day), the model is shown every current
record and that day's notes and returns the records that change, whole — it
merges examples into traits, drops incidental detail, adds dated corrections —
plus the profile. Before the result is written, whatever is on disk (your hand
edits included) is committed as its own git commit; the reconcile is a second
commit. So an old fact is one `git log` away, a wrong reconcile is one revert,
and the model's diff is exactly what it changed. "Forget the cafe" removes the
file from the working tree; history keeps it. The first live run on this Mac's
real notes (2026-09-21, gpt-5.4-nano, two days) produced eight typed records
and a three-section profile.

| Variable | Default | What it does |
|---|---|---|
| `CC_BUDDY_RECORDS` | `0` | The switch: reconcile each curated day into records, and give the text brain the profile and the two memory tools. |
| `CC_BUDDY_RECORDS_MODEL` | `gpt-5.4-nano` | The reconciling model (one call per day, `store=False`). |

Off, nothing changes: no records directory, no git repository, the text brain
gets the one-clause brief only. On, the store becomes a git repository (`git`
must be installed; without it records are still written, without history).

## The rules, and why they are code

This door reaches a Mac with full computer control. None of these is a prompt
instruction; each is a branch in `telegram.accept` or the inlet, with a test.

| Rule | Why |
|---|---|
| Only an owner id, in a private chat, reaches a model. Anyone else gets no reply at all. | A reply tells a stranger the bot is alive. A group the owner is in carries other people's words. |
| A forwarded message never reaches a model (you get one fixed line back). | It is the easiest way to put someone else's instructions in front of an agent that can click. |
| A message older than two minutes when it arrives is dropped. | Telegram holds undelivered messages for a day. A task texted while the daemon was down must not run when it comes back. |
| Only your next message can answer a task's question. | "Only the human approves" ([routing](routing.md)) has to hold over chat too. |
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
nothing you texted is kept on OpenAI's side. A texted task sends screenshots to
the planner exactly as a spoken one does.

## Not done

- No evaluation set for the text brain exists, so it ships off (`GATES.md`).
- Text only: voice notes and pictures you send are answered with one fixed line.
- One owner conversation. Several owner ids share one chat history.
