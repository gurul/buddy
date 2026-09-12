# Buddy math lessons

Buddy's learning workspace implements the two paths in the activity diagram:
learn a topic, or bring a problem. It shares a saved lesson with the existing
voice agent and opens a mouse/pen whiteboard in a local browser or the macOS
widget helper window.

## Try the working demo

From the repository root, with Python 3.11 or newer:

```powershell
python tools/start_learning.py --demo --data-dir .demo-data --port 48767
```

Open `http://127.0.0.1:48767/`. Click **Play workflow demo** to watch Buddy
create an algebra problem, save an idea, give a hint, reveal three individual
steps, recap, and return to the saved dashboard. You can also use the controls
yourself. The demo has fixed addition, algebra, and derivative examples. It
never calls an API and explicitly does **not** recognize images or handwriting.
For a screenshot demo, choose **Help with my problem**, attach an image, type
`Solve 2x + 3 = 11.`, confirm it, and enter your ideas or select **I don't know
how to start**. Type `2x = 8` to see Buddy continue with `x = 4`.

The recorded browser walkthrough is [buddy-learning-demo.webm](learning-demo/buddy-learning-demo.webm).
It exercises both paths against the real local server. The screenshots and
machine-readable check results are in [learning-demo/](learning-demo/).

## Live tutoring

The existing bridge daemon starts the learning server at
`http://127.0.0.1:48766/`. Restart the daemon after updating this checkout.
The browser window uses the same service as voice. Say **"okay buddy, I want
to do a math lesson"** (the default wake-word file also includes "hey buddy"
and "ok buddy"). Buddy asks which path you want, and asks for a topic and
starting level when needed. It uses the `math_lesson` tool instead of taking
over the computer with the computer-control agent.

Alternatively, run the learning service by itself:

```powershell
python tools/start_learning.py
```

Or, with the bridge installed: `cc-buddy-bridge learning`.
Do not run two learning servers on the same port. Standalone mode provides
the whiteboard and tutoring; robot voice requires the existing daemon, its
microphone/wake-word setup, and a working voice API configuration.

### OpenRouter with Astra

Edit `~/.config/cc-buddy-bridge/env` (on this Windows machine:
`C:\Users\gitsw\.config\cc-buddy-bridge\env`). The file is outside the repository.
Use these entries, replacing the empty key value locally:

```dotenv
CC_BUDDY_LEARNING_PROVIDER=openrouter
CC_BUDDY_LEARNING_MODEL=openai/gpt-6-astra
OPENROUTER_API_KEY=
```

Restart the learning server or daemon after saving the key. Start **without**
`--demo`: `python tools/start_learning.py`. The dashboard displays the provider
and model, and reports a missing OpenRouter key without exposing its value.
All questions, screenshots, handwritten work, hints and steps use Astra; there
is no automatic switch to a cheaper model. OpenRouter can select an upstream
provider serving that same model.

