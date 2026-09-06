# buddy's diary — macOS desktop widget and diary window

A native WidgetKit widget that shows buddy's newest thoughts and how it feels
about them on the macOS desktop (macOS 14+, desktop widgets), and a diary window
with everything behind them. It lives in `widget/` as an Xcode project generated
by [xcodegen](https://github.com/yonaskolb/XcodeGen).

**The widget** is a dark card: buddy's name, its current feeling (emoji, label and
a colour dot — hue from valence, brightness from arousal, the same mapping as the
robot's LEDs), then the newest thoughts, each with its time and a thin colour bar
for the feeling it was written in; the large size adds what changed. **Tap it**
(`stackchan://diary`) and the helper opens the diary window:

| Tab | What |
|---|---|
| Thoughts | every thought by day; click one for the observations, what changed, tags, novelty and importance, and valence/arousal gauges; a toggle shows the unwritten candidates buddy kept in memory only; **★ stars** a thought into `highlights.md` |
| Feelings | valence and arousal per thought over time (Swift Charts), a tally of feelings |
| Profile | ★ never forget (the starred layer), then buddy's ROOM / HUMAN / SELF / RULES blocks |
| Dreams | the nightly insights per day, with the ★ candidates buddy proposed |

The menu-bar item has *Open diary* (⌘D) as well.

## What it is

Two targets, one app bundle:

| Target | Kind | Sandboxed | Bundle id |
|---|---|---|---|
| `StackChanNotes` | SwiftUI menu-bar helper (`LSUIElement`, no Dock icon) | no — it must read `~/.config/cc-buddy-bridge/notes` | `com.github.cc-buddy-bridge.StackChanNotes` |
| `StackChanNotesWidget` | WidgetKit extension, small / medium / large | yes (required for widgets) | `com.github.cc-buddy-bridge.StackChanNotes.Widget` |

Both share the App Group `SJ8BKXTNUS.com.github.cc-buddy-bridge` (macOS App
Groups are prefixed with the Team ID, which is the `OU` of the "Apple
Development" signing certificate).

Sizes: small shows 2 thoughts, medium 4, large 9 with a "changed:" line. Empty
state: *Nothing noticed yet — buddy explores when Claude is idle.*

## How notes flow

```
cc-buddy-bridge daemon (diary.py)
  └─ ~/.config/cc-buddy-bridge/notes/
       YYYY-MM-DD.md   "- HH:MM yaw=+20 pitch=40 — <thought>" lines + a "## Dreams" section at night
       memory.jsonl    one record per frame: thought, observations, changed, tags, novelty, importance,
                       written, valence, arousal, label
       profile.md      ROOM / HUMAN / SELF / RULES
       highlights.md   "- ★ <claim> — starred <date>"  (written by the diary window, read by buddy)
           │  (DispatchSource on the dir + on the newest day file, plus a 30 s timer)
           ▼
StackChanNotes.app  (helper, unsandboxed; hosts the diary window; claims stackchan://)
  └─ ~/Library/Group Containers/SJ8BKXTNUS.com.github.cc-buddy-bridge/notes.json
       notes, thoughts (newest 200), profile, reflections (dreams), highlights → WidgetCenter.reloadAllTimelines()
           ▼
StackChanNotesWidget.appex  (sandboxed)
  └─ TimelineProvider reads notes.json; refreshes on its own every 15 min as a fallback; .widgetURL(stackchan://diary)
```

Parsing lives in `widget/Shared/NoteStore.swift`: `NoteLine.parse` for diary
lines (em dash, with ` – `, ` -- `, ` - ` fallbacks; a line without a pose is
fine), `NoteLine.reflections` for the `## Dreams` (or `## Evening reflection`)
section, tolerant `Thought` decoding for `memory.jsonl` (missing fields default),
`NoteStore.readHighlights` / `NoteStore.star` for the starred layer. Older
`notes.json` files without the new fields still decode.

Environment knobs on the helper:

- `CC_BUDDY_NOTES_DIR` — override the notes directory (default `~/.config/cc-buddy-bridge/notes`).
- `CC_BUDDY_NO_LOGIN_ITEM=1` — skip the one-time login-item registration (smoke tests from a build dir).

## Build and install

Requirements: Xcode 26.x, `brew install xcodegen`, an "Apple Development"
signing identity for team `SJ8BKXTNUS` (edit `DEVELOPMENT_TEAM` and the App
Group prefix in `widget/project.yml` + `widget/Shared/NoteStore.swift` for
another team).

```sh
cd widget && xcodegen generate && cd ..        # regenerates StackChanNotes.xcodeproj
xcodebuild -project widget/StackChanNotes.xcodeproj -scheme StackChanNotes \
  -configuration Release -derivedDataPath widget/build \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=SJ8BKXTNUS build

ditto widget/build/Build/Products/Release/StackChanNotes.app ~/Applications/StackChanNotes.app
open ~/Applications/StackChanNotes.app          # first launch: registers login item + widget
pluginkit -m -v -p com.apple.widgetkit-extension | grep -i stackchan   # widget is known
```

The helper shows a small `note.text` icon in the menu bar with the note count,
last sync time, *Refresh now*, *Open notes folder*, a *Launch at login* toggle,
and *Quit*.

## Add the widget to the desktop (manual)

WidgetKit does not let an app place its own widget. Once the helper has been
launched from `~/Applications`:

1. Right-click the desktop → **Edit Widgets…**
2. Search for **StackChan notes** (or scroll to *StackChanNotes*).
3. Drag the small, medium, or large size onto the desktop. Done.

If it does not appear in the gallery, re-run `open ~/Applications/StackChanNotes.app`
and check `pluginkit -m -p com.apple.widgetkit-extension | grep -i stackchan`.

## Smoke test without the robot

```sh
mkdir -p /tmp/notes
printf -- '- 10:02 yaw=+20 pitch=40 — a mug on the desk, still steaming\n' > /tmp/notes/2026-09-05.md
CC_BUDDY_NOTES_DIR=/tmp/notes CC_BUDDY_NO_LOGIN_ITEM=1 \
  widget/build/Build/Products/Release/StackChanNotes.app/Contents/MacOS/StackChanNotes &
cat ~/Library/Group\ Containers/SJ8BKXTNUS.com.github.cc-buddy-bridge/notes.json
echo '- 10:15 yaw=-10 pitch=30 — the window blind is half down' >> /tmp/notes/2026-09-05.md
sleep 1; cat ~/Library/Group\ Containers/SJ8BKXTNUS.com.github.cc-buddy-bridge/notes.json   # now two entries
pkill -x StackChanNotes
```

## Remove

```sh
# menu bar → Launch at login off, then Quit — or:
pkill -x StackChanNotes
rm -rf ~/Applications/StackChanNotes.app
rm -rf ~/Library/Group\ Containers/SJ8BKXTNUS.com.github.cc-buddy-bridge
defaults delete com.github.cc-buddy-bridge.StackChanNotes
```

Deleting the app removes the login item (System Settings → General → Login
Items & Extensions lists it under "StackChan Notes" if it lingers). Remove the
widget from the desktop via right-click → *Remove Widget*.
