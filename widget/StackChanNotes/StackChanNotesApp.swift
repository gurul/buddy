// StackChan Notes helper — a Dock-less (LSUIElement) menu-bar app whose only job is to
// mirror the bridge daemon's note files into the App Group container the widget reads.
// It is deliberately NOT sandboxed so it can read ~/.config/cc-buddy-bridge/notes.

import AppKit
import ServiceManagement
import SwiftUI
import os

@main
struct StackChanNotesApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra("StackChan Notes", systemImage: "note.text") {
            MenuContent()
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let log = Logger(subsystem: "com.github.cc-buddy-bridge.StackChanNotes", category: "app")
    private static let registeredKey = "loginItemRegistered"

    func applicationDidFinishLaunching(_ notification: Notification) {
        registerLoginItemOnce()
        NotesMirror.shared.start()
    }

    /// Registers as a login item on the first launch only. Set CC_BUDDY_NO_LOGIN_ITEM=1
    /// (e.g. for a smoke test from a build directory) to skip.
    private func registerLoginItemOnce() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: Self.registeredKey) else { return }
        guard ProcessInfo.processInfo.environment["CC_BUDDY_NO_LOGIN_ITEM"] == nil else {
            log.info("login item registration skipped (CC_BUDDY_NO_LOGIN_ITEM)")
            return
        }
        do {
            try SMAppService.mainApp.register()
            defaults.set(true, forKey: Self.registeredKey)
            log.info("registered as login item")
        } catch {
            log.error("login item registration failed: \(error.localizedDescription, privacy: .public)")
        }
    }
}

private struct MenuContent: View {
    @State private var mirror = NotesMirror.shared
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled

    var body: some View {
        Text(status)
        Text(mirror.notesDir.path)
            .font(.caption)
        if let err = mirror.lastError {
            Text("Error: \(err)")
        }
        Divider()
        Button("Refresh now") { mirror.sync() }
        Button("Open notes folder") { NSWorkspace.shared.open(mirror.notesDir) }
        Toggle("Launch at login", isOn: $launchAtLogin)
            .onChange(of: launchAtLogin) { _, on in
                do {
                    if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                } catch {
                    launchAtLogin = SMAppService.mainApp.status == .enabled
                }
            }
        Divider()
        Button("Quit StackChan Notes") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    private var status: String {
        let when = mirror.lastSync.map { $0.formatted(date: .omitted, time: .shortened) } ?? "never"
        return "\(mirror.count) notes · synced \(when)"
    }
}
