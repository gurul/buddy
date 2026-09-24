# What buddy remembers of talking with you

buddy remembers two kinds of thing, and keeps them apart. What it **saw** is the
diary ([personality.md](personality.md)): thoughts about the room, under
`~/.config/cc-buddy-bridge/notes/`. What was **said** — by voice, by Telegram, in a
meeting it was asked to record — is this page: one memory folder, three stores and
an archive, one switch, one nightly dream, and one forget that reaches all of it.

It ships **off**. `CC_BUDDY_MEMORY=1` turns it on. Off, nothing said is written
anywhere, no brain gets a profile or a memory tool, and every prompt is
byte-identical to a session with no memory at all.

## Why it looks like this

Until 2026-09-23 a conversation lived in RAM until it closed, went to one model
call to be distilled into a note, and was dropped. One failed call, one restart or
one crash and the words were gone. A Telegram chat could not see the voice chat of
an hour ago, Telegram was remembered only after ten quiet minutes, the voice could
not look anything up, and there were session notes, day files, an index, a
highlights file, records and a mem0 index, each written by a different loop. The
owner asked for it simpler (owner, 2026-09-23: "too many stores", "reconcile and
minimize") and for buddy to "dream every night so memories can be fixed and
updated". So:

- **Every word is written the moment it is said.** A restart, a crash or a failed
  model call costs nothing, and the other channel sees it at once.
- **One nightly dream** turns a finished day into the records and the index. It is
  the only automatic writer of what buddy "knows".
- **One facade** (`memory.py`) gives both brains the same context and the same
  tools, so what buddy knows does not depend on how the owner reached it.

## The folder

```
~/.config/cc-buddy-bridge/memory/            0700   (CC_BUDDY_MEMORY_DIR moves it)
├── transcripts/                              0700   SOURCE: every word (transcripts.py)
│   ├── <YYYY-MM-DD>.jsonl                    0600   one JSON line per turn, both channels
│   ├── meetings/<YYYY-MM-DD>/<HHMM>-<slug>.md 0600  meeting notes buddy was asked to take (notes.py)
│   └── .metadata_never_index                        keeps Spotlight out
├── records/                                  0700   TRUTH: its own sealed git repository (records.py)
│   ├── profile.md                            0600   the one-pager every conversation starts with
│   ├── starred.md                            0600   what the owner said to remember, one dated line each
│   ├── <id>.md                               0600   one typed record per thing
│   ├── days/<YYYY-MM-DD>.md                  0600   the dream journal: what happened, learned, still open, corrected
│   └── .dreamt                               0600   the days already dreamt, one per line
├── mem0/                                     0700   INDEX: search by meaning, rebuilt from transcripts (mem0_memory.py)
│   ├── qdrant/  history.db  home/            0700 / 0600
│   └── ingested_convs                        0600   the conversations the index has read
├── archive/                                  0700   the old debrief store, moved here once, still searched
└── .migrated                                 0600   the one-time move's counts
```

Modes are set in code, not left to the umask: folders are `chmod 0700`, files are
opened `0600` and `fchmod`ed again. The mem0 folder is re-tightened every time it
opens, because mem0 creates files of its own.

**A day starts at 04:00, not midnight.** A conversation at 01:00 belongs to the
evening it continued, in the transcripts, in a star's date and in the dream.

**The words never reach a git object, a cloud folder or a backup.** The transcripts
refuse (one WARNING naming the rule, then nothing is written) a folder that is
inside a git work tree, under `~/Documents`, `~/Desktop`, iCloud Drive or
`~/Library/CloudStorage`, a symlink, or not owned by this user. So
`CC_BUDDY_MEMORY_DIR` must point somewhere outside those. The records' repository
is the `records/` folder itself, never the memory root, so a transcript can never
be committed.

### The three stores

| Store | Holds | Written by | Read by |
|---|---|---|---|
| **transcripts** | every line said or typed, with time, channel, conversation and speaker; what tools answered; meeting notes | the voice session and the Telegram inlet, live; the meeting recorder; a `/buddy/memory/remember` bus line (as a channel `bus` line) | both brains (today's block, `memory_search`, `memory_read`, `recent_conversation`); the dream |
| **records** | typed records with dated facts and `[[id]]` links, the profile, the stars, the journal | the dream; "remember that" (to `starred.md` only); forget | both brains (profile, `memory_search`, `memory_read`); the widget (stars, journal) |
| **mem0 index** | facts about the owner, extracted by meaning, each with its day and channel | the dream (`ingest_day`, `tidy`); forget | `memory_search` (as `recalled`) |
| archive | the retired debrief store: session notes, day files, INDEX.md, HIGHLIGHTS.md and its git history | the migration, once; forget | `memory_search` (as `said`, channel `note`) |

A transcript line on disk:

```json
{"ts": "2026-01-01T18:30:05+00:00", "ch": "telegram", "conv": "t-1767292205000-a1b2", "who": "owner", "kind": "say", "text": "…"}
```

`ch` is `voice`, `telegram` or `bus`; `who` is `owner`, `buddy`, `claude` or
`system`; `kind` is `say`, `tool`, `command`, `relay`, `image` or `close`. Nothing
is pruned: the owner keeps every day. A `bus` line is kept for the dream and for
search (labelled "sent"), but it is never in a prompt's today block, never in
`recent_conversation`, and never the "last talked" time: it is not the owner
speaking.

## Who writes what

Nothing else writes:

1. **Speaking and typing** → `transcripts.append`, the moment a turn closes (voice)
   or a message is typed or sent (Telegram). Both sides, the results buddy answered
   from (a search, a look, `think_hard`, a memory lookup, a finished task), and a
   `close` marker when the conversation ends: on goodbye, after ten quiet minutes
   on Telegram, and at daemon shutdown for any conversation still open. **Think out
   loud (a lesson) writes nothing**: a learner's words are never kept.
2. **"Remember that"** — said out loud, or the Telegram `remember` tool —
   → `records.star` → one dated line in `records/starred.md`. It counts on the next
   message: the profile puts every star the dream has not absorbed yet on top of
   itself, verbatim. The notes widget shows the stars; it does not write them.
3. **The nightly dream** → the records, the profile, the day's journal and the mem0
   index. It is the only automatic writer of the records, which keeps their first
   rule: **the brains read, they never write.** A model that promotes its own
   mid-conversation conclusions into what it "knows" launders a guess into a fact.
4. **Forget** (the owner confirms) → removes matching words from every store.

## What each brain has

| | Telegram text brain | Voice: the Live front | Voice: the backend |
|---|---|---|---|
| In its prompt | `profile()` (≤ 6,000 chars, with the record index), today on both channels (≤ 32,000), the opening brief in the turn note | the opening brief, `profile_for_voice()` (≤ 3,000, no record index), today in a voice style (≤ 4,000) | `profile()`, today on both channels (≤ 16,000) |
| When it is read | every turn (a hand edit to the profile counts at once) | once, at session open (Live instructions cannot change mid-session) | once, at session open |
| Tools | `memory_search`, `memory_read`, `forget_preview`, `forget_apply` | none: it hands questions to the backend | the four, plus `recent_conversation` |
| `think_hard` gets | the voice profile and as much of today as fits in 6,000 | — | the profile and today, bounded to 6,000 by `think.py` |

The today block leaves out the lines the chat's own history already shows, so no
line is in the prompt twice. When today overflows its budget, the oldest lines go
and a header says how many, and names `memory_search`. The voice builds both
today blocks within 50 ms at session open; a slower disk opens the conversation
without them rather than holding the owner's first word.

**The opening brief** (`recall.opening_brief`) is one clause or nothing: the gap
since the last word said on either channel ("You last talked yesterday."), and, when
that was before today and the dream has written that day's journal, its title. A
talk less than fifteen minutes ago gets no clause. With memory off, or on the first
ever conversation, it is empty and buddy greets as it always did.

## The tools

All five are defined once in `memory.py` with strict schemas and run through
`Memory.handle_tool` in a worker thread. The voice backend waits 2.5 s at most
(then it is told "Memory lookup took too long.") and sees at most 1,500 characters
of a result.

| Tool | Arguments | Returns |
|---|---|---|
| `memory_search` | `query`, `days_back` (null = 30) | three dated groups: `hits` (record fact lines with their ids), `recalled` (mem0 memories, as `(day, channel) fact`), `said` (transcript lines, meeting notes and archived notes, each with the line before and after). Every word of the query must be in a `said` line; a "quoted phrase" matches as written. |
| `memory_read` | `ref` | a record, by id; or a day of transcript, `YYYY-MM-DD`, optionally with `HH:MM` or `HH:MM-HH:MM`. A long stretch stops with "read again from HH:MM". |
| `recent_conversation` | — | voice only: what was typed on the other channel since this voice conversation opened. |
| `forget_preview` | `query` | counts per store and a token; see below. Changes nothing. |
| `forget_apply` | `token` | forgets what the preview counted. |

## The nightly dream

`dream.py`, started by the daemon when memory is on and `OPENAI_API_KEY` is set.
It wakes two minutes after start, then every 30 minutes, and dreams whatever
nights are due.

**Which nights are due.** Every transcript day before the current one that is not
in `records/.dreamt`. The day that just ended waits until 04:30 local time
(`DREAM_HOUR = 4.5`), half an hour after the 04:00 rollover, so a conversation that
ran past 04:00 has closed. Older nights are due at once: that is the catch-up after
the Mac slept through 04:30. Catch-up runs oldest first, **at most three nights a
wake**, so a week away does not become one burst of model calls.

**One night**, in a worker thread, never on the event loop:

1. `records.reconcile_day` — **one** model call reads the current records, the
   profile, the owner's stars and the whole day's transcript (at most 100,000
   characters), and returns the records that change (whole), the profile as it
   should read now, and the day's journal. Where the day contradicts a record, the
   record gets a dated correction and the journal says so. Owner hand edits survive,
   because the current files are always its input. One commit, `dream: <day>`.
2. `records.consolidate` — **one** model call over every record merges the same
   thing filed under two ids and dates a fact a newer one replaced. Skipped, with a
   log line, when the records pass 120,000 characters.
3. `index.ingest_day` + `index.tidy` — each conversation of the day the index has
   not read goes into mem0: what the owner said as the user, what buddy said as the
   assistant, dated and labelled with its channel. Tool calls, commands, relays,
   photos and Claude's lines stay out. Then exact duplicates and any ★ candidate are
   removed.
4. The day is appended to `records/.dreamt` and a `## Dream` section with the
   counts is added to its journal.

A day with nothing said is marked with no model call. When the reconcile call
fails, nothing is written and the night is not marked, so the next wake tries
again; after three failures in one daemon run the night waits for the next start,
so one bad night cannot hold up the ones after it. A failure in consolidate or the
index still marks the night. Consolidate catches up on the next night, because it
reads every record. The index does not: the next night feeds it only that night's
conversations, so a day the index missed stays out of it until
`cc-buddy-bridge memory reindex`.

Every dream is one git commit in `records/`, so a wrong edit is one `git revert`
away and `git log -p` shows exactly what the model changed.

## Forget

"Forget that" used to remove one record file and leave the same words in nine
other places. Now it is two calls and one confirmation.

1. **`forget_preview(query)`** counts every place the words live and returns:

   ```json
   {"ok": true, "total": 7,
    "layers": {"transcripts": 4, "records": 1, "index": 1, "archive": 1, "meetings": 0},
    "token": "…", "expires_in": 600, "not_covered": ["claude-mem", "vault"]}
   ```

   It **never returns the matched words**: the owner decides on numbers, not on
   buddy reading their secret back to them. The token is bound to that query, lives
   **600 seconds**, and works **once**.
2. buddy tells the owner the counts and asks. The tool descriptions forbid calling
   `forget_apply` in the same turn: the owner confirms **in a new message**.
3. **`forget_apply(token)`** then:
   - rewrites every transcript line that matches to `(forgotten)`, keeping its time,
     channel, conversation and speaker (atomic rewrite under the writers' lock, so
     a concurrent append is never lost);
   - removes matching lines from the records, the profile, `starred.md` and the
     journal (never frontmatter);
   - deletes matching mem0 memories and their rows in mem0's history database;
   - removes matching lines from the archive's and the meeting notes' markdown;
   - **squashes the git history** of `records/` and of `archive/` to one commit of
     what is left, expires the reflog and prunes, so the words are not one
     `git log -p` away.

Matching is the one rule search uses: case-insensitive, every word longer than one
character must be in the line, a "quoted phrase" as written.

**Not covered**, and named as such rather than pretended: **claude-mem** and the
owner's **Obsidian vault** ([second-brain.md](second-brain.md)). Neither is buddy's
store. Words already sent to a model provider or to Telegram are not reachable
either (see [Privacy](#privacy)).

## The one-time move from the old store

The first time the daemon starts with memory on, `memory.migrate` moves the old
debrief store (`~/.config/cc-buddy-bridge/debrief`, or wherever
`CC_BUDDY_DEBRIEF_DIR` pointed it) and the old mem0 home
(`~/.config/cc-buddy-bridge/mem0`) under the memory folder:

| From | To |
|---|---|
| `debrief/records/` | `records/` |
| the owner's ★ lines under `## From talking` in `debrief/HIGHLIGHTS.md` (never the installer's worked example, never a `(candidate)`) | `records/starred.md`, with their dates |
| `mem0/` | `mem0/` |
| `debrief/notes/<date>/` (meeting notes) | `transcripts/meetings/<date>/` |
| everything else: `sessions/`, day files, `INDEX.md`, `HIGHLIGHTS.md`, `.git` | `archive/`, its repository sealed |

- **Renames only, never deletes.** Nothing is copied and nothing is removed. A
  rename across volumes is refused rather than turned into copy-and-delete.
- **A target that already exists is skipped** and counted, never overwritten.
- **Idempotent.** `.migrated` records the counts; while it exists the move does
  nothing. When a move failed, no marker is written and the next start tries again.
- With memory off, nothing under `~/.config/cc-buddy-bridge` is touched.

**Back up the old store before the first start with memory on:**

```bash
cp -Rp ~/.config/cc-buddy-bridge/debrief ~/cc-buddy-debrief-backup
cp -Rp ~/.config/cc-buddy-bridge/mem0   ~/cc-buddy-mem0-backup     # if it exists
```

The move is designed to lose nothing, but it rearranges the owner's only copy.
Keep the backup outside iCloud and git, and delete it once the new folder looks
right.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `CC_BUDDY_MEMORY` | `0` | The one switch. `1`: keep the transcripts, run the dream, give both brains the profile, today and the memory tools. `CC_BUDDY_RECORDS=1` still works as a legacy alias. |
| `CC_BUDDY_MEMORY_DIR` | `~/.config/cc-buddy-bridge/memory` | Moves the whole memory folder. The daemon, the CLI and the widget all read it. |
| `CC_BUDDY_MEM0` | on with memory | `0` turns just the mem0 index off. It is also off when `mem0ai` is not installed (`pip install -e ".[mem0]"`). |
| `CC_BUDDY_DEBRIEF_DIR` | `~/.config/cc-buddy-bridge/debrief` | Read only as the source of the one-time move. |
| `OPENAI_API_KEY` | — | Needed by the dream and by mem0. Without it the records are read but never dreamt, and there is no index. |

Everything else is a constant in code, not a knob: the 04:00 day start, the 04:30
dream, the budgets above, the models (`gpt-5.4-nano` for the dream and mem0's
extraction, `text-embedding-3-small` for mem0's embeddings).

## From the terminal

```bash
cc-buddy-bridge memory forget "<words>"   # preview the counts, then [y/N], then forget everywhere
cc-buddy-bridge memory dream               # dream every night that is due now
cc-buddy-bridge memory dream --day 2026-01-01   # dream that day again, even if already dreamt
cc-buddy-bridge memory reindex             # rebuild the mem0 index from every transcript day
```

All three need `CC_BUDDY_MEMORY=1` in the shell, as the daemon has it, and refuse
until the daemon has started once with memory on (the one-time move is the
daemon's, and `.migrated` must exist). They print counts, never the matched words.

**Run `dream` and `reindex` with the daemon stopped.** mem0's on-disk store takes
one opener at a time. The CLI does not stop the daemon for you: `reindex` checks
afterwards and says "stop the daemon first" when the index did not open. A
`dream` run beside the daemon still writes the records, but the index cannot open,
so that day stays out of the index until a `reindex`. The same holds for `forget`
from the terminal: with the daemon running, the index cannot be opened and its
count is 0, so forget from a chat (the daemon's own index) or stop the daemon
first. `reindex` clears `mem0/ingested_convs` and feeds every transcript day again;
mem0's own update step and `tidy` absorb what was already there.

## Privacy

The memory folder is this Mac's. Here is exactly what leaves it, and when.

- **The Telegram text brain** (`gpt-6-astra`, OpenAI Responses API, `store=False`)
  is sent the profile, today's lines on both channels and each memory tool's result,
  every turn. Telegram's own servers carry the chat itself, as they always did.
- **The voice** (OpenAI's Live API and its Responses backend) is sent the voice
  profile and today's lines at session open, and the backend the full profile,
  today, and each memory tool's result.
- **`think_hard`** (`store=False`) is sent the profile and up to 6,000 characters of
  today with the question.
- **The dream** (`gpt-5.4-nano`, `store=False`) is sent one day's transcript (at most
  100,000 characters), the records, the profile and the stars, once a night; and all
  the records for consolidate.
- **mem0** sends the owner's side of each conversation, and buddy's replies, to
  OpenAI for fact extraction (`store=False`), and each fact and each
  `memory_search` query for an embedding (the embeddings call has no such flag).
  Its vector store and history database stay on disk. mem0's PostHog
  telemetry is forced off before the first import, and mem0 is refused if it still
  reads on; `MEM0_DIR` keeps its config out of `~/.mem0`.
- **claude-mem receives no conversation content.** The memory bus carries no
  conversation topic ([memory-bus.md](../memory-bus.md)): what was said stays in the
  memory folder. A `/buddy/memory/remember` line sent in over the bus becomes one
  transcript line for the dream to judge, never a star.
- **The memory's logs carry counts, channels and milliseconds**, never a word that
  was said and never a file name (a journal's name summarises its day). The Telegram
  inlet logs no words either. One exception, older than this memory and local to the
  daemon's log file: the voice session logs buddy's own spoken replies, what the
  backend handed the voice, and the first words of an owner turn it classified as a
  command, so a wrong reply can be traced. A learner thinking out loud is never logged.
- **The git repositories cannot be pushed.** `records/` and `archive/` are sealed on
  every start (`records.seal`): any remote is removed, every push URL is rewritten
  to one that cannot resolve (which `--no-verify` cannot skip), a pre-push hook
  refuses, and `core.hooksPath` points at it so a global hooks path cannot bypass
  it. A records folder inside some other repository is never committed to at all.

Nothing in `transcripts.py` imports a network library. FileVault is what protects
the folder at rest.

## Where to look in the code

`bridge/src/cc_buddy_bridge/`: `memory.py` (the facade, the tools, forget, the
move), `transcripts.py`, `records.py`, `mem0_memory.py`, `dream.py`, `recall.py`
(the folder layout and the opening brief). The brains: `telegram.py`,
`voice_agent.py`. The CLI: `cli.py` (`memory forget|dream|reindex`).
