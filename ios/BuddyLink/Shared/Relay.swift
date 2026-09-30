// The relay: presses on the stick become a call to buddy; buddy's replies go back to the stick's speaker.
//
// A call is opened on the first press and hung up after a quiet spell, so buddy's other chat replies (texts on
// Telegram) are not read out on the stick all day: a call hears everything the chat says while it is open.
import Foundation
#if os(iOS)
import UIKit
#endif

struct Pairing: Equatable {
    var base: URL
    var token: String

    /// buddylink://pair?u=<https origin>&t=<token>, from the pairing page (stick_link.PAIR_PAGE).
    init?(link: URL) {
        guard link.scheme == "buddylink", link.host == "pair",
              let items = URLComponents(url: link, resolvingAgainstBaseURL: false)?.queryItems,
              let u = items.first(where: { $0.name == "u" })?.value, let base = URL(string: u),
              base.scheme == "https" || base.host == "127.0.0.1",
              let t = items.first(where: { $0.name == "t" })?.value, t.count >= 32 else { return nil }
        self.base = base
        self.token = t
    }

    init(base: URL, token: String) {
        self.base = base
        self.token = token
    }
}

@MainActor
final class Relay: ObservableObject {
    static let hangUpAfter: TimeInterval = 45       // quiet this long after a turn: the call ends
    static let batchSamples = 2400                  // 100 ms of the press per WebSocket frame

    @Published private(set) var stickStatus = "Starting…"
    @Published private(set) var callStatus = "Idle"
    @Published private(set) var lastHeard = ""
    @Published private(set) var firmware = ""
    @Published private(set) var battery = -1
    @Published var pairing: Pairing? { didSet { if let pairing { Keychain.save(pairing) } } }

    let stick: StickLink
    private let call = CallSocket()
    private var batch: [Int16] = []
    private var talking = false
    private var hangUpTimer: Timer?
    #if os(iOS)
    private var background: UIBackgroundTaskIdentifier = .invalid
    #endif

    init(restore: Bool = true) {
        stick = StickLink(restore: restore)
        pairing = Keychain.load()
        stick.onStatus = { [weak self] in self?.stickStatus = $0 }
        stick.onEvent = { [weak self] in self?.fromStick($0) }
        call.onEvent = { [weak self] in self?.fromBuddy($0) }
    }

    func pair(with link: URL) -> Bool {
        guard let p = Pairing(link: link) else { return false }
        pairing = p
        call.hangUp()
        callStatus = "Paired with \(p.base.host ?? "buddy")"
        stick.send(["t": "state", "s": "ready", "note": "paired with buddy"])
        return true
    }

    // ---- the stick ----------------------------------------------------------------------------------------

    private func fromStick(_ event: StickEvent) {
        switch event {
        case .ready(let fw, let batt):
            firmware = fw
            battery = batt
            if pairing == nil {
                stick.send(["t": "state", "s": "offline", "note": "Send /stick to buddy in Telegram, tap its button"])
            }
        case .talk:
            guard let pairing else {
                stick.send(["t": "state", "s": "offline", "note": "Not paired: send /stick to buddy in Telegram"])
                return
            }
            keepAwake()
            hangUpTimer?.invalidate()
            stick.dropAudio()
            talking = true
            batch.removeAll()
            if !call.isOpen {
                callStatus = "Calling buddy…"
                call.open(base: pairing.base, token: pairing.token)
            }
            call.talk()
        case .audio(let pcm):
            guard talking else { return }
            batch += pcm
            if batch.count >= Self.batchSamples {
                call.audio(batch)
                batch.removeAll(keepingCapacity: true)
            }
        case .done:
            guard talking else { return }
            talking = false
            if !batch.isEmpty { call.audio(batch) }
            batch.removeAll()
            call.done()
            callStatus = "buddy is thinking…"
            armHangUp()
        case .ping:
            keepAwake()
        case .stat:
            break
        case .disconnected:
            talking = false
            call.hangUp()
            callStatus = "Idle"
            letSleep()
        }
    }

