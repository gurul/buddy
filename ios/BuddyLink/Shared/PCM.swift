import Foundation

enum PCM16 {
    /// PCM from WebSocket Data has no guaranteed Int16 alignment. Refuse truncated samples.
    static func decode(_ data: Data) -> [Int16]? {
        guard data.count.isMultiple(of: MemoryLayout<Int16>.size) else { return nil }
        return data.withUnsafeBytes { bytes in
            stride(from: 0, to: bytes.count, by: MemoryLayout<Int16>.size).map {
                Int16(littleEndian: bytes.loadUnaligned(fromByteOffset: $0, as: Int16.self))
            }
        }
    }
}
