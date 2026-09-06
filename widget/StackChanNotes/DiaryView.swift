// The diary window: what the widget shows, in full — every thought with the
// observations, what changed, tags, novelty and importance behind it; a
// feelings timeline; buddy's profile of the room and its human; the evening
// reflections. Opened by tapping the widget (stackchan://diary) or from the
// menu-bar item.

import Charts
import SwiftUI

struct DiaryView: View {
    @State private var mirror = NotesMirror.shared
    @State private var tab: Tab = .thoughts

    enum Tab: String, CaseIterable, Identifiable {
        case thoughts = "Thoughts", photos = "Photos", feelings = "Feelings", profile = "Profile",
             reflections = "Dreams"
        var id: String { rawValue }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            Group {
                switch tab {
                case .thoughts: ThoughtsList(thoughts: mirror.snapshot.thoughts, notes: mirror.snapshot.notes)
                case .photos: PhotosGrid(thoughts: mirror.snapshot.thoughts, notesDir: mirror.notesDir)
                case .feelings: FeelingsView(thoughts: mirror.snapshot.thoughts)
                case .profile: ProfileView(profile: mirror.snapshot.profile, highlights: mirror.snapshot.highlights)
                case .reflections: ReflectionsView(reflections: mirror.snapshot.reflections)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 560, minHeight: 480)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var header: some View {
        HStack(spacing: 12) {
            let mood = mirror.snapshot.mood
            Circle()
                .fill(Color.moodColor(mood))
                .frame(width: 14, height: 14)
                .shadow(color: Color.moodColor(mood).opacity(0.7), radius: 6)
            VStack(alignment: .leading, spacing: 1) {
                Text("buddy's diary").font(.title3.weight(.semibold))
                if let mood {
                    Text("feeling \(MoodColor.emoji(for: mood.label)) \(mood.label) · valence \(mood.valence) · arousal \(mood.arousal) · \(mood.time)")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("no feelings recorded yet").font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Picker("", selection: $tab) {
                ForEach(Tab.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .frame(width: 440)
            Button { mirror.sync() } label: { Image(systemName: "arrow.clockwise") }
                .help("Refresh now")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

extension Color {
    static func moodColor(_ t: Thought?) -> Color {
        guard let t else { return .secondary }
        let (r, g, b) = MoodColor.rgb(valence: t.valence, arousal: t.arousal, label: t.label)
        return Color(red: r, green: g, blue: b)
    }
}

// MARK: - Thoughts

private struct ThoughtsList: View {
    let thoughts: [Thought]
    let notes: [Note]
    @State private var showUnwritten = false

    private var visible: [Thought] { showUnwritten ? thoughts : thoughts.filter(\.written) }

    private var days: [(String, [Thought])] {
        var out: [(String, [Thought])] = []
        for t in visible {
            if let last = out.last, last.0 == t.day { out[out.count - 1].1.append(t) } else { out.append((t.day, [t])) }
        }
        return out
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Toggle("Show unwritten candidates (kept in memory only)", isOn: $showUnwritten)
                    .toggleStyle(.checkbox).font(.caption)
                Spacer()
                Text("\(visible.count) of \(thoughts.count)").font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16).padding(.vertical, 8)
            if thoughts.isEmpty {
                EmptyDiary(notes: notes)
            } else {
                List {
                    ForEach(days, id: \.0) { day, items in
                        Section(header: Text(dayLabel(day)).font(.caption.weight(.semibold))) {
                            ForEach(items) { ThoughtCard(thought: $0) }
                        }
                    }
                }
                .listStyle(.inset)
            }
        }
    }

    private func dayLabel(_ day: String) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        guard let d = f.date(from: day) else { return day }
        if Calendar.current.isDateInToday(d) { return "Today · \(day)" }
        if Calendar.current.isDateInYesterday(d) { return "Yesterday · \(day)" }
        return d.formatted(date: .complete, time: .omitted)
    }
}

private struct ThoughtCard: View {
    let thought: Thought
    @State private var open = false
    @State private var mirror = NotesMirror.shared
    private var starred: Bool { mirror.snapshot.highlights.contains { $0.hasPrefix(thought.thought) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 2).fill(Color.moodColor(thought)).frame(width: 4)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(thought.time).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        Text("\(MoodColor.emoji(for: thought.label)) \(thought.label)").font(.caption)
                            .foregroundStyle(Color.moodColor(thought))
                        Text("novelty \(thought.novelty) · importance \(thought.importance)")
                            .font(.caption2).foregroundStyle(.secondary)
                        if !thought.written {
                            Text("unwritten").font(.caption2).padding(.horizontal, 5).padding(.vertical, 1)
                                .background(Color.secondary.opacity(0.15), in: Capsule())
                        }
                        Spacer()
                        Text("yaw \(thought.yaw >= 0 ? "+" : "")\(thought.yaw)  pitch \(thought.pitch)")
                            .font(.caption2.monospacedDigit()).foregroundStyle(.tertiary)
                        Button {
                            if !starred { mirror.star(thought.thought) }
                        } label: {
                            Image(systemName: starred ? "star.fill" : "star")
                                .foregroundStyle(starred ? .yellow : .secondary)
                        }
                        .buttonStyle(.plain)
                        .help(starred ? "Starred — buddy will never forget this" : "Star: buddy must never forget this")
                    }
                    Text(thought.thought).font(.body).fixedSize(horizontal: false, vertical: true)
                    if let photo = PhotoFile.url(thought.photo, notesDir: mirror.notesDir) {
                        PhotoThumb(url: photo, height: open ? 220 : 96)
                            .help("buddy thought this was worth a picture — click the card for the full size")
                    }
                    if open {
                        Details(thought: thought)
                    }
                }
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture { withAnimation(.easeInOut(duration: 0.15)) { open.toggle() } }
        .help(open ? "Click to collapse" : "Click for observations, changes and tags")
    }
}

private struct Details: View {
    let thought: Thought

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if !thought.observations.isEmpty {
                Labeled("saw") { ForEach(thought.observations, id: \.self) { Text("• \($0)") } }
            }
            if !thought.changed.isEmpty {
                Labeled("changed") { ForEach(thought.changed, id: \.self) { Text("• \($0)") } }
            }
            if !thought.tags.isEmpty {
                HStack(spacing: 6) {
                    ForEach(thought.tags, id: \.self) { tag in
                        Text(tag).font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.15), in: Capsule())
                    }
                }
            }
            HStack(spacing: 12) {
                Gauge(value: Double(thought.valence + 100), in: 0...200) { Text("valence") }
                    .tint(Color.moodColor(thought))
                Gauge(value: Double(thought.arousal + 100), in: 0...200) { Text("arousal") }
                    .tint(Color.moodColor(thought))
            }
            .gaugeStyle(.accessoryLinearCapacity)
            .font(.caption2)
            .frame(maxWidth: 360)
        }
        .font(.callout)
        .padding(.top, 2)
    }
}

