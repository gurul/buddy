// Shared between the helper app and the widget extension.
//
// Data flow: cc-buddy-bridge writes, under `~/.config/cc-buddy-bridge/notes/`:
//   YYYY-MM-DD.md   diary lines `- HH:MM yaw=+20 pitch=40 — <thought>` and, at
//                   night, a `## Evening reflection` section of `- insight` lines
//   memory.jsonl    one record per frame (bridge/src/cc_buddy_bridge/diary.py):
//                   thought, observations, what changed, tags, novelty,
//                   importance, whether it was written, valence/arousal/label
//   profile.md      ROOM / HUMAN / SELF / RULES blocks buddy rewrites nightly
// The (unsandboxed) helper mirrors all of it into `notes.json` inside the App
// Group container; the (sandboxed) widget only ever reads that JSON.

import Foundation

enum AppGroup {
    /// macOS App Groups must be prefixed with the Team ID.
    static let id = "SJ8BKXTNUS.com.github.cc-buddy-bridge"
    static let fileName = "notes.json"
    /// Tapping the widget opens this in the helper app (Info.plist CFBundleURLTypes).
    static let diaryURL = URL(string: "stackchan://diary")!

    static var containerURL: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: id)
    }

    static var notesFileURL: URL? {
        containerURL?.appendingPathComponent(fileName, isDirectory: false)
    }
}

struct Note: Codable, Hashable, Identifiable, Sendable {
    /// `YYYY-MM-DD`, taken from the daily file name.
    let date: String
    /// `HH:MM` as written by the daemon.
    let time: String
    let yaw: Double?
    let pitch: Double?
    let text: String

    var id: String { "\(date)T\(time) \(text)" }

    /// `yaw=+20 pitch=40` rendered for the dim secondary line.
    var pose: String? {
        var parts: [String] = []
        if let yaw { parts.append("yaw \(Self.signed(yaw))") }
        if let pitch { parts.append("pitch \(Self.signed(pitch))") }
        return parts.isEmpty ? nil : parts.joined(separator: "  ")
    }

    private static func signed(_ v: Double) -> String {
        let rounded = v.rounded()
        let s = rounded == v ? String(Int(rounded)) : String(format: "%.1f", v)
        return v > 0 ? "+\(s)" : s
    }
}

/// One memory record from `memory.jsonl` — everything behind a diary line.
struct Thought: Codable, Hashable, Identifiable, Sendable {
    let id: Int
    let ts: Double
    let yaw: Int
    let pitch: Int
    let thought: String
    var observations: [String] = []
    var changed: [String] = []
    var tags: [String] = []
    var novelty: Int = 5
    var importance: Int = 3
    var written: Bool = false
    var valence: Int = 0
    var arousal: Int = 0
    var label: String = "calm"

    var date: Date { Date(timeIntervalSince1970: ts) }
    var day: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
    var time: String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f.string(from: date)
    }

    enum CodingKeys: String, CodingKey {
        case id, ts, yaw, pitch, thought, observations, changed, tags, novelty, importance, written, valence, arousal, label
    }

    init(id: Int, ts: Double, yaw: Int, pitch: Int, thought: String, observations: [String] = [], changed: [String] = [],
         tags: [String] = [], novelty: Int = 5, importance: Int = 3, written: Bool = false, valence: Int = 0,
         arousal: Int = 0, label: String = "calm") {
        self.id = id; self.ts = ts; self.yaw = yaw; self.pitch = pitch; self.thought = thought
        self.observations = observations; self.changed = changed; self.tags = tags; self.novelty = novelty
        self.importance = importance; self.written = written; self.valence = valence; self.arousal = arousal
        self.label = label
    }

    /// Tolerant decoding: the daemon may add fields; older lines may lack some.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        ts = try c.decode(Double.self, forKey: .ts)
        yaw = (try? c.decode(Int.self, forKey: .yaw)) ?? 0
        pitch = (try? c.decode(Int.self, forKey: .pitch)) ?? 45
        thought = try c.decode(String.self, forKey: .thought)
        observations = (try? c.decode([String].self, forKey: .observations)) ?? []
        changed = (try? c.decode([String].self, forKey: .changed)) ?? []
        tags = (try? c.decode([String].self, forKey: .tags)) ?? []
        novelty = (try? c.decode(Int.self, forKey: .novelty)) ?? 5
        importance = (try? c.decode(Int.self, forKey: .importance)) ?? 3
        written = (try? c.decode(Bool.self, forKey: .written)) ?? false
        valence = (try? c.decode(Int.self, forKey: .valence)) ?? 0
        arousal = (try? c.decode(Int.self, forKey: .arousal)) ?? 0
        label = (try? c.decode(String.self, forKey: .label)) ?? "calm"
    }
}

