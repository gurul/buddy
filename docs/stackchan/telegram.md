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
                                                                 screenshot · send_file · list_files · think_hard ·
                                                                 web_search (+ memory_search · memory_get)
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
| `CC_BUDDY_TELEGRAM_EFFORT` | `low` | Its reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). Hard questions go to `think_hard` instead. |

## What you can text

- **Anything you would say at the desk.** Buddy answers in a line or three, with
  what it remembers of your conversations in its prompt (`recall.opening_brief`)
  and the earlier turns of this chat as context (the last 24).
- **A task for the Mac** — "open the calculator", "play my focus playlist". It
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
