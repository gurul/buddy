"""The reference codec: it round-trips speech well enough, frames survive loss, and vectors.txt is current."""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import adpcm  # noqa: E402


def snr_db(a: list[int], b: list[int]) -> float:
    sig = sum(x * x for x in a)
    err = sum((x - y) ** 2 for x, y in zip(a, b)) or 1
    return 10 * math.log10(sig / err)


def main() -> None:
    by_name = {name: (pcm, p, i) for name, pcm, p, i in adpcm.vector_inputs()}

    # Speech-like audio comes back close: IMA ADPCM gives roughly 20+ dB on voiced sound.
    vowel = by_name["vowel"][0]
    codes, _, _ = adpcm.encode(vowel)
    back, _, _ = adpcm.decode(codes)
    assert len(codes) == len(vowel) // 2
    assert snr_db(vowel[48:], back[48:]) > 18, snr_db(vowel[48:], back[48:])   # after the step settles

    # Silence stays silent, and nothing overflows at full scale.
    silent, _, _ = adpcm.decode(adpcm.encode([0] * 64)[0])
    assert max(abs(x) for x in silent) <= 8
    square = by_name["full_scale_square"][0]
    out, _, _ = adpcm.decode(adpcm.encode(square)[0])
    assert all(-32768 <= x <= 32767 for x in out)

    # Frames: sized to the payload, numbered, each decodable alone, and the stream equals the plain codec's.
    long = vowel * 4 + vowel[:7]                                    # odd: the last frame is padded
    back, _, _ = adpcm.decode(adpcm.encode(long + long[-1:])[0])
    enc = adpcm.Encoder(payload=180)
    frames = enc.feed(long) + enc.flush()
    assert all(len(f) <= 180 for f in frames) and len(frames) == math.ceil(len(long) / enc.samples_per_frame)
    assert [f[1] for f in frames] == list(range(len(frames)))
    joined = [s for f in frames for s in adpcm.decode_frame(f)[1]]
    assert joined == back, "framed stream differs from the plain codec"
    # Losing a frame costs only that frame: the next one still decodes to the same samples.
    assert adpcm.decode_frame(frames[2])[1] == back[2 * enc.samples_per_frame: 3 * enc.samples_per_frame]

    # Sequence numbers wrap at 256.
    enc = adpcm.Encoder(payload=6)
    frames = enc.feed([0] * 2 * 300)
    assert [f[1] for f in frames[254:258]] == [254, 255, 0, 1]

    # Malformed frames are refused (positive control: a good one is accepted above).
    for bad in (b"", b"\x01\x00\x00\x00", b"\x02{}", b"\x01\x00\x00\x00\x59\x00"):
        try:
            adpcm.decode_frame(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")

    # vectors.txt is what this code writes now.
    with tempfile.TemporaryDirectory() as d:
        fresh = adpcm.write_vectors(Path(d) / "v.txt")
    assert adpcm.VECTORS.read_text() == fresh, "vectors.txt is stale: python3 adpcm.py --write-vectors"
    print("reference codec: all checks passed")


if __name__ == "__main__":
    main()