/// A day's `## Evening reflection` insights.
struct Reflection: Codable, Hashable, Identifiable, Sendable {
    let date: String
    let insights: [String]
    var id: String { date }
}

struct NotesSnapshot: Codable, Sendable {
    var updatedAt: Date
    var notesDir: String
    /// Newest first, capped at `NoteStore.limit` by the helper.
    var notes: [Note]
    /// Memory records, newest first, capped at `NoteStore.limit`.
    var thoughts: [Thought] = []
    /// `profile.md`, or empty until buddy has reflected once.
    var profile: String = ""
    /// Newest day first.
    var reflections: [Reflection] = []
    /// `highlights.md`: what the human starred, in file order (oldest first).
    var highlights: [String] = []

    enum CodingKeys: String, CodingKey { case updatedAt, notesDir, notes, thoughts, profile, reflections, highlights }

    init(updatedAt: Date, notesDir: String, notes: [Note], thoughts: [Thought] = [], profile: String = "",
         reflections: [Reflection] = [], highlights: [String] = []) {
        self.updatedAt = updatedAt; self.notesDir = notesDir; self.notes = notes
        self.thoughts = thoughts; self.profile = profile; self.reflections = reflections; self.highlights = highlights
    }

    /// Older mirrors wrote only `notes`; keep reading them.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        updatedAt = try c.decode(Date.self, forKey: .updatedAt)
        notesDir = (try? c.decode(String.self, forKey: .notesDir)) ?? ""
        notes = (try? c.decode([Note].self, forKey: .notes)) ?? []
        thoughts = (try? c.decode([Thought].self, forKey: .thoughts)) ?? []
        profile = (try? c.decode(String.self, forKey: .profile)) ?? ""
        reflections = (try? c.decode([Reflection].self, forKey: .reflections)) ?? []
        highlights = (try? c.decode([String].self, forKey: .highlights)) ?? []
    }

    /// The newest feeling, from the newest memory record.
    var mood: Thought? { thoughts.first }

    static let empty = NotesSnapshot(updatedAt: .distantPast, notesDir: "", notes: [])

    static let placeholder: NotesSnapshot = {
        let now = Date().timeIntervalSince1970
        return NotesSnapshot(
            updatedAt: .now,
            notesDir: "",
            notes: [
                Note(date: "2026-09-06", time: "10:42", yaw: 20, pitch: 40, text: "Someone finished the coffee and left the spoon in the blue mug — again."),
                Note(date: "2026-09-06", time: "10:31", yaw: -15, pitch: 10, text: "The blinds are half down at half past ten; my human likes the room dim before lunch."),
                Note(date: "2026-09-06", time: "10:17", yaw: 0, pitch: 0, text: "Third morning in a row the chair is pushed in — when does it get used?"),
            ],
            thoughts: [
                Thought(id: 3, ts: now, yaw: 20, pitch: 40, thought: "Someone finished the coffee and left the spoon in the blue mug — again.",
                        observations: ["blue mug, spoon inside", "keyboard centred"], changed: ["mug empty"], tags: ["mug", "spoon"],
                        novelty: 6, importance: 4, written: true, valence: 30, arousal: 35, label: "curious"),
                Thought(id: 2, ts: now - 660, yaw: -15, pitch: 10, thought: "The blinds are half down at half past ten; my human likes the room dim before lunch.",
                        observations: ["blinds half down"], changed: [], tags: ["blinds"], novelty: 5, importance: 3, written: true,
                        valence: 10, arousal: -10, label: "calm"),
                Thought(id: 1, ts: now - 1500, yaw: 0, pitch: 0, thought: "Third morning in a row the chair is pushed in — when does it get used?",
                        observations: ["chair pushed in"], changed: [], tags: ["chair"], novelty: 7, importance: 5, written: true,
                        valence: 20, arousal: 20, label: "curious"),
            ],
            profile: "## ROOM\nA desk with a blue mug that moves.\n\n## HUMAN\nAway around 13:00 most days.\n\n## SELF\nI am buddy.\n\n## RULES\nIgnore lighting.",
            reflections: [Reflection(date: "2026-09-05", insights: ["My human leaves around 13:00 most days (12, 15, 18)."])]
        )
    }()
}

