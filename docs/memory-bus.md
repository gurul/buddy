# buddy's memory bus

buddy keeps three memories, and until now none of them was live. The diary is a
JSONL stream of what it saw. The debrief store holds one distilled note per
conversation, written after the conversation closes. A lesson is a SQLite row.
The memory bus is the one place those become events the moment they happen, so
that anything else can watch buddy's day as it forms.

```
 eyes / voice / lessons / diary ──► daemon ──► MemoryBus (in-process pub/sub)
                                                 │
            ┌────────────────────────────────────┼──────────────────────────┐
            ▼                                    ▼                          ▼
  rosbridge server  ws://127.0.0.1:9090   claude-mem sink               recall (as before)
  /buddy/memory/observation               POST /api/memory/save         debrief store
  /buddy/memory/conversation              project "buddy"
  /buddy/memory/lesson                    ▲
  /buddy/state                            │ GET /api/search
  service /buddy/memory/recall ───────────┘  answers a recall call
  + inbound publish /buddy/memory/remember ──► a ★ (candidate) draft
  + /claude/observation ◄── mirrored from claude-mem's live stream
```

The bus itself (`bridge/src/cc_buddy_bridge/memory_bus.py`) is pure Python and
always on. The two sinks are opt-in and off by default. The daemon never waits
on either: a sink that cannot start is one warning line at boot, and a sink that
dies later logs once and goes quiet.

## Topics

Every message carries `time`, a Unix timestamp. The `type` names are what a
rosbridge client sends with `subscribe`.

| Topic | Type | Fields | Fires when |
| --- | --- | --- | --- |
| `/buddy/memory/observation` | `buddy_msgs/Observation` | `thought`, `observations[]`, `changed[]`, `tags[]`, `importance`, `novelty` | The diary **writes** a thought to the day file. Candidates it keeps only in memory never fire. Photos never ride along. |
| `/buddy/memory/conversation` | `buddy_msgs/ConversationNote` | `title`, `note[]`, `open[]`, `owes[]`, `session_id`, `ended` | A conversation closed and buddy distilled it. The note, never the transcript. |
| `/buddy/memory/lesson` | `buddy_msgs/LessonEvent` | `action`, `stage`, `topic`, `level`, `mode`, `lesson_id`, `feedback` | A lesson moved on: from the web page, the voice, or `cc-buddy-bridge lesson`. `feedback` is buddy's own words. `action` is the lesson action when the event came through the terminal or voice, `notice` when the web page drove it. |
| `/buddy/state` | `buddy_msgs/AgentState` | `state` | The conversation phase changed: `idle`, `wake`, `listening`, `thinking`, `speaking`, `working`, `done`, `error`. |
| `/buddy/presence` | `buddy_msgs/Presence` | `state` | In a conversation, buddy's sense of where its person is changed ([vision.md](stackchan/vision.md#following-whoever-is-talking)): `seen`, `lost` (with `leaving`: they were moving fast or left by a frame edge), `found` (with `how`: where they were heading, where they usually are, either side, up and down). Carries `yaw`, `pitch`, `speed` (deg/s) and `phase`. Live only: not stored in claude-mem. |
| `/buddy/memory/remember` | `buddy_msgs/Remember` | `text`, `title` (optional) | **Inbound.** Anyone on the bus asks buddy to keep a line. |
| `/claude/observation` | `buddy_msgs/ClaudeObservation` | as claude-mem sends it | The owner's claude-mem stream reported a new observation (mirror on). |

## The service

`/buddy/memory/recall` (`buddy_msgs/Recall`). Args `{"query": str, "limit": int}`.
Values `{"results": [{"id", "title", "time", "text"?}]}`. Searches the owner's
claude-mem for buddy's own memories (project `buddy`). With claude-mem off it
answers `{"results": [], "error": "claude-mem is off (CC_BUDDY_CLAUDE_MEM=1)"}`.

## What a `remember` does

buddy never stars anything for itself. A line published on
`/buddy/memory/remember` becomes a draft in buddy's chat-memory store,
`sessions/<date>/<time>-bus-<id>.md`, with the line under **Proposed for the
permanent layer** as a `★ (candidate)`. The owner promotes it, or not, the same
way as any candidate from a conversation.

## Environment

