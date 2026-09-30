// The stick link's wire: IMA ADPCM audio frames and JSON frames, one Bluetooth value each.
//
// The reference is tools/stick_link/adpcm.py; this copy and the stick's (firmware/buddy_stick/link_codec.h) must
// match it byte for byte, which Tests/run.sh checks against tools/stick_link/vectors.txt.
//
// A frame:  [kind] then for audio [seq][predictor Int16 LE][step index] [codes, low nibble first]
//           or for JSON the UTF-8 text.

enum Wire {
    static let kindAudio: UInt8 = 0x01
    static let kindJSON: UInt8 = 0x02
    static let header = 5
    static let sampleRate = 24_000

    static func samplesPerFrame(payload: Int) -> Int { payload > header ? 2 * (payload - header) : 0 }
}

struct ADPCMState: Equatable {
    var predictor: Int16 = 0
    var index: UInt8 = 0

    private static let steps: [Int32] = [
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
        107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
        5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
        27086, 29794, 32767,
    ]
    private static let indexAdjust: [Int] = [-1, -1, -1, -1, 2, 4, 6, 8]

    mutating func step(_ code: UInt8) {
        let st = Self.steps[Int(index)]
        var diff = st >> 3
        if code & 4 != 0 { diff += st }
        if code & 2 != 0 { diff += st >> 1 }
        if code & 1 != 0 { diff += st >> 2 }
        let p = code & 8 != 0 ? Int32(predictor) - diff : Int32(predictor) + diff
        predictor = Int16(max(-32768, min(32767, p)))
        index = UInt8(max(0, min(88, Int(index) + Self.indexAdjust[Int(code & 7)])))
    }

    mutating func encodeSample(_ sample: Int16) -> UInt8 {
        let st = Self.steps[Int(index)]
        var diff = Int32(sample) - Int32(predictor)
        var code: UInt8 = 0
        if diff < 0 { code = 8; diff = -diff }
        if diff >= st { code |= 4; diff -= st }
        if diff >= st >> 1 { code |= 2; diff -= st >> 1 }
        if diff >= st >> 2 { code |= 1 }
        step(code)
        return code
    }

    /// An even number of samples into half as many code bytes.
    mutating func encode(_ pcm: ArraySlice<Int16>) -> [UInt8] {
        var out = [UInt8]()
        out.reserveCapacity(pcm.count / 2)
        var i = pcm.startIndex
        while i + 1 < pcm.endIndex {
            let lo = encodeSample(pcm[i])
            let hi = encodeSample(pcm[i + 1])
            out.append(lo | (hi << 4))
            i += 2
        }
        return out
    }

    mutating func decode(_ codes: some Collection<UInt8>) -> [Int16] {
        var out = [Int16]()
        out.reserveCapacity(codes.count * 2)
        for byte in codes {
            step(byte & 0x0F)
            out.append(predictor)
            step(byte >> 4)
            out.append(predictor)
        }
        return out
    }
}

/// PCM in, audio frames out, `payload` bytes each at most.
struct FrameEncoder {
    private(set) var state = ADPCMState()
    private(set) var seq: UInt8 = 0
    private var pending: [Int16] = []
    var payload: Int

    init(payload: Int) { self.payload = payload }

    mutating func feed(_ pcm: [Int16]) -> [[UInt8]] {
        pending.append(contentsOf: pcm)
        let spf = Wire.samplesPerFrame(payload: payload)
        guard spf > 0 else { return [] }
        var frames = [[UInt8]]()
        var start = 0
        while pending.count - start >= spf {
            frames.append(frame(pending[start..<(start + spf)]))
            start += spf
        }
        pending.removeFirst(start)
        return frames
    }

    /// What is left, padded with its last sample to an even count.
    mutating func flush() -> [[UInt8]] {
        guard let last = pending.last else { return [] }
        if pending.count % 2 == 1 { pending.append(last) }
        let out = frame(pending[...])
        pending.removeAll()
        return [out]
    }

    mutating func reset() {
        state = ADPCMState()
        pending.removeAll()
    }

    private mutating func frame(_ chunk: ArraySlice<Int16>) -> [UInt8] {
        let p = UInt16(bitPattern: state.predictor)
        var out: [UInt8] = [Wire.kindAudio, seq, UInt8(p & 0xFF), UInt8(p >> 8), state.index]
        out += state.encode(chunk)
        seq &+= 1
        return out
    }
}

enum FrameError: Error { case malformed }

/// One audio frame to (sequence, samples).
func decodeAudioFrame(_ frame: [UInt8]) throws -> (UInt8, [Int16]) {
    guard frame.count >= Wire.header + 1, frame[0] == Wire.kindAudio, frame[4] <= 88 else { throw FrameError.malformed }
    var s = ADPCMState(predictor: Int16(bitPattern: UInt16(frame[2]) | UInt16(frame[3]) << 8), index: frame[4])
    return (frame[1], s.decode(frame[Wire.header...]))
}

/// Counts frames lost on the way, from the sequence numbers (mod 256).
struct GapCounter {
    private var last: UInt8?
    private(set) var lost = 0
    mutating func see(_ seq: UInt8) {
        if let last { lost += Int(seq &- last &- 1) }
        last = seq
    }
    mutating func reset() { last = nil; lost = 0 }
}
