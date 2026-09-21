// BuddyPower — buddy's power switch, shared between the card and the helper.
//
// The desktop card is sandboxed and cannot touch launchd, but it shares the
// App Group container with the menu-bar helper. So the card's button writes a
// request file there, the helper (which can run launchctl, see BuddyService)
// watches for it, does the work, and writes the resulting state back for the
// card to draw. Two small JSON files, newest timestamp wins:
//
//   power-request.json  { on: Bool, at: Date }   written by the card's button
//   power.json          { on, switchable, line, at }   written by the helper
//
// A request newer than the state is one the helper has not answered yet: the
// card shows it as "stopping…" / "starting…" until the helper writes a state
// stamped after it.

import Foundation

enum BuddyPower {
    static let requestFileName = "power-request.json"
    static let stateFileName = "power.json"

    static var requestURL: URL? { AppGroup.containerURL?.appendingPathComponent(requestFileName, isDirectory: false) }
    static var stateURL: URL? { AppGroup.containerURL?.appendingPathComponent(stateFileName, isDirectory: false) }

    /// What the card asked for.
    struct Request: Codable, Equatable, Sendable {
        var on: Bool
        var at: Date
    }

    /// What the helper last saw (a projection of ServiceState the card can draw).
    struct State: Codable, Equatable, Sendable {
        /// launchd holds the agent and has not been told to stop.
        var on: Bool
        /// A button makes sense: installed as a service, not run by hand from a terminal.
        var switchable: Bool
        /// The menu's one-liner, for the card's off state.
        var line: String
        var at: Date
    }

    /// How long the card waits for the helper before saying it is not there.
    static let helperTimeout: TimeInterval = 20

    /// What the card should draw.
    enum Face: Equatable, Sendable {
        /// No helper has written a state yet (or this Mac has no service): no button.
        case unknown
        case on
        case off
        case turningOn
        case turningOff
        /// A request sat unanswered past `helperTimeout`: the helper is not running.
        case waiting
    }

    static func face(now: Date = .now) -> Face {
        let state = readState()
        if let request = readRequest(), request.at > (state?.at ?? .distantPast) {
            if now.timeIntervalSince(request.at) > helperTimeout { return .waiting }
            return request.on ? .turningOn : .turningOff
        }
        guard let state, state.switchable else { return .unknown }
        return state.on ? .on : .off
    }

    // MARK: - files

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys]
        return e
    }()

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()

    static func readRequest() -> Request? { read(requestURL) }
    static func readState() -> State? { read(stateURL) }

    /// The card's button: ask the helper to turn buddy on or off.
    static func writeRequest(on: Bool, at: Date = .now) throws {
        try write(Request(on: on, at: at), to: requestURL)
    }

    static func writeState(_ state: State) throws {
        try write(state, to: stateURL)
    }

    private static func read<T: Decodable>(_ url: URL?) -> T? {
        guard let url, let data = try? Data(contentsOf: url) else { return nil }
        return try? decoder.decode(T.self, from: data)
    }

    private static func write<T: Encodable>(_ value: T, to url: URL?) throws {
        guard let url else { throw CocoaError(.fileNoSuchFile) }
        // Atomic: the file is renamed into place, which is one event on the
        // directory for the helper's watcher and never a half-written read.
        try encoder.encode(value).write(to: url, options: .atomic)
    }
}