private struct Labeled<Content: View>: View {
    let label: String
    @ViewBuilder let content: Content

    init(_ label: String, @ViewBuilder content: () -> Content) { self.label = label; self.content = content() }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(label).font(.caption.weight(.semibold)).foregroundStyle(.secondary).frame(width: 56, alignment: .trailing)
            VStack(alignment: .leading, spacing: 2) { content }
        }
    }
}

private struct EmptyDiary: View {
    let notes: [Note]

    var body: some View {
        VStack(spacing: 8) {
            Spacer()
            Image(systemName: "eye").font(.system(size: 36)).foregroundStyle(.secondary)
            Text(notes.isEmpty ? "Nothing noticed yet." : "The memory file has not been written yet.")
                .font(.headline)
            Text(notes.isEmpty ? "buddy explores the room once Claude has been idle for ten minutes, and writes what it finds interesting."
                 : "Older notes appear in the widget; the diary fills in as new thoughts arrive.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 380)
            Spacer()
        }
    }
}

// MARK: - Feelings

private struct FeelingsView: View {
    let thoughts: [Thought]

    private var points: [Thought] { Array(thoughts.prefix(120).reversed()) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if points.count < 2 {
                Spacer()
                Text("A feelings timeline appears after a few thoughts.").foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                Spacer()
            } else {
                Text("Valence (how good) and arousal (how keen), per thought. Colours match the robot's LEDs.")
                    .font(.caption).foregroundStyle(.secondary)
                Chart {
                    ForEach(points) { t in
                        LineMark(x: .value("time", t.date), y: .value("valence", t.valence), series: .value("s", "valence"))
                            .foregroundStyle(.green)
                            .interpolationMethod(.monotone)
                        LineMark(x: .value("time", t.date), y: .value("arousal", t.arousal), series: .value("s", "arousal"))
                            .foregroundStyle(.orange)
                            .interpolationMethod(.monotone)
                        PointMark(x: .value("time", t.date), y: .value("valence", t.valence))
                            .foregroundStyle(Color.moodColor(t))
                            .symbolSize(40)
                    }
                    RuleMark(y: .value("zero", 0)).foregroundStyle(.secondary.opacity(0.3))
                }
                .chartYScale(domain: -100...100)
                .chartLegend(.hidden)
                .frame(minHeight: 220)
                HStack(spacing: 16) {
                    Label("valence", systemImage: "circle.fill").foregroundStyle(.green)
                    Label("arousal", systemImage: "circle.fill").foregroundStyle(.orange)
                }
                .font(.caption)
                MoodTally(thoughts: thoughts)
            }
        }
        .padding(16)
    }
}

private struct MoodTally: View {
    let thoughts: [Thought]

