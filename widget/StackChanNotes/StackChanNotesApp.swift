// StackChan Notes helper — a Dock-less (LSUIElement) menu-bar app that mirrors
// the bridge daemon's diary files into the App Group container the widget
// reads, and hosts the diary window the widget opens (stackchan://diary).
// It is deliberately NOT sandboxed so it can read ~/.config/cc-buddy-bridge/notes.

import AppKit
import ServiceManagement
import SwiftUI
import WebKit
import os

@main
struct StackChanNotesApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    /// Window scenes can be built before the delegate runs, so register the brand
    /// fonts here too. Registration runs once however often it is called.
    init() { BrandFonts.register() }

    var body: some Scene {
        MenuBarExtra("StackChan Notes", systemImage: "note.text") {
            MenuContent()
        }
        // The diary window. `handlesExternalEvents` routes stackchan://diary here
        // (the URL scheme is declared in Info.plist through project.yml).
        Window("buddy's diary", id: "diary") {
            DiaryView()
        }
        .windowResizability(.contentMinSize)
        .defaultSize(width: 720, height: 560)
        .handlesExternalEvents(matching: ["diary"])
        Window("Buddy Learning", id: "learning") {
            LearningDashboardView()
        }
        .defaultSize(width: 1200, height: 820)
        .handlesExternalEvents(matching: ["learning"])
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let log = Logger(subsystem: "com.github.cc-buddy-bridge.StackChanNotes", category: "app")
    private static let registeredKey = "loginItemRegistered"

    func applicationDidFinishLaunching(_ notification: Notification) {
        BrandFonts.register()
        registerLoginItemOnce()
        NotesMirror.shared.start()
    }

    /// A URL open must bring the (LSUIElement) app forward so the window shows.
    func application(_ application: NSApplication, open urls: [URL]) {
        if urls.contains(where: { $0.scheme == "stackchan" }) {
            NSApp.activate(ignoringOtherApps: true)
            log.info("opening the diary from a URL")
        }
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
    @Environment(\.openWindow) private var openWindow
    @State private var mirror = NotesMirror.shared
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    /// The daemon's answer to `mic status`; nil until asked, or when it is not reachable.
    @State private var mic: MicState?
    @State private var micAsked = false
    @State private var micOn = true

    var body: some View {
        Text(status)
        if let mood = mirror.snapshot.mood {
            Text("feeling \(MoodColor.emoji(for: mood.label)) \(mood.label)")
        }
        Text(mirror.notesDir.path)
            .font(.caption)
        if let err = mirror.lastError {
            Text("Error: \(err)")
        }
        Divider()
        // The owner's microphone switch: the same thing as `cc-buddy-bridge mic on|off`.
        // The mic itself opens only while the robot is connected (or CC_BUDDY_MIC_ALWAYS=1);
        // this switch closes it for good until it is turned back on, across restarts.
        Text(micLine)
        Toggle("Microphone", isOn: $micOn)
            .disabled(mic == nil || !(mic?.available ?? false))
            .onChange(of: micOn) { _, on in
                guard let current = mic, current.switchOn != on else { return }
                Task { await flipMic(on) }
            }
        .task { await refreshMic() }
        Divider()
        Button("Open diary") {
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: "diary")
        }
        .keyboardShortcut("d")
        Button("Open learning dashboard") {
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: "learning")
        }
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
        return "\(mirror.count) thoughts · synced \(when)"
    }

    private var micLine: String {
        if let mic { return mic.line }
        return micAsked ? "Microphone: daemon not reachable" : "Microphone: asking the daemon…"
    }

    private func refreshMic() async {
        let state = await BuddyDaemon.micStatus()
        mic = state
        micAsked = true
        if let state { micOn = state.switchOn }
    }

    private func flipMic(_ on: Bool) async {
        let state = await BuddyDaemon.setMic(on: on)
        mic = state
        micAsked = true
        // The daemon is the truth: if it could not be reached, show the switch where it really is.
        if let state { micOn = state.switchOn } else if let current = mic { micOn = current.switchOn }
    }
}


/// The same local web workspace as Windows, embedded in the widget's helper app.
struct LearningDashboardView: View {
    @State private var retry = UUID()
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                InkHeading("Buddy Learning", size: 20)
                Spacer()
                Text("Start the bridge daemon if the workspace is offline.")
                    .font(BrandFont.body(12))
                    .foregroundStyle(Color.brandInkSoft)
                Button("Reload") { retry = UUID() }
                    .buttonStyle(ChunkyButtonStyle())
            }
            .padding(12)
            .background(Color.brandPaper)
            .overlay(alignment: .bottom) { Rectangle().fill(Color.brandInk).frame(height: 2) }
            .environment(\.colorScheme, .light)
            LearningWebView().id(retry)
        }
        .frame(minWidth: 850, minHeight: 600)
    }
}

struct LearningWebView: NSViewRepresentable {
    func makeNSView(context: Context) -> WKWebView {
        let view = WKWebView()
        let port = Int(ProcessInfo.processInfo.environment["CC_BUDDY_LEARNING_PORT"] ?? "48766") ?? 48766
        view.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}
}