    // ---- buddy ----------------------------------------------------------------------------------------------

    private func fromBuddy(_ event: CallEvent) {
        switch event {
        case .open:
            callStatus = "On a call with buddy"
        case .state(let state, let note):
            var m: [String: Any] = ["t": "state", "s": state]
            if let note { m["note"] = Self.ascii(note) }
            stick.send(m)
            if state == "speaking" { callStatus = "buddy is speaking" }
            if state == "listening" {
                stick.flushAudio()
                callStatus = "On a call with buddy"
                armHangUp()
            }
        case .heard(let text):
            lastHeard = text
            stick.send(["t": "heard", "text": Self.ascii(text)])
        case .audio(let pcm):
            stick.sendAudio(pcm)
            armHangUp()
        case .flush:
            stick.dropAudio()
            stick.send(["t": "flush"])
        case .ended(let reason):
            callStatus = reason
            talking = false
            let offline = reason.contains("address") || reason.contains("refused") || reason.contains("reach")
                || reason.contains("offline") || reason.contains("owner")
            stick.send(["t": "state", "s": offline ? "offline" : "listening", "note": Self.ascii(reason)])
            stick.send(["t": "end"])
            letSleep()
        case .failed(let why):
            callStatus = why
        }
    }

    private func armHangUp() {
        hangUpTimer?.invalidate()
        hangUpTimer = Timer.scheduledTimer(withTimeInterval: Self.hangUpAfter, repeats: false) { [weak self] _ in
            Task { @MainActor in
                guard let self, !self.talking, self.stick.queuedAudioFrames == 0 else { return }
                self.call.hangUp()
                self.callStatus = "Idle"
                self.stick.send(["t": "end"])
                self.letSleep()
            }
        }
    }

    // ---- staying awake with the screen locked -------------------------------------------------------------
    // Bluetooth events wake the app (bluetooth-central background mode); the stick pings once a second while a
    // turn is live, and a background task covers the gaps, so the WebSocket keeps being served.

    private func keepAwake() {
        #if os(iOS)
        guard background == .invalid else { return }
        background = UIApplication.shared.beginBackgroundTask(withName: "buddy call") { [weak self] in
            Task { @MainActor in self?.letSleep() }
        }
        #endif
    }

    private func letSleep() {
        #if os(iOS)
        if background != .invalid {
            UIApplication.shared.endBackgroundTask(background)
            background = .invalid
        }
        #endif
    }

    /// The stick's font is ASCII: fold accents and punctuation, drop the rest.
    static func ascii(_ s: String) -> String {
        let folded = s.applyingTransform(.toLatin, reverse: false)?
            .applyingTransform(.stripDiacritics, reverse: false) ?? s
        let map: [Character: String] = ["\u{2018}": "'", "\u{2019}": "'", "\u{201C}": "\"", "\u{201D}": "\"",
                                        "\u{2014}": "-", "\u{2013}": "-", "\u{2026}": "..."]
        return String(folded.flatMap { c -> String in
            if let m = map[c] { return m }
            return c.isASCII && !(c.asciiValue.map { $0 < 32 } ?? true) ? String(c) : ""
        })
    }
}

/// The pairing, in the Keychain (the token is a password to buddy).
enum Keychain {
    private static let service = "com.github.cc-buddy-bridge.BuddyLink"
    private static let account = "pairing"

    static func save(_ p: Pairing) {
        let data = Data("\(p.base.absoluteString)\n\(p.token)".utf8)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: account]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly   // locked-phone calls
        SecItemAdd(add as CFDictionary, nil)
    }

    static func load() -> Pairing? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true]
        var out: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        let parts = String(decoding: data, as: UTF8.self).split(separator: "\n", maxSplits: 1).map(String.init)
        guard parts.count == 2, let base = URL(string: parts[0]) else { return nil }
        return Pairing(base: base, token: parts[1])
    }
}
