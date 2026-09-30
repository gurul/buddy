// BuddyProbe: the Mac standing in for the phone, on the bench (docs/stick-link.md, GATES.md G2.2 / G2.3).
// It runs the app's own StickLink. Modes, from the first argument:
//   tone         send one second of 440 Hz and check the stick played it all   → STICK_PROBE_OK
//   listen       wait for one press, report its length and loudness            → STICK_PRESS_OK
//   relay <url> <token>   be the phone: relay presses to buddy at <url>
// Output goes to stdout and to the file named by BUDDY_PROBE_OUT (it runs as an app, launched by `open`).
import AppKit
import CoreBluetooth
import Foundation

let args = Array(CommandLine.arguments.dropFirst())
let mode = args.first ?? "tone"
let outPath = ProcessInfo.processInfo.environment["BUDDY_PROBE_OUT"] ?? "/tmp/buddy-probe.txt"
FileManager.default.createFile(atPath: outPath, contents: nil)
let out = FileHandle(forWritingAtPath: outPath)!

@MainActor func say(_ s: String) {
    print(s)
    out.write((s + "\n").data(using: .utf8)!)
}

@MainActor func finish(_ code: Int32) -> Never {
    out.synchronizeFile()
    exit(code)
}

@MainActor final class Probe {
    let link = StickLink(restore: false)
    var relay: Relay?
    var pressSamples = 0
    var pressPeak = 0

    func start() {
        link.onStatus = { say("status: \($0)") }
        switch mode {
        case "relay":
            guard args.count >= 3, let url = URL(string: args[1]) else { say("usage: relay <url> <token>"); finish(2) }
            let r = Relay(restore: false)
            r.pairing = Pairing(base: url, token: args[2])
            relay = r
            r.stick.onStatus = { say("stick: \($0)") }
            say("relaying presses to \(url)")
            return
        default: break
        }
        link.onEvent = { [weak self] e in self?.event(e) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) {
            say("STICK_PROBE_TIMEOUT: no result in 90 s")
            finish(1)
        }
    }

    func event(_ e: StickEvent) {
        switch e {
        case .ready(let fw, let batt):
            say("hello: firmware \(fw), battery \(batt)%")
            if mode == "tone" { sendTone() }
            if mode == "listen" { say("hold the stick's front button, say something, let go") }
        case .talk:
            pressSamples = 0
            pressPeak = 0
        case .audio(let pcm):
            pressSamples += pcm.count
            pressPeak = max(pressPeak, pcm.map { abs(Int($0)) }.max() ?? 0)
        case .done:
            let dbfs = pressPeak > 0 ? 20 * log10(Double(pressPeak) / 32767) : -120
            say(String(format: "press: %.2f s, peak %d (%.1f dBFS), frames lost %d",
                       Double(pressSamples) / 24000, pressPeak, dbfs, link.framesLost))
            if mode == "listen" {
                say(pressSamples > 24000 / 2 && dbfs > -30 ? "STICK_PRESS_OK" : "STICK_PRESS_QUIET")
                finish(0)
            }
        case .stat(let s):
            let played = s["played"] as? Int ?? 0
            say("stat: \(s)")
            if mode == "tone" {
                // 1 s sent; the stick plays in 50 ms blocks, so all of it, within one block of padding
                say(played >= 24000 && played <= 24000 + 1200 && (s["lost"] as? Int ?? 1) == 0
                    ? "STICK_PROBE_OK" : "STICK_PROBE_FAIL")
                finish(0)
            }
        default: break
        }
    }

    func sendTone() {
        let pcm = (0..<24000).map { Int16(8000 * sin(2 * Double.pi * 440 * Double($0) / 24000)) }
        link.send(["t": "state", "s": "speaking"])
        for i in stride(from: 0, to: pcm.count, by: 2400) { link.sendAudio(Array(pcm[i..<min(i + 2400, pcm.count)])) }
        link.flushAudio()
        say("sent 1 s of tone")
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { self.link.send(["t": "stat"]) }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let probe = MainActor.assumeIsolated { Probe() }
MainActor.assumeIsolated { probe.start() }
app.run()
