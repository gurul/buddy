// Buddy Link: the iPhone half of the stick link (docs/stick-link.md). It keeps the stick connected over
// Bluetooth, in the background too, and carries each press to buddy as a call.
import SwiftUI

@main
struct BuddyLinkApp: App {
    @StateObject private var relay = Relay()

    var body: some Scene {
        WindowGroup {
            ContentView(relay: relay)
                .onOpenURL { url in _ = relay.pair(with: url) }
        }
    }
}

struct ContentView: View {
    @ObservedObject var relay: Relay
    @State private var pasted = ""
    @State private var pasteFailed = false

    var body: some View {
        NavigationStack {
            List {
                Section("Stick") {
                    row("Link", relay.stickStatus)
                    if !relay.firmware.isEmpty { row("Firmware", relay.firmware) }
                    if relay.battery >= 0 { row("Battery", "\(relay.battery)%") }
                    Button("Forget this stick", role: .destructive) { relay.stick.forget() }
                }
                Section("buddy") {
                    row("Paired with", relay.pairing?.base.host ?? "not paired")
                    row("Call", relay.callStatus)
                    if !relay.lastHeard.isEmpty { row("Heard", relay.lastHeard) }
                }
                Section {
                    TextField("buddylink://pair?…", text: $pasted)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Pair from a pasted link") {
                        if let url = URL(string: pasted.trimmingCharacters(in: .whitespaces)) {
                            pasteFailed = !relay.pair(with: url)
                            if !pasteFailed { pasted = "" }
                        } else {
                            pasteFailed = true
                        }
                    }
                    if pasteFailed { Text("That isn't a Buddy Link pairing link.").foregroundStyle(.red) }
                } header: {
                    Text("Pairing")
                } footer: {
                    Text("Send /stick to buddy in Telegram and tap Update Buddy Link. Do the same after buddy restarts: the pinned message's button has the new address.")
                }
            }
            .navigationTitle("Buddy Link")
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        LabeledContent(label) { Text(value).multilineTextAlignment(.trailing) }
    }
}