| Variable | Meaning |
| --- | --- |
| `CC_BUDDY_ROSBRIDGE=1` | Serve the rosbridge v2 protocol over WebSocket. Default off. |
| `CC_BUDDY_ROSBRIDGE_HOST` | Bind address, default `127.0.0.1`. Only change it on a network you trust. |
| `CC_BUDDY_ROSBRIDGE_PORT` | Default `9090`, the rosbridge convention. |
| `CC_BUDDY_CLAUDE_MEM=1` | Store bus events in the owner's claude-mem and answer recalls from it. Default off. |
| `CC_BUDDY_CLAUDE_MEM_URL` | The worker's base URL. Unset, buddy reads `CLAUDE_MEM_WORKER_PORT` from `~/.claude-mem/settings.json`, then the port in `~/.claude-mem/worker.pid`, then `37777`. |
| `CC_BUDDY_CLAUDE_MEM_MIRROR` | Republish claude-mem's live stream as `/claude/observation`. Defaults to the value of `CC_BUDDY_CLAUDE_MEM`. |

Put them in `~/.config/cc-buddy-bridge/env` and restart the daemon. The boot log
says what attached:

```
memory bus: rosbridge ws://127.0.0.1:9090, claude-mem http://127.0.0.1:37701, mirror on
```

## Watching it

From a terminal, with the daemon running:

```bash
cc-buddy-bridge memory tail                       # every /buddy/* event, one line each
cc-buddy-bridge memory tail --topic /buddy/state  # one topic
cc-buddy-bridge memory recall "the green mug"     # ask claude-mem through the bus
```

From anything that speaks rosbridge. roslibjs in a browser:

```js
const ros = new ROSLIB.Ros({ url: "ws://127.0.0.1:9090" });
new ROSLIB.Topic({ ros, name: "/buddy/memory/observation", messageType: "buddy_msgs/Observation" })
  .subscribe(m => console.log(m.thought, m.tags));
new ROSLIB.Topic({ ros, name: "/buddy/memory/remember", messageType: "buddy_msgs/Remember" })
  .publish(new ROSLIB.Message({ text: "The green mug lives on the left shelf." }));
```

roslibpy, Foxglove, or a raw WebSocket sending
`{"op":"subscribe","topic":"/buddy/state"}` all work the same way. The protocol
subset served is `advertise`, `unadvertise`, `subscribe`, `unsubscribe`,
`publish`, `call_service`; the server answers `service_response` and a `status`
op for errors.

## claude-mem

[claude-mem](https://github.com/thedotmack/claude-mem) is the owner's memory
plugin for Claude Code. Its worker keeps SQLite plus a vector index and serves
an HTTP API on localhost. buddy uses three of its routes:

- `POST /api/memory/save` for every `/buddy/memory/*` event, as project `buddy`
  with `metadata.platformSource = "buddy"` and `metadata.topic` set.
- `GET /api/search?query=…&project=buddy&limit=…` for the recall service, then
  `GET /api/observation/<id>` for each hit's text.
- `GET /stream` (server-sent events) for the mirror, which republishes
  `new_observation` events and skips project `buddy` so buddy never echoes itself.

So a Claude Code session with claude-mem installed can search what buddy saw and
said today. In that session: *"what did buddy notice this afternoon?"* reaches
claude-mem's `search` tool with project `buddy`.

The worker starts when a Claude Code session starts. If buddy boots first, or
the worker goes away, each save fails fast, is counted in the sink's stats, and
is dropped. The sink does not retry: those events reach the WebSocket clients
and buddy's own stores as usual, only claude-mem misses them. The mirror does
reconnect, every 5 s.

## Privacy

Two things never reach the bus, and therefore never reach a WebSocket client
or claude-mem:

- **The learner's words in a lesson.** Typed ideas, spoken ideas, strokes and
  images stay in the lesson store on this computer (docs/learning.md). A lesson
  event carries the action, the stage, the topic, the level and buddy's own
  feedback line, nothing else.
- **Conversation transcripts.** The distilled note goes out, the turns do not.

The rosbridge server binds `127.0.0.1` unless told otherwise. There is no
authentication on the protocol, which is why the default is loopback only.

## Limits, today

- One bus, one process. A client that falls behind drops messages (bounded
  queue per client) rather than slowing buddy down.
- No `fragment`, `png` or action ops. `set_level` is accepted and ignored. No
  topic type checking: the `type` a client sends is recorded, not enforced.
- The mirror republishes claude-mem's `new_observation` events for every
  project except `buddy`, and ignores summaries.
- The sink drops a save the worker refuses or cannot take; it never retries.
