"""Ears: the "hey buddy" wake word on the Mac's microphone.

The daemon keeps one microphone stream open (24 kHz mono int16, 100 ms
blocks) and runs every block through a sherpa-onnx keyword spotter — a
3.3M-parameter streaming zipformer that needs no training for a new
phrase: the phrase is written as BPE tokens into a keywords file and the
spotter fires when the decoded lattice contains it. Bench 2026-09-06 on an
M-series Mac: a 1.9 s clip decodes in 0.02 s, so this costs ~1% of a core.

Two consumers share the stream. The spotter listens all the time; while a
voice session is open (voice_agent.py) the same PCM is forwarded to the
realtime model through `subscribe()`, and the spotter is muted so buddy
does not wake itself on its own replies.

Everything that touches hardware or the model is behind two small seams —
`WakeSpotter` (the model) and the sounddevice callback — so the gating
logic (`WakeGate`) and the stream plumbing are unit-testable without a mic.

Why sherpa-onnx and not the usual suspects (research 2026-09-06):
openWakeWord's ONNX path returns ~0 scores on Apple Silicon (issue #336)
and is 2.5 years stale; Porcupine dropped its free tier on 2026-06-30;
livekit-wakeword needs a GPU training run per phrase (a later upgrade —
see docs/stackchan/voice.md).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_WAKE_WORD = "hey buddy"
DEFAULT_MODEL_DIR = "~/.config/cc-buddy-bridge/models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
             "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2")
SAMPLE_RATE = 24000          # what the realtime model wants; the spotter resamples to 16 k itself
BLOCK_SECS = 0.1
# A keyword fires at most once per COOLDOWN after a hit; a spoken "hey buddy"
# can otherwise trigger twice as the lattice settles.
DEFAULT_COOLDOWN_SECS = 2.0
# Boosting score / threshold from the sherpa-onnx KWS docs; 0.25 fired on a
# synthesized clip and stayed quiet on a control sentence (bench).
DEFAULT_BOOST = 2.0
DEFAULT_THRESHOLD = 0.25
# RMS of int16 samples below this for SILENCE_WARN_SECS → the mic is dead
# (no TCC grant, wrong device). Ambient on the bench read ~90.
SILENCE_RMS = 3.0
SILENCE_WARN_SECS = 10.0


@dataclass(frozen=True)
class EarsConfig:
    enabled: bool = True
    wake_word: str = DEFAULT_WAKE_WORD
    model_dir: Path = Path(DEFAULT_MODEL_DIR).expanduser()
    threshold: float = DEFAULT_THRESHOLD
    boost: float = DEFAULT_BOOST
    cooldown_secs: float = DEFAULT_COOLDOWN_SECS
    device: Optional[str] = None      # sounddevice input name substring; None = default


def configured(environ: Any = None) -> EarsConfig:
    env = os.environ if environ is None else environ
    raw = (env.get("CC_BUDDY_VOICE") or "").strip().lower()
    enabled = raw not in ("0", "false", "no", "off")
    word = (env.get("CC_BUDDY_WAKE_WORD") or DEFAULT_WAKE_WORD).strip() or DEFAULT_WAKE_WORD
    model_dir = Path((env.get("CC_BUDDY_KWS_MODEL_DIR") or DEFAULT_MODEL_DIR)).expanduser()
    thr = DEFAULT_THRESHOLD
    raw_thr = (env.get("CC_BUDDY_WAKE_THRESHOLD") or "").strip()
    if raw_thr:
        try:
            thr = float(raw_thr)
        except ValueError:
            log.warning("ears: CC_BUDDY_WAKE_THRESHOLD=%r is not a number; using %s", raw_thr, thr)
    device = (env.get("CC_BUDDY_MIC") or "").strip() or None
    return EarsConfig(enabled=enabled, wake_word=word, model_dir=model_dir, threshold=thr, device=device)


# ---- keyword file ---------------------------------------------------------------

def keyword_id(phrase: str) -> str:
    """"hey buddy" -> "hey_buddy": the tag the spotter returns on a hit."""
    return re.sub(r"[^a-z0-9]+", "_", phrase.strip().lower()).strip("_") or "wake"


def keyword_line(tokens: list[str], phrase: str, boost: float, threshold: float) -> str:
    """One line of a sherpa-onnx keywords file: tokens, then :boost #threshold @id."""
    return f"{' '.join(tokens)} :{boost:g} #{threshold:g} @{keyword_id(phrase)}"


def encode_phrase(phrase: str, bpe_model: Path) -> list[str]:
    """BPE tokens of the phrase in the KWS model's vocabulary (upper-case, as gigaspeech was trained)."""
    import sentencepiece as spm  # pulled in with sherpa-onnx's KWS extra

    sp = spm.SentencePieceProcessor(model_file=str(bpe_model))
    return list(sp.encode(phrase.strip().upper(), out_type=str))