The adapter uses OpenRouter's Chat Completions endpoint with base64 images and
strict JSON schema, requiring endpoint support for the requested parameters.
[Astra's OpenRouter page](https://openrouter.ai/openai/gpt-6-astra) lists its model
ID and image/structured-output support; see also
[OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).
The key stays on the Python server. OpenRouter and its selected provider handle
submitted lesson data according to their account/data policies.

Direct OpenAI remains supported with `CC_BUDDY_LEARNING_PROVIDER=openai`,
`CC_BUDDY_LEARNING_MODEL=gpt-6-astra`, and `OPENAI_API_KEY`. If provider is unset,
a nonempty OpenRouter key selects OpenRouter; otherwise OpenAI is selected.
An explicitly selected provider never falls back to the other account's key.
Direct OpenAI uses `store: false` on Responses requests. The math provider setting
does not change Buddy's separate realtime voice connection, which still needs
its existing OpenAI configuration. Browser Read aloud remains available.

| Setting | Meaning |
| --- | --- |
| `CC_BUDDY_LEARNING=0` | Disable the daemon's learning server |
| `CC_BUDDY_LEARNING_PORT` | Daemon/widget port, default 48766 |
| `CC_BUDDY_LEARNING_DIR` | Local store, default `~/.config/cc-buddy-bridge/learning` |
| `CC_BUDDY_LEARNING_PROVIDER` | `openrouter` or `openai` |
| `CC_BUDDY_LEARNING_MODEL` | `openai/gpt-6-astra` on OpenRouter; `gpt-6-astra` on OpenAI |
| `--demo` | Explicit offline examples; separate default `learning/demo` store |
| `--data-dir` / `--port` | Standalone and demo overrides |

## Activity workflow

1. **Learn a topic:** choose topic and starting level (K through college year
   two). Buddy generates a problem without revealing its answer.
2. **Help with my problem:** paste/upload a PNG, JPEG, or WebP, write on the
   board, or type a problem. Buddy transcribes the problem. Correct unclear
   symbols and explicitly confirm before tutoring begins.
3. Put down ideas. The help path asks for a starting attempt or an explicit
   "I don't know how to start" before it gives help. Writing the original
   problem does not count as a new attempt.
4. **Hint** nudges without completing a step. **Check my work** reviews current
   writing and ideas. **Show one step** adds one next transformation to Buddy's
   separate panel. Click again for the next step. The learner's board is never
   overwritten by the tutor.
5. Optional supervision checks after a five-second pause following an edit.
   It does not continuously poll the model or interrupt individual pen strokes.
6. When complete, ask for a recap/understanding check, try another problem,
   change topic, or end. A lesson can be ended at any stage and resumed later.

The voice tool supports open, start, ideas, hint, check, step, status, recap,
and end. Selecting a lesson in the browser makes it the voice tool's active
lesson. The browser polls saved revisions to display voice-originated updates.
The optional browser **Read aloud** control uses the operating system/browser
speech synthesizer; it is labelled separately from the robot's existing voice.

## Saved work and dashboard

SQLite transactions save the problem, source screenshot, typed ideas, pen and
eraser strokes, flattened whiteboard image, tutor hints/steps/feedback, mode,
level, and completion state. Every autosave and tutoring action has a revision.
**Saved work** opens those revisions; **Export** downloads the lesson and its
full revision history as JSON. Ending a lesson preserves its previous stage.
Revision checks reject stale writes from another window instead of overwriting
newer work. The browser keeps a temporary local recovery draft while edits are
unsaved, and warns before closing with unsaved work.

The files are `lessons.sqlite3` and a small `dashboard.json` widget summary.
They are separate from the room diary, automatic memories, and photo pruning.
All saved revisions are retained, including erased writing; history can grow
large over time. Protect/back up the directory as appropriate for learner data.
Uploaded images are resized to a maximum dimension of 1600 pixels. Live checks
send the selected screenshot, current board, and relevant lesson context to
the tutor. No automatic desktop capture is used.

## Widget integration (macOS)

Build the existing `widget/StackChanNotes.xcodeproj` in Xcode on a Mac. All Swift
changes are in files already included in that project; regeneration is not
required. Start the helper and bridge daemon, then add **buddy's learning**
from the widget gallery. The helper mirrors the summary into its App Group
every 30 seconds. The card shows lesson counts and recent problems; click it
to open the full dashboard embedded in the helper app. The existing diary
widget's medium/large layouts also contain a **Learning dashboard** link,
and the menu-bar app has an **Open learning dashboard** command. The older
Python `notes-widget` panel also includes this command in its right-click menu.

If using a custom directory or port, give the helper app the same environment
values as the daemon. The demo is separate from live widget data unless you
explicitly point the helper at the demo directory and port. Windows uses the
browser dashboard; WidgetKit is only available on macOS.

## Verification and current limits

`bridge/tests/test_learning.py` covers the flow, exactly-one-step demo behavior,
current image inputs, API response validation, durable revisions, conflicts,
failure recovery, lesson lifecycle, voice delegation, and local HTTP request
boundaries. Voice tests cover the new tool through the existing connection.

```powershell
python -m pytest bridge/tests/test_learning.py bridge/tests/test_voice_agent.py -q
# With Playwright installed (plus its ffmpeg component for video recording):
python tools/demo_learning.py
```

The demo runner needs `bridge/src` on `PYTHONPATH` (or an editable bridge install)
and Chrome at its standard Windows path; `--browser` accepts another executable.
It runs headless and saves a video and screenshots. Playwright's test context
bypasses CSP only for its assertion machinery; the application keeps its strict
same-origin content policy. The live tutor is model-based, not an independently
verified symbolic mathematics engine. The single-step schema and instruction
constrain its output, but they cannot prove that every generated transformation
is mathematically correct or pedagogically atomic. Handwriting accuracy and
age-level teaching quality need real learner evaluations before classroom use.

On the Windows development machine, the recorded demo uses offline examples.
Live image/voice API calls, physical robot behavior, and the Swift widget build
require their respective API access/hardware/macOS environment and are not
represented as verified by that demo.


### Verification in this checkout (Windows, 2026-09-12)

- Targeted learning/voice/wake-word/daemon/widget suite: **142 passed, 3 skipped**.
- Browser demo: **15 workflow checks passed**, no JavaScript page errors.
- Changed Python files: Ruff passed.
- Full bridge suite with dependencies installed and UTF-8 enabled: **993 passed,
  9 failed, 5 skipped**. Remaining failures are in chat-memory/identity/notes/
  photos/recall POSIX-permission assertions, environment home expansion,
  read-policy POSIX paths, and a timing-sensitive state ordering assertion.
  These modules were not modified for learning. The full suite is not green.
- Live API key: absent; live image/voice calls not exercised.
- macOS Swift build and physical robot: not available on this Windows machine.


### Running from WSL with a Windows environment file

WSL's `~` is the Linux home, not the Windows home. To use the same key file:

```bash
python tools/start_learning.py --env-file /mnt/c/Users/gitsw/.config/cc-buddy-bridge/env
```

Alternatively set `CC_BUDDY_ENV_FILE` to that path before launching the bridge
or learning server. Explicit `--env-file` takes precedence over that variable;
existing shell settings still take precedence over values inside the file.
Restart the server after changing settings. Windows PowerShell users can keep
using `python tools/start_learning.py` with the default Windows home path.


### OpenRouter 403: provider Terms of Service restriction

If Buddy reports this specific restriction, the request reached OpenRouter but
was rejected under a provider policy. A simple addition-generation request also
returned this error during diagnosis on 2026-09-12. The response does not name
the specific policy or identify whether the restriction is account-wide or
model-specific. Ask OpenRouter support to review access to `openai/gpt-6-astra`.
Changing the worksheet or adding an OpenAI key is not an established fix for
this error. Buddy preserves saved work and does not automatically retry or
switch models. Raw response metadata and account identifiers are not shown.


### Address already in use

Only one service can listen on port 48766. If Buddy is already running, open
`http://127.0.0.1:48766/` instead of launching it again. To reload changed
settings, stop that specific Buddy process, then restart it with the same
arguments. In WSL, `pgrep -af start_learning.py` identifies standalone launchers.
Do not terminate WSL itself or unrelated processes. The launcher now reports
an occupied port with this guidance and exits with status 2 instead of showing
a traceback. `--port 48768` can launch a separate instance when intended.
