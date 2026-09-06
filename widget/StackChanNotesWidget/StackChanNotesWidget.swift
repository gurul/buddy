// WidgetKit extension — sandboxed, reads only the mirrored notes.json from the App Group.
//
// Design: a dark, warm card. A header with buddy's name and the current feeling
// (emoji + label + a colour dot: hue from valence, brightness from arousal — the
// same mapping as the robot's LEDs), then the newest thoughts, each with its
// time and a thin colour bar for the feeling it was written in. Tapping the
// widget opens the diary window in the helper app (stackchan://diary).

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
                .containerBackground(for: .widget) {
                    LinearGradient(colors: [Color(red: 0.11, green: 0.11, blue: 0.13),
                                            Color(red: 0.07, green: 0.07, blue: 0.09)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)
                }
                .widgetURL(AppGroup.diaryURL)
        }
        .configurationDisplayName("buddy's diary")
        .description("What your desk robot noticed, and how it felt about it. Tap to open the diary.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

extension Color {
    static func mood(_ t: Thought?) -> Color {
        guard let t else { return Color.white.opacity(0.35) }
        let (r, g, b) = MoodColor.rgb(valence: t.valence, arousal: t.arousal, label: t.label)
        return Color(red: r, green: g, blue: b)
    }
}

struct NotesView: View {
    @Environment(\.widgetFamily) private var family
    let entry: NotesEntry

    private var lineBudget: Int {
        switch family {
        case .systemSmall: 2
        case .systemMedium: 4
        default: 9
        }
    }

    /// Written thoughts first (they match the diary lines); fall back to plain notes
    /// for a mirror made before memory.jsonl existed.
    private var rows: [Row] {
        let written = entry.snapshot.thoughts.filter(\.written)
        if !written.isEmpty {
            return written.prefix(lineBudget).map { Row(id: "t\($0.id)", time: $0.time, text: $0.thought, mood: $0,
                                                        changed: $0.changed.first, photo: AppGroup.photo($0.photo)) }
        }
        return entry.snapshot.notes.prefix(lineBudget).map { Row(id: $0.id, time: $0.time, text: $0.text, mood: nil,
                                                                 changed: nil, photo: nil) }
    }

    /// The newest photo among the shown rows — the medium and large widget lead
    /// with it, because a picture is why buddy kept that moment.
    private var lead: (row: Row, url: URL)? {
        guard family != .systemSmall else { return nil }
        for row in rows { if let url = row.photo { return (row, url) } }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: family == .systemSmall ? 6 : 8) {
            Header(mood: entry.snapshot.mood, count: max(entry.snapshot.notes.count, entry.snapshot.thoughts.filter(\.written).count),
                   compact: family == .systemSmall)
            if rows.isEmpty {
                Spacer(minLength: 0)
                Text("Nothing noticed yet — buddy explores when Claude is idle.")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.6))
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            } else {
                if let lead {
                    PhotoStrip(url: lead.url, height: family == .systemLarge ? 96 : 64)
                }
                VStack(alignment: .leading, spacing: family == .systemSmall ? 5 : 7) {
                    ForEach(rows.prefix(lead == nil ? lineBudget : max(1, lineBudget - 2))) { row in
                        ThoughtRow(row: row, family: family)
                    }
                }
                Spacer(minLength: 0)
                if family != .systemSmall {
                    Text("tap to open the diary")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.35))
                }
            }
        }
        .foregroundStyle(.white)
    }
}

/// The mirrored photo, rounded and cropped to a wide strip.
private struct PhotoStrip: View {
    let url: URL
    let height: CGFloat

    var body: some View {
        if let image = NSImage(contentsOf: url) {
            Image(nsImage: image)
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(height: height)
                .frame(maxWidth: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(alignment: .topTrailing) {
                    Image(systemName: "camera.fill")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.85))
                        .padding(4)
                        .background(.black.opacity(0.35), in: Circle())
                        .padding(5)
                }
        }
    }
}

struct Row: Identifiable {
    let id: String
    let time: String
    let text: String
    let mood: Thought?
    let changed: String?
    /// The mirrored photo buddy kept for this thought, if it kept one.
    var photo: URL? = nil
}

private struct Header: View {
    let mood: Thought?
    let count: Int
    let compact: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            Circle()
                .fill(Color.mood(mood))
                .frame(width: compact ? 9 : 11, height: compact ? 9 : 11)
                .shadow(color: Color.mood(mood).opacity(0.8), radius: 4)
            Text("buddy")
                .font(compact ? .subheadline.weight(.semibold) : .headline)
            if let mood {
                Text("\(MoodColor.emoji(for: mood.label)) \(mood.label)")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.75))
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            if !compact {
                Text("\(count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.45))
            }
        }
    }
}

private struct ThoughtRow: View {
    let row: Row
    let family: WidgetFamily

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(Color.mood(row.mood))
                .frame(width: 3)
                .padding(.vertical, 1)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(row.time)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.white.opacity(0.5))
                    if family == .systemLarge, let m = row.mood {
                        Text(m.label)
                            .font(.caption2)
                            .foregroundStyle(Color.mood(m).opacity(0.9))
                    }
                }
                Text(row.text)
                    .font(family == .systemSmall ? .caption : .footnote)
                    .lineLimit(family == .systemSmall ? 3 : family == .systemMedium ? 2 : 2)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                if family == .systemLarge, let changed = row.changed {
                    Text("changed: \(changed)")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.45))
                        .lineLimit(1)
                }
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
