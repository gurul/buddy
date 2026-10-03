import Foundation

// A large buffered BLE reply on the host. Wall time here is not radio latency.
let packets = (0..<20_000).map { Data([UInt8(truncatingIfNeeded: $0), UInt8(truncatingIfNeeded: $0 >> 8)]) }
func milliseconds(since start: ContinuousClock.Instant) -> Double {
    let duration = start.duration(to: .now).components
    return Double(duration.seconds) * 1000 + Double(duration.attoseconds) / 1e15
}
func previous() -> (Double, Int) {
    var queue = packets
    var checksum = 0
    let start = ContinuousClock.now
    while !queue.isEmpty { checksum += Int(queue.removeFirst()[0]) }
    return (milliseconds(since: start), checksum)
}
func current() -> (Double, Int) {
    var queue = PacketQueue<Data>()
    for packet in packets { queue.append(packet) }
    var checksum = 0
    let start = ContinuousClock.now
    while let packet = queue.popFirst() { checksum += Int(packet[0]) }
    return (milliseconds(since: start), checksum)
}
for trial in 1...3 {
    let before = previous(), after = current()
    guard before.1 == after.1, before.1 > 0 else { exit(1) }
    print(String(format: "queue trial=%d before_ms=%.3f after_ms=%.3f checksum=%d", trial, before.0, after.0, after.1))
}
