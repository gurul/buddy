// Shared between the helper app and the widget extension.
//
// Data flow: cc-buddy-bridge writes `~/.config/cc-buddy-bridge/notes/YYYY-MM-DD.md`
// with lines `- HH:MM yaw=+20 pitch=40 — <sentence>`. The (unsandboxed) helper
// mirrors the newest lines into `notes.json` inside the App Group container; the
// (sandboxed) widget only ever reads that JSON.

import Foundation

enum AppGroup {
    /// macOS App Groups must be prefixed with the Team ID.
    static let id = "SJ8BKXTNUS.com.github.cc-buddy-bridge"
    static let fileName = "notes.json"

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

struct NotesSnapshot: Codable, Sendable {
    var updatedAt: Date
    var notesDir: String
    /// Newest first, capped at `NoteStore.limit` by the helper.
    var notes: [Note]

    static let empty = NotesSnapshot(updatedAt: .distantPast, notesDir: "", notes: [])

    static let placeholder = NotesSnapshot(
        updatedAt: .now,
        notesDir: "",
        notes: [
            Note(date: "2026-09-05", time: "10:42", yaw: 20, pitch: 40, text: "A coffee mug appeared next to the keyboard."),
            Note(date: "2026-09-05", time: "10:31", yaw: -15, pitch: 10, text: "The window blinds are half open now."),
            Note(date: "2026-09-05", time: "10:17", yaw: 0, pitch: 0, text: "Someone is sitting at the desk again."),
        ]
    )
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
            // `- 10:42 —sentence` with no leading space before the dash
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

    /// Reads the daemon's notes directory: newest day file first, newest line first, capped at `limit`.
    /// Returns the snapshot plus the newest day file (the one worth watching for appends).
    static func read(notesDir: URL) -> (snapshot: NotesSnapshot, newestFile: URL?) {
        let fm = FileManager.default
        let files = ((try? fm.contentsOfDirectory(at: notesDir, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "md" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }

        var notes: [Note] = []
        for file in files where notes.count < limit {
            let date = file.deletingPathExtension().lastPathComponent
            guard let body = try? String(contentsOf: file, encoding: .utf8) else { continue }
            for line in body.split(separator: "\n", omittingEmptySubsequences: true).reversed() {
                if let note = NoteLine.parse(String(line), date: date) {
                    notes.append(note)
                    if notes.count >= limit { break }
                }
            }
        }
        let snap = NotesSnapshot(updatedAt: .now, notesDir: notesDir.path, notes: notes)
        return (snap, files.first)
    }
}
