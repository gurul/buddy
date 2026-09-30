"""The stick link's audio wire: IMA ADPCM in self-contained Bluetooth frames. The reference for the other two.

The stick (firmware/buddy_stick/link_codec.h) and the iPhone app (ios/BuddyLink/BuddyLink/ADPCM.swift) each
carry their own copy of this codec; all three must agree byte for byte, which ``test_adpcm.py`` and the two host
tests check against ``vectors.txt`` (written by this file, ``python3 adpcm.py --write-vectors``).

Why ADPCM: the call is 24 kHz 16-bit mono, 48 KB/s, which is more than a phone keeps up over Bluetooth LE in the
background. IMA ADPCM is 4 bits a sample, 12 KB/s, costs a few instructions a sample on either end, and is plenty
for speech that is transcribed or played on a 1 W speaker.

A frame on the link is one GATT value:

    byte 0      kind: 0x01 audio, 0x02 a JSON message (UTF-8, the rest of the value)
    audio:
    byte 1      sequence number, 0-255, wrapping (the receiver counts gaps; it does not reorder)
    bytes 2-3   the predictor before this frame, int16 little-endian
    byte 4      the step index before this frame, 0-88
    bytes 5-    ADPCM codes, two samples a byte, the earlier sample in the low nibble

Each audio frame carries the coder's state, so a lost frame costs its own 15 ms and nothing after it.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

KIND_AUDIO = 0x01
KIND_JSON = 0x02
HEADER = 5                                  # kind, seq, predictor (2), index

STEPS = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
)
INDEX_ADJUST = (-1, -1, -1, -1, 2, 4, 6, 8)


def _clamp16(v: int) -> int:
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def _step(pred: int, index: int, code: int) -> tuple[int, int]:
    """The decoder's update, which the encoder runs too so both stay in lockstep."""
    step = STEPS[index]
    diff = step >> 3
    if code & 4:
        diff += step
    if code & 2:
        diff += step >> 1
    if code & 1:
        diff += step >> 2
    pred = _clamp16(pred - diff if code & 8 else pred + diff)
    index = min(88, max(0, index + INDEX_ADJUST[code & 7]))
    return pred, index


def encode(pcm: list[int], pred: int = 0, index: int = 0) -> tuple[bytes, int, int]:
    """PCM samples (an even count) to codes, from the given state. Returns the codes and the state after."""
    if len(pcm) % 2:
        raise ValueError("an even number of samples")
    out = bytearray()
    lo = 0
    for i, sample in enumerate(pcm):
        step = STEPS[index]
        diff = sample - pred
        code = 0
        if diff < 0:
            code, diff = 8, -diff
        if diff >= step:
            code |= 4
            diff -= step
        if diff >= step >> 1:
            code |= 2
            diff -= step >> 1
        if diff >= step >> 2:
            code |= 1
        pred, index = _step(pred, index, code)
        if i % 2 == 0:
            lo = code
        else:
            out.append(lo | (code << 4))
    return bytes(out), pred, index


def decode(codes: bytes, pred: int = 0, index: int = 0) -> tuple[list[int], int, int]:
    out: list[int] = []
    for byte in codes:
        for code in (byte & 0x0F, byte >> 4):
            pred, index = _step(pred, index, code)
            out.append(pred)
    return out, pred, index


class Encoder:
    """PCM in, audio frames out, ``payload`` bytes each at most (the link's value size)."""

    def __init__(self, payload: int) -> None:
        if payload < HEADER + 1:
            raise ValueError("payload too small")
        self.samples_per_frame = 2 * (payload - HEADER)
        self.pred = self.index = self.seq = 0
        self.pending: list[int] = []

    def feed(self, pcm: list[int]) -> list[bytes]:
        self.pending.extend(pcm)
        frames = []
        while len(self.pending) >= self.samples_per_frame:
            chunk, self.pending = self.pending[: self.samples_per_frame], self.pending[self.samples_per_frame:]
            frames.append(self._frame(chunk))
        return frames

    def flush(self) -> list[bytes]:
        """What is left, padded with the last sample to an even count."""
        if not self.pending:
            return []
        chunk, self.pending = self.pending, []
        if len(chunk) % 2:
            chunk.append(chunk[-1])
        return [self._frame(chunk)]

    def _frame(self, chunk: list[int]) -> bytes:
        head = bytes([KIND_AUDIO, self.seq]) + (self.pred & 0xFFFF).to_bytes(2, "little") + bytes([self.index])
        codes, self.pred, self.index = encode(chunk, self.pred, self.index)
        self.seq = (self.seq + 1) & 0xFF
        return head + codes


def decode_frame(frame: bytes) -> tuple[int, list[int]]:
    """An audio frame to (sequence, samples). Raises ValueError on anything malformed."""
    if len(frame) < HEADER + 1 or frame[0] != KIND_AUDIO:
        raise ValueError("not an audio frame")
    index = frame[4]
    if index > 88:
        raise ValueError("step index out of range")
    pred = int.from_bytes(frame[2:4], "little", signed=True)
    samples, _, _ = decode(frame[HEADER:], pred, index)
    return frame[1], samples


# ---- vectors ------------------------------------------------------------------------------------------------

def _lcg(seed: int):
    while True:
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        yield seed


def vector_inputs() -> list[tuple[str, list[int], int, int]]:
    """(name, pcm, predictor, index): speech-like and edge-case inputs, all deterministic."""
    n = 480
    rnd = _lcg(7)
    noise = [((next(rnd) >> 8) % 20001) - 10000 for _ in range(n)]
    chirp = [int(9000 * math.sin(2 * math.pi * (200 + 3000 * i / n) * i / 24000)) for i in range(n)]
    vowel = [int(6000 * math.sin(2 * math.pi * 140 * i / 24000) + 2500 * math.sin(2 * math.pi * 700 * i / 24000)
                 + 1200 * math.sin(2 * math.pi * 2200 * i / 24000)) for i in range(n)]
    return [
        ("silence", [0] * 64, 0, 0),
        ("tone_1k", [int(12000 * math.sin(2 * math.pi * 1000 * i / 24000)) for i in range(n)], 0, 0),
        ("chirp", chirp, 0, 0),
        ("vowel", vowel, 0, 0),
        ("noise", noise, 0, 0),
        ("full_scale_square", [32767 if (i // 12) % 2 else -32768 for i in range(n)], 0, 0),
        ("mid_state_start", vowel[:200], -1234, 40),
        ("max_index_start", chirp[:100], 30000, 88),
    ]


def write_vectors(path: Path) -> str:
    """One block per vector, whitespace-separated so C++ and Swift parse it with a few lines each:
    ``name pred index count`` / the PCM / the codes in hex / the decoded samples / ``end_pred end_index``."""
    lines = ["# written by tools/stick_link/adpcm.py --write-vectors; do not edit"]
    for name, pcm, pred, index in vector_inputs():
        codes, end_pred, end_index = encode(pcm, pred, index)
        decoded, dp, di = decode(codes, pred, index)
        assert (dp, di) == (end_pred, end_index)
        lines += [f"{name} {pred} {index} {len(pcm)}", " ".join(map(str, pcm)), codes.hex(),
                  " ".join(map(str, decoded)), f"{end_pred} {end_index}"]
    text = "\n".join(lines) + "\n"
    path.write_text(text)
    return text


VECTORS = Path(__file__).with_name("vectors.txt")

if __name__ == "__main__":
    if sys.argv[1:] == ["--write-vectors"]:
        write_vectors(VECTORS)
        print(f"wrote {VECTORS}")
    else:
        sys.exit("usage: adpcm.py --write-vectors")
