// The phone's end of the Bluetooth link to the stick (firmware/buddy_stick). Shared with the Mac bench probe
// (BuddyProbe), so the bench exercises the code the phone runs.
//
// The stick is a GATT peripheral with one service and two characteristics: UP (the stick's notifications: audio
// frames and JSON events) and DOWN (writes without response: the reply's audio frames and JSON messages). Both
// need an encrypted, authenticated link: the first read of UP makes iOS ask for the six-digit code on the
// stick's screen, and the bond is kept after that.
import CoreBluetooth
import Foundation

enum StickUUID {
    static var service: CBUUID { CBUUID(string: "6a5ad52d-ec26-4b36-9b2d-0e4ef9d2f2bc") }
    static var up: CBUUID { CBUUID(string: "19a1cbb3-15c5-4596-bcae-65985cfe7226") }
    static var down: CBUUID { CBUUID(string: "dfc25597-3956-43b0-8c38-a7e5dc928505") }
}

/// What the stick says, decoded.
enum StickEvent {
    case ready(firmware: String, battery: Int)   // paired and subscribed: its hello
    case talk
    case audio([Int16])
    case done
    case ping
    case wake                                    // picked up: a press is likely coming
    case stat([String: Any])
    case disconnected
}

@MainActor
final class StickLink: NSObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    // The central runs on the main queue, so every delegate call arrives on the main actor.
    static let restoreID = "buddylink.stick"
    private static let knownKey = "stickPeripheral"

    var onEvent: ((StickEvent) -> Void)?
    var onStatus: ((String) -> Void)?
    private(set) var connected = false
    private(set) var framesLost = 0

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var up: CBCharacteristic?
    private var down: CBCharacteristic?
    private var outbox = PacketQueue<Data>()      // writes waiting for the link to take them
    private var gaps = GapCounter()
    private var encoder = FrameEncoder(payload: 20)

    init(restore: Bool) {
        super.init()
        var options: [String: Any] = [CBCentralManagerOptionShowPowerAlertKey: true]
        #if os(iOS)
        if restore { options[CBCentralManagerOptionRestoreIdentifierKey] = Self.restoreID }
        #endif
        central = CBCentralManager(delegate: self, queue: .main, options: options)
    }

    // ---- sending ----------------------------------------------------------------------------------------

    /// A JSON message to the stick, cut to fit one value.
    func send(_ message: [String: Any]) {
        guard var data = try? JSONSerialization.data(withJSONObject: message) else { return }
        let room = writeRoom() - 1
        if data.count > room, var text = message["text"] as? String ?? message["note"] as? String {
            // Too long for one value: shorten the words, never the structure.
            let key = message["text"] != nil ? "text" : "note"
            var shorter = message
            while data.count > room, !text.isEmpty {
                text = String(text.dropLast(max(1, (data.count - room) / 2)))
                shorter[key] = text + "..."
                data = (try? JSONSerialization.data(withJSONObject: shorter)) ?? Data()
            }
        }
        guard data.count <= room else { return }
        enqueue(Data([Wire.kindJSON]) + data)
    }

    /// Reply audio at 24 kHz, framed and queued for the stick.
    func sendAudio(_ pcm: [Int16]) {
        encoder.payload = writeRoom()
        for frame in encoder.feed(pcm) { enqueue(Data(frame)) }
    }

    func flushAudio() {
        for frame in encoder.flush() { enqueue(Data(frame)) }
    }

    /// Drop reply audio not yet handed to the radio (buddy was interrupted).
    func dropAudio() {
        outbox.removeAll { $0.first == Wire.kindAudio }
        encoder.reset()
    }

    var queuedAudioFrames: Int { outbox.count { $0.first == Wire.kindAudio } }

    private func writeRoom() -> Int {
        guard let peripheral else { return 20 }
        return min(244, peripheral.maximumWriteValueLength(for: .withoutResponse))
    }

    private func enqueue(_ value: Data) {
        outbox.append(value)
        pump()
    }

    private func pump() {
        guard let peripheral, let down else { return }
        while !outbox.isEmpty, peripheral.canSendWriteWithoutResponse {
            guard let value = outbox.popFirst() else { break }
            peripheral.writeValue(value, for: down, type: .withoutResponse)
        }
    }

    // ---- finding the stick ------------------------------------------------------------------------------

    private func status(_ s: String) { onStatus?(s) }

    private func findStick() {
        guard central.state == .poweredOn, peripheral == nil || peripheral?.state == .disconnected else { return }
        if let id = UserDefaults.standard.string(forKey: Self.knownKey).flatMap(UUID.init(uuidString:)),
           let known = central.retrievePeripherals(withIdentifiers: [id]).first {
            attach(known)
            return
        }
        if let already = central.retrieveConnectedPeripherals(withServices: [StickUUID.service]).first {
            attach(already)
            return
        }
        status("Looking for the stick…")
        central.scanForPeripherals(withServices: [StickUUID.service])
    }

    private func attach(_ p: CBPeripheral) {
        central.stopScan()
        peripheral = p
        p.delegate = self
        status("Connecting to the stick…")
        central.connect(p)   // pending until it is in range: iOS keeps trying, even in the background
    }

    func forget() {
        UserDefaults.standard.removeObject(forKey: Self.knownKey)
        if let peripheral { central.cancelPeripheralConnection(peripheral) }
        peripheral = nil
        findStick()
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn: findStick()
        case .unauthorized: status("Bluetooth is off for Buddy Link in Settings.")
        case .poweredOff: status("Bluetooth is off.")
        default: break
        }
    }

    #if os(iOS)
    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        if let p = (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral])?.first {
            peripheral = p
            p.delegate = self
        }
    }
    #endif

    func centralManager(_ central: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        attach(p)
    }

    func centralManager(_ central: CBCentralManager, didConnect p: CBPeripheral) {
        UserDefaults.standard.set(p.identifier.uuidString, forKey: Self.knownKey)
        status("Pairing with the stick…")
        p.discoverServices([StickUUID.service])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        reconnect()
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral p: CBPeripheral,
                        error: Error?) {
        reconnect()
    }

    private func reconnect() {
        let was = connected
        connected = false
        up = nil
        down = nil
        outbox.removeAll()
        if was { onEvent?(.disconnected) }
        status("The stick is out of reach. It reconnects by itself.")
        if let peripheral { central.connect(peripheral) } else { findStick() }
    }

    // ---- the service ------------------------------------------------------------------------------------

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard let svc = p.services?.first(where: { $0.uuid == StickUUID.service }) else { return }
        p.discoverCharacteristics([StickUUID.up, StickUUID.down], for: svc)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        up = service.characteristics?.first { $0.uuid == StickUUID.up }
        down = service.characteristics?.first { $0.uuid == StickUUID.down }
        guard let up else { return }
        p.readValue(for: up)                  // an authenticated read: iOS asks for the stick's code here
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor c: CBCharacteristic, error: Error?) {
        let value = c.value.map { [UInt8]($0) } ?? []
        if let error {
            status("Pairing didn't finish (\(error.localizedDescription)). Try again, with the code on the stick.")
            return
        }
        if !c.isNotifying {                  // the pairing read came back: subscribe, then say hi
            p.setNotifyValue(true, for: c)
            return
        }
        handle(value)
    }

    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor c: CBCharacteristic, error: Error?) {
        guard c.isNotifying, error == nil else { return }
        connected = true
        gaps.reset()
        encoder = FrameEncoder(payload: writeRoom())
        send(["t": "hi"])
    }

    func peripheralIsReady(toSendWriteWithoutResponse p: CBPeripheral) {
        pump()
    }

    private func handle(_ value: [UInt8]) {
        guard let kind = value.first else { return }
        if kind == Wire.kindAudio {
            guard let (seq, pcm) = try? decodeAudioFrame(value) else { return }
            gaps.see(seq)
            framesLost = gaps.lost
            onEvent?(.audio(pcm))
            return
        }
        guard kind == Wire.kindJSON,
              let obj = try? JSONSerialization.jsonObject(with: Data(value.dropFirst())) as? [String: Any],
              let t = obj["t"] as? String else { return }
        switch t {
        case "hello":
            status("Connected to the stick.")
            onEvent?(.ready(firmware: obj["fw"] as? String ?? "?", battery: obj["batt"] as? Int ?? -1))
        case "talk":
            gaps.reset()
            onEvent?(.talk)
        case "done": onEvent?(.done)
        case "ping": onEvent?(.ping)
        case "wake": onEvent?(.wake)
        case "stat": onEvent?(.stat(obj))
        default: break
        }
    }
}
