// The app's codec (Shared/ADPCM.swift) against tools/stick_link/vectors.txt, which the Python reference wrote.
import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String, line: Int = #line) {
    if !ok { failures += 1; FileHandle.standardError.write("line \(line): \(what)\n".data(using: .utf8)!) }
}

let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "tools/stick_link/vectors.txt"
let lines = try String(contentsOfFile: path, encoding: .utf8).split(separator: "\n", omittingEmptySubsequences: false)
    .map(String.init).filter { !$0.hasPrefix("#") && !$0.isEmpty }
func ints(_ s: String) -> [Int] { s.split(separator: " ").compactMap { Int($0) } }
func unhex(_ s: String) -> [UInt8] {
    var out = [UInt8](); var i = s.startIndex
    while i < s.endIndex { let j = s.index(i, offsetBy: 2); out.append(UInt8(s[i..<j], radix: 16)!); i = j }
    return out
}

var seen = 0
var at = 0
while at + 4 < lines.count {
    let head = lines[at].split(separator: " ")
    let name = String(head[0]), pred = Int(head[1])!, index = Int(head[2])!, count = Int(head[3])!
    let pcm = ints(lines[at + 1]).map { Int16($0) }, want = unhex(lines[at + 2])
    let dec = ints(lines[at + 3]).map { Int16($0) }, end = ints(lines[at + 4])
    check(pcm.count == count && dec.count == count && want.count == count / 2, "\(name): sizes")
    var s = ADPCMState(predictor: Int16(pred), index: UInt8(index))
    check(s.encode(pcm[...]) == want, "\(name): encode differs")
    check(Int(s.predictor) == end[0] && Int(s.index) == end[1], "\(name): end state")
    var d = ADPCMState(predictor: Int16(pred), index: UInt8(index))
    check(d.decode(want) == dec, "\(name): decode differs")
    seen += 1
    at += 5
}
check(seen == 8, "expected 8 vectors, saw \(seen)")

// Framed: each frame alone decodes to the same samples as the plain codec, seq wraps, bad frames are refused.
let pcm = (0..<1000).map { Int16(($0 * 997) % 20000 - 10000) }
var enc = FrameEncoder(payload: 180)
let frames = enc.feed(pcm) + enc.flush()
check(frames.allSatisfy { $0.count <= 180 } && frames.count == 3, "frame sizes")
var plain = ADPCMState()
var back = ADPCMState()
let expected = back.decode(plain.encode(pcm[...]))
var joined = [Int16](); var gaps = GapCounter()
for f in frames { let (seq, s) = try decodeAudioFrame(f); gaps.see(seq); joined += s }
check(joined == expected && gaps.lost == 0, "framed stream")
check((try? decodeAudioFrame([1, 0, 0, 0, 0, 0x12]))?.1.count == 2, "a good frame is accepted (control)")
for bad: [UInt8] in [[], [1, 0, 0, 0, 0], [2, 0x7b, 0x7d, 0, 0, 0], [1, 0, 0, 0, 89, 0]] {
    check((try? decodeAudioFrame(bad)) == nil, "accepted a malformed frame \(bad)")
}
var wrap = FrameEncoder(payload: 6)
let many = wrap.feed([Int16](repeating: 0, count: 600))
check(many[254...257].map { $0[1] } == [254, 255, 0, 1], "sequence wraps")
var g = GapCounter(); for s: UInt8 in [250, 251, 254, 1] { g.see(s) }
check(g.lost == 4, "gaps across the wrap")

if failures > 0 { print("\(failures) check(s) failed"); exit(1) }
print("swift codec: \(seen) vectors and all checks passed")
