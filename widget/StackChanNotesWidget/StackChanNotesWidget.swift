// WidgetKit extension — sandboxed, reads only the mirrored notes.json from the App Group.

import SwiftUI
import WidgetKit

@main
struct StackChanNotesWidgetBundle: WidgetBundle {
    var body: some Widget {
        StackChanNotesWidget()
    }
}

struct NotesEntry: TimelineEntry {
    let date: Date
    let snapshot: NotesSnapshot
}

struct NotesProvider: TimelineProvider {
    func placeholder(in context: Context) -> NotesEntry {
        NotesEntry(date: .now, snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (NotesEntry) -> Void) {
        completion(NotesEntry(date: .now, snapshot: context.isPreview ? .placeholder : NoteStore.load()))
    }

    /// One entry, refreshed every 15 min as a fallback. The helper app's
    /// `WidgetCenter.reloadAllTimelines()` is the real trigger.
    func getTimeline(in context: Context, completion: @escaping (Timeline<NotesEntry>) -> Void) {
        let entry = NotesEntry(date: .now, snapshot: NoteStore.load())
        completion(Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(15 * 60))))
    }
}

struct StackChanNotesWidget: Widget {
    static let kind = "StackChanNotes"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: Self.kind, provider: NotesProvider()) { entry in
            NotesView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("StackChan notes")
        .description("What your desk robot noticed.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct NotesView: View {
    @Environment(\.widgetFamily) private var family
    let entry: NotesEntry

    private var lineBudget: Int {
        switch family {
        case .systemSmall: 3
        case .systemMedium: 6
        default: 14
        }
    }

    private var visible: ArraySlice<Note> { entry.snapshot.notes.prefix(lineBudget) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("StackChan")
                    .font(.headline)
                Spacer()
                Text("\(entry.snapshot.notes.count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            if visible.isEmpty {
                Spacer(minLength: 0)
                Text("No notes yet — the robot explores when Claude is idle")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            } else {
                ForEach(visible) { note in
                    NoteRow(note: note, compact: family == .systemSmall)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

private struct NoteRow: View {
    let note: Note
    let compact: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(note.time)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                Text(note.text)
                    .font(compact ? .caption : .footnote)
                    .lineLimit(compact ? 2 : 1)
                    .truncationMode(.tail)
            }
            if !compact, let pose = note.pose {
                Text(pose)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .padding(.leading, 38)
            }
        }
    }
}

#Preview("medium", as: .systemMedium) {
    StackChanNotesWidget()
} timeline: {
    NotesEntry(date: .now, snapshot: .placeholder)
    NotesEntry(date: .now, snapshot: .empty)
}