def write_keywords_file(config: EarsConfig, path: Optional[Path] = None) -> Path:
    tokens = encode_phrase(config.wake_word, config.model_dir / "bpe.model")
    out = path or (config.model_dir / f"keywords-{keyword_id(config.wake_word)}.txt")
    out.write_text(keyword_line(tokens, config.wake_word, config.boost, config.threshold) + "\n", encoding="utf-8")
    return out


def model_present(model_dir: Path) -> bool:
    return all((model_dir / f).exists() for f in (
        "tokens.txt", "bpe.model",
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"))


# ---- the spotter (model seam) ---------------------------------------------------

class WakeSpotter:
    """sherpa-onnx KeywordSpotter over one streaming session.

    feed(samples, rate) returns the keyword id when the phrase completes in
    this block, else None. `samples` is float32 in [-1, 1]; any sample rate
    is fine — sherpa resamples to the model's 16 kHz.
    """

    def __init__(self, config: EarsConfig, keywords_file: Optional[Path] = None) -> None:
        import sherpa_onnx

        m = config.model_dir
        if not model_present(m):
            raise FileNotFoundError(f"KWS model missing under {m} — download {MODEL_URL}")
        # Always (re)write the file: sherpa-onnx exit()s the whole process on a
        # missing keywords file rather than raising.
        kw = write_keywords_file(config, keywords_file)
        if not kw.exists():
            raise FileNotFoundError(f"could not write keywords file {kw}")
        self.keywords_file = kw
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(m / "tokens.txt"),
            encoder=str(m / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            decoder=str(m / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            joiner=str(m / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            num_threads=1, max_active_paths=4, keywords_file=str(kw),
            keywords_score=1.0, keywords_threshold=config.threshold, num_trailing_blanks=1, provider="cpu")
        self._stream = self._kws.create_stream()

    def feed(self, samples: Any, rate: int) -> Optional[str]:
        self._stream.accept_waveform(rate, samples)
        hit: Optional[str] = None
        while self._kws.is_ready(self._stream):
            self._kws.decode_stream(self._stream)
            r = self._kws.get_result(self._stream)
            if r:
                hit = r
                self._kws.reset_stream(self._stream)
        return hit


# ---- gating (pure) --------------------------------------------------------------

@dataclass
class WakeGate:
    """Decides whether a spotter hit becomes a wake event.

    suppressed(): the daemon's reasons not to wake — the listen key is down
    (the human is dictating), a voice session is already open (buddy would
    hear itself), a permission card is up. cooldown: one wake per window.
    """

    cooldown_secs: float = DEFAULT_COOLDOWN_SECS
    suppressed: Callable[[], bool] = lambda: False
    last_wake_at: float = field(default=float("-inf"))
    hits: int = 0
    suppressed_hits: int = 0

    def consider(self, hit: Optional[str], now: float) -> Optional[str]:
        if not hit:
            return None
        self.hits += 1
        if self.suppressed():
            self.suppressed_hits += 1
            return None
        if now - self.last_wake_at < self.cooldown_secs:
            return None
        self.last_wake_at = now
        return hit


def rms_int16(block: Any) -> float:
    import numpy as np

    a = np.asarray(block, dtype=np.float32)
    return float(math.sqrt(float(np.mean(a * a)))) if a.size else 0.0


# ---- the stream ------------------------------------------------------------------

class Ears:
    """Own the mic stream; fan PCM out to the spotter and to subscribers.

    on_wake(keyword_id) is called on the event loop. subscribe() returns an
    asyncio.Queue that receives every raw int16 24 kHz block (bytes) until
    unsubscribe(); while any subscriber exists the spotter is muted.
    """

    def __init__(self, config: EarsConfig, on_wake: Callable[[str], None],
                 loop: asyncio.AbstractEventLoop, suppressed: Callable[[], bool] = lambda: False,
                 spotter: Optional[WakeSpotter] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.on_wake = on_wake
        self.loop = loop
        self.gate = WakeGate(cooldown_secs=config.cooldown_secs, suppressed=suppressed)
        self._spotter = spotter
        self._clock = clock
        self._subs: list[asyncio.Queue[bytes]] = []
        self._stream: Any = None
        self._silent_since: Optional[float] = None
        self._silence_warned = False
        self.blocks = 0
        self.last_rms = 0.0

    # -- subscribers (the voice session) --
    def subscribe(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        if q in self._subs:
            self._subs.remove(q)

    @property
    def muted(self) -> bool:
        return bool(self._subs)

    # -- one block, called on the audio thread --
    def _on_block(self, block_int16: Any, now: Optional[float] = None) -> Optional[str]:
        """Process one int16 block. Returns the keyword id on a wake (tests)."""
        import numpy as np

        now = self._clock() if now is None else now
        self.blocks += 1
        self.last_rms = rms_int16(block_int16)
        self._watch_silence(now)
        if self._subs:
            raw = np.asarray(block_int16, dtype=np.int16).tobytes()
            for q in list(self._subs):
                self.loop.call_soon_threadsafe(_offer, q, raw)
            return None            # muted: never wake on our own voice
        if self._spotter is None:
            return None
        samples = np.asarray(block_int16, dtype=np.float32) / 32768.0
        hit = self.gate.consider(self._spotter.feed(samples, SAMPLE_RATE), now)
        if hit:
            self.loop.call_soon_threadsafe(self.on_wake, hit)
        return hit

    def _watch_silence(self, now: float) -> None:
        if self.last_rms >= SILENCE_RMS:
            self._silent_since = None
            return
        if self._silent_since is None:
            self._silent_since = now
        elif not self._silence_warned and now - self._silent_since >= SILENCE_WARN_SECS:
            self._silence_warned = True
            log.warning("ears: the microphone has been silent for %.0f s (rms %.1f) — is Microphone "
                        "granted to %s in System Settings > Privacy & Security?", SILENCE_WARN_SECS,
                        self.last_rms, sys.executable)

    # -- lifecycle --
    def start(self) -> bool:
        """Open the mic. False (with one warning) when audio or the model is unavailable."""
        try:
            import sounddevice as sd
        except ImportError as e:
            log.warning("ears: sounddevice not importable (%s) — no wake word (pip install sounddevice)", e)
            return False
        if self._spotter is None:
            try:
                self._spotter = WakeSpotter(self.config)
            except Exception as e:  # noqa: BLE001
                log.warning("ears: wake-word model unavailable (%s) — no wake word", e)
                return False
        device = None
        if self.config.device:
            device = _find_input_device(sd, self.config.device)
            if device is None:
                log.warning("ears: no input device matching %r; using the default", self.config.device)

        def callback(indata: Any, frames: int, _time: Any, status: Any) -> None:
            if status:
                log.debug("ears: stream status %s", status)
            try:
                self._on_block(indata[:, 0].copy())
            except Exception:  # noqa: BLE001
                log.exception("ears: block failed")

        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                          blocksize=int(SAMPLE_RATE * BLOCK_SECS), device=device,
                                          callback=callback)
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            log.warning("ears: could not open the microphone (%s) — no wake word", e)
            self._stream = None
            return False
        name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
        log.info("ears: listening for %r on %s (%d Hz, threshold %.2f)", self.config.wake_word, name,
                 SAMPLE_RATE, self.config.threshold)
        return True

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None


def _offer(q: "asyncio.Queue[bytes]", raw: bytes) -> None:
    """Drop the oldest block rather than block the audio thread on a slow consumer."""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(raw)


def _find_input_device(sd: Any, needle: str) -> Optional[int]:
    n = needle.lower()
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0 and n in str(d.get("name", "")).lower():
            return i
    return None


# ---- `cc-buddy-bridge ears-check` -------------------------------------------------

def diagnose(seconds: float = 8.0) -> int:
    """Print why the wake word is or isn't working; listen for it for a few seconds."""
    cfg = configured()
    print(f"wake word:      {cfg.wake_word!r}  (CC_BUDDY_WAKE_WORD)")
    print(f"model dir:      {cfg.model_dir}  present={model_present(cfg.model_dir)}")
    if not model_present(cfg.model_dir):
        print(f"  download: curl -L {MODEL_URL} | tar xj -C {cfg.model_dir.parent}")
        return 2
    try:
        import sounddevice as sd
    except ImportError:
        print("sounddevice:    NOT INSTALLED (pip install sounddevice)")
        return 2
    dev = sd.query_devices(kind="input")
    print(f"input device:   {dev['name']}")
    spot = WakeSpotter(cfg)
    print(f"keywords file:  {spot.keywords_file} -> {spot.keywords_file.read_text().strip()}")
    print(f"\nSay {cfg.wake_word!r} within {seconds:.0f} s ...")
    import numpy as np

    hits: list[float] = []
    silent = True
    t0 = time.monotonic()

    def cb(indata: Any, frames: int, _t: Any, status: Any) -> None:
        nonlocal silent
        block = indata[:, 0].copy()
        if rms_int16(block) >= SILENCE_RMS:
            silent = False
        r = spot.feed(np.asarray(block, dtype=np.float32) / 32768.0, SAMPLE_RATE)
        if r:
            hits.append(time.monotonic() - t0)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=int(SAMPLE_RATE * BLOCK_SECS), callback=cb):
        while time.monotonic() - t0 < seconds:
            time.sleep(0.1)
            if hits:
                break
    if hits:
        print(f"HEARD IT at {hits[0]:.1f} s — ears are working.")
        return 0
    if silent:
        print("mic was silent the whole time: grant Microphone to", os.path.realpath(sys.executable))
        return 1
    print("mic is live but the phrase was not heard — try again closer, or lower CC_BUDDY_WAKE_THRESHOLD (0.25).")
    return 1