// MARK: - Mood colour (mirrors firmware mood.cpp: hue from valence, brightness from arousal)

enum MoodColor {
    /// RGB 0...1 for a feeling. Positive = green (keen) / cyan (calm), negative = orange,
    /// affection = pink, startled = red; brighter with arousal.
    static func rgb(valence: Int, arousal: Int, label: String) -> (Double, Double, Double) {
        let v = Double(valence) / 100.0, a = Double(arousal) / 100.0
        var hue: Double = v >= 0 ? (a < 0 ? 170 : 120) : 20
        if label == "affection" { hue = 320 }
        if label == "startled" { hue = 0 }
        let sat = min(1.0, 0.4 + 0.5 * abs(v))
        let bri = min(1.0, max(0.35, 0.45 + 0.5 * (a + 1) / 2))
        return hsv(hue, sat, bri)
    }

    static func hsv(_ h: Double, _ s: Double, _ v: Double) -> (Double, Double, Double) {
        let c = v * s
        let x = c * (1 - abs((h / 60).truncatingRemainder(dividingBy: 2) - 1))
        let m = v - c
        var r = 0.0, g = 0.0, b = 0.0
        switch h {
        case ..<60: r = c; g = x
        case ..<120: r = x; g = c
        case ..<180: g = c; b = x
        case ..<240: g = x; b = c
        case ..<300: r = x; b = c
        default: r = c; b = x
        }
        return (r + m, g + m, b + m)
    }

    static func emoji(for label: String) -> String {
        switch label {
        case "curious": "🧐"
        case "happy": "😊"
        case "surprised": "😮"
        case "startled": "😳"
        case "bored": "😑"
        case "lonely": "🥺"
        case "affection": "🥰"
        default: "🙂"
        }
    }
}

enum NoteLine {
    /// Parses one daemon line. Returns nil for headers, blanks, or anything not a note bullet.
    /// Accepted shapes:
    ///   `- 10:42 yaw=+20 pitch=40 — sentence`
    ///   `- 10:42 — sentence`
    ///   `- 10:42 yaw=+20 pitch=40 - sentence`   (ASCII dash fallback)
    static func parse(_ raw: String, date: String) -> Note? {
        var line = raw.trimmingCharacters(in: .whitespaces)
        guard line.hasPrefix("- ") || line.hasPrefix("* ") else { return nil }
        line.removeFirst(2)
        line = line.trimmingCharacters(in: .whitespaces)

        let timeEnd = line.index(line.startIndex, offsetBy: 5, limitedBy: line.endIndex) ?? line.endIndex
        let time = String(line[..<timeEnd])
        guard isTime(time) else { return nil }
        var rest = String(line[timeEnd...]).trimmingCharacters(in: .whitespaces)

        let separators = [" — ", " – ", " -- ", " - "]
        var pose = ""
        var text = rest
        for sep in separators {
            if let r = rest.range(of: sep) {
                pose = String(rest[..<r.lowerBound])
                text = String(rest[r.upperBound...])
                break
            }
        }
        if text == rest, rest.hasPrefix("—") {
            rest.removeFirst()
            text = rest
        }
        text = text.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }

        var yaw: Double?
        var pitch: Double?
        for token in pose.split(separator: " ") {
            if let v = token.split(separator: "=", maxSplits: 1).last, token.hasPrefix("yaw=") {
                yaw = Double(v)
            } else if let v = token.split(separator: "=", maxSplits: 1).last, token.hasPrefix("pitch=") {
                pitch = Double(v)
            }
        }
        return Note(date: date, time: time, yaw: yaw, pitch: pitch, text: text)
    }

    /// The `- insight` lines under a `## Evening reflection` header.
    static func reflections(in body: String) -> [String] {
        var out: [String] = []
        var inSection = false
        for raw in body.split(separator: "\n", omittingEmptySubsequences: true) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("## ") {
                let l = line.lowercased()
                inSection = l.contains("dream") || l.contains("reflection")
                continue
            }
            guard inSection, line.hasPrefix("- ") else { continue }
            let text = line.dropFirst(2).trimmingCharacters(in: .whitespaces)
            if !text.isEmpty { out.append(text) }
        }
        return out
    }

    private static func isTime(_ s: String) -> Bool {
        let chars = Array(s)
        guard chars.count == 5, chars[2] == ":" else { return false }
        return [chars[0], chars[1], chars[3], chars[4]].allSatisfy(\.isNumber)
    }
}