    private var counts: [(String, Int)] {
        var d: [String: Int] = [:]
        for t in thoughts { d[t.label, default: 0] += 1 }
        return d.sorted { $0.value > $1.value }
    }

    var body: some View {
        HStack(spacing: 10) {
            ForEach(counts, id: \.0) { label, n in
                HStack(spacing: 4) {
                    Text(MoodColor.emoji(for: label))
                    Text("\(label) \(n)").font(.caption)
                }
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Color.secondary.opacity(0.12), in: Capsule())
            }
        }
    }
}

// MARK: - Profile

private struct ProfileView: View {
    let profile: String
    let highlights: [String]

    private var blocks: [(String, String)] {
        var out: [(String, String)] = []
        for chunk in profile.components(separatedBy: "\n## ") {
            var text = chunk
            if text.hasPrefix("## ") { text.removeFirst(3) }
            guard let nl = text.firstIndex(of: "\n") else { continue }
            out.append((String(text[..<nl]).trimmingCharacters(in: .whitespaces), String(text[nl...]).trimmingCharacters(in: .whitespacesAndNewlines)))
        }
        return out
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("★ Never forget").font(.headline)
                    if highlights.isEmpty {
                        Text("Nothing starred yet. Press ★ on a thought to make it something buddy always keeps in mind; buddy proposes candidates in its dreams but never stars them itself.")
                            .font(.callout).foregroundStyle(.secondary)
                    } else {
                        ForEach(highlights.reversed(), id: \.self) { Text("★ \($0)").font(.body).textSelection(.enabled) }
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.yellow.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
                if profile.isEmpty {
                    Text("No profile yet. buddy writes one after its first evening reflection: what is in the room, what it has learned about you, its own open questions, and what it ignores.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(blocks, id: \.0) { title, body in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(title.capitalized).font(.headline)
                            Text(body).font(.body).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
            .padding(16)
        }
    }
}

// MARK: - Reflections

private struct ReflectionsView: View {
    let reflections: [Reflection]

    var body: some View {
        if reflections.isEmpty {
            VStack {
                Spacer()
                Text("No dreams yet. Each evening (or after a busy day) buddy sleeps on the day: what the notes add up to, what it should keep, and what it proposes to never forget (★ candidates — star them from the Thoughts tab, or here in the file).")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
                Spacer()
            }
            .frame(maxWidth: .infinity)
        } else {
            List(reflections) { r in
                Section(header: Text(r.date).font(.caption.weight(.semibold))) {
                    ForEach(r.insights, id: \.self) { Text("• \($0)").fixedSize(horizontal: false, vertical: true) }
                }
            }
            .listStyle(.inset)
        }
    }
}


// MARK: - Photos

/// Resolving a record's photo path. The helper app is not sandboxed, so it
/// reads the daemon's own file; the mirrored copy in the App Group container
/// is the fallback (and the only thing the widget extension can read).
enum PhotoFile {
    static func url(_ rel: String?, notesDir: URL) -> URL? {
        guard let rel, !rel.isEmpty else { return nil }
        let direct = notesDir.appendingPathComponent(rel, isDirectory: false)
        if FileManager.default.fileExists(atPath: direct.path) { return direct }
        return AppGroup.photo(rel)
    }
}

private struct PhotoThumb: View {
    let url: URL
    var height: CGFloat = 96

    var body: some View {
        if let image = NSImage(contentsOf: url) {
            Image(nsImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxWidth: .infinity, maxHeight: height, alignment: .leading)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.25)))
        }
    }
}

/// Everything buddy photographed, newest first, with the thought it kept it for.
private struct PhotosGrid: View {
    let thoughts: [Thought]
    let notesDir: URL

    private var items: [(Thought, URL)] {
        thoughts.compactMap { t in PhotoFile.url(t.photo, notesDir: notesDir).map { (t, $0) } }
    }

    var body: some View {
        if items.isEmpty {
            VStack(spacing: 8) {
                Image(systemName: "camera").font(.system(size: 36)).foregroundStyle(.secondary)
                Text("No photos yet").font(.title3)
                Text("buddy keeps a picture when a view surprises it — not for every thought.")
                    .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding()
        } else {
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 16)], spacing: 16) {
                    ForEach(items, id: \.0.id) { thought, url in
                        VStack(alignment: .leading, spacing: 6) {
                            PhotoThumb(url: url, height: 180)
                            HStack(spacing: 6) {
                                Text("\(thought.day) \(thought.time)")
                                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                Text("\(MoodColor.emoji(for: thought.label)) \(thought.label)")
                                    .font(.caption).foregroundStyle(Color.moodColor(thought))
                                Spacer()
                                Text("novelty \(thought.novelty)")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            }
                            Text(thought.thought).font(.callout).fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(10)
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                        .onTapGesture { NSWorkspace.shared.open(url) }
                        .help("Click to open the full picture")
                    }
                }
                .padding(16)
            }
        }
    }
}