enum NoteStore {
    static let limit = 200

    private static var encoder: JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        return e
    }

    private static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }

    /// Reads the mirrored snapshot from the group container. Missing/corrupt → `.empty`.
    static func load() -> NotesSnapshot {
        guard let url = AppGroup.notesFileURL,
              let data = try? Data(contentsOf: url),
              let snap = try? decoder.decode(NotesSnapshot.self, from: data)
        else { return .empty }
        return snap
    }

    /// Atomically writes the snapshot into the group container.
    static func save(_ snapshot: NotesSnapshot) throws {
        guard let url = AppGroup.notesFileURL else {
            throw CocoaError(.fileNoSuchFile, userInfo: [NSLocalizedDescriptionKey: "App Group container \(AppGroup.id) unavailable"])
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(snapshot).write(to: url, options: .atomic)
    }

    static func highlightsURL(notesDir: URL) -> URL { notesDir.appendingPathComponent("highlights.md", isDirectory: false) }

    /// `★ claim` / `- ★ claim` lines of highlights.md, file order.
    static func readHighlights(notesDir: URL) -> [String] {
        guard let body = try? String(contentsOf: highlightsURL(notesDir: notesDir), encoding: .utf8) else { return [] }
        var out: [String] = []
        for raw in body.split(separator: "\n", omittingEmptySubsequences: true) {
            var t = raw.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("-") { t.removeFirst(); t = t.trimmingCharacters(in: .whitespaces) }
            guard t.hasPrefix("★") else { continue }
            t.removeFirst()
            t = t.trimmingCharacters(in: .whitespaces)
            if !t.isEmpty { out.append(t) }
        }
        return out
    }

    /// The human stars a thought: append-only, never pruned, dated. buddy reads it, never writes it.
    static func star(_ claim: String, notesDir: URL, date: Date = .now) throws {
        let url = highlightsURL(notesDir: notesDir)
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm"
        var body = (try? String(contentsOf: url, encoding: .utf8)) ?? "# never forget\n\nStarred by hand from the diary window. Append-only.\n\n"
        if !body.hasSuffix("\n") { body += "\n" }
        body += "- ★ \(claim.trimmingCharacters(in: .whitespacesAndNewlines)) — starred \(f.string(from: date))\n"
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    /// Reads the daemon's notes directory: diary lines (newest first), memory records
    /// (newest first), the profile and the reflections. Returns the snapshot plus the
    /// newest day file (the one worth watching for appends).
    static func read(notesDir: URL) -> (snapshot: NotesSnapshot, newestFile: URL?) {
        let fm = FileManager.default
        let files = ((try? fm.contentsOfDirectory(at: notesDir, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "md" && $0.lastPathComponent != "profile.md" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }

        var notes: [Note] = []
        var reflections: [Reflection] = []
        for file in files {
            let date = file.deletingPathExtension().lastPathComponent
            guard let body = try? String(contentsOf: file, encoding: .utf8) else { continue }
            if notes.count < limit {
                for line in body.split(separator: "\n", omittingEmptySubsequences: true).reversed() {
                    if let note = NoteLine.parse(String(line), date: date) {
                        notes.append(note)
                        if notes.count >= limit { break }
                    }
                }
            }
            let insights = NoteLine.reflections(in: body)
            if !insights.isEmpty, reflections.count < 30 {
                reflections.append(Reflection(date: date, insights: insights))
            }
        }

        var thoughts: [Thought] = []
        let memory = notesDir.appendingPathComponent("memory.jsonl", isDirectory: false)
        if let body = try? String(contentsOf: memory, encoding: .utf8) {
            let dec = JSONDecoder()
            for line in body.split(separator: "\n", omittingEmptySubsequences: true).reversed() {
                guard let data = line.data(using: .utf8), let t = try? dec.decode(Thought.self, from: data) else { continue }
                thoughts.append(t)
                if thoughts.count >= limit { break }
            }
        }
        let profileURL = notesDir.appendingPathComponent("profile.md", isDirectory: false)
        let profile = (try? String(contentsOf: profileURL, encoding: .utf8)) ?? ""
        let highlights = readHighlights(notesDir: notesDir)

        let snap = NotesSnapshot(updatedAt: .now, notesDir: notesDir.path, notes: notes, thoughts: thoughts,
                                 profile: profile, reflections: reflections, highlights: highlights)
        return (snap, files.first)
    }
}
