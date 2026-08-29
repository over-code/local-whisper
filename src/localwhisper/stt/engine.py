"""faster-whisper wrapper.

faster-whisper runs Whisper through CTranslate2, which is fast enough on a
plain CPU that a laptop can dictate a sentence in about a second with the
``small`` model. The model is loaded once and kept resident: reloading it per
utterance would dominate the latency budget.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..config import ModelConfig
from ..logging_setup import get
from .. import paths

log = get("stt")


@dataclass(frozen=True)
class ModelInfo:
    name: str
    label: str
    size_mb: int
    note: str


#: The subset of Whisper models that make sense for dictation. Anything larger
#: than ``large-v3`` costs more latency than it wins in accuracy for short
#: utterances; anything smaller than ``base`` mangles proper nouns.
MODELS: tuple[ModelInfo, ...] = (
    ModelInfo("tiny", "Tiny", 75, "Fastest, noticeably rough. Good on very old hardware."),
    ModelInfo("base", "Base", 145, "Fast, usable for short commands."),
    ModelInfo("small", "Small", 484, "The sweet spot on CPU — recommended default."),
    ModelInfo("medium", "Medium", 1530, "Clearly better punctuation, ~3x slower than small."),
    ModelInfo("large-v3", "Large v3", 3090, "Best accuracy. Wants a GPU."),
    ModelInfo("large-v3-turbo", "Large v3 Turbo", 1620, "Near-large accuracy, much faster. GPU or a strong CPU."),
    ModelInfo("distil-large-v3", "Distil Large v3", 1510, "English only, fast, very accurate."),
)

MODEL_NAMES = tuple(m.name for m in MODELS)


@dataclass
class TranscriptionResult:
    text: str
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    #: Seconds spent inside the model.
    elapsed: float = 0.0
    segments: list[str] = field(default_factory=list)

    @property
    def speed_factor(self) -> float:
        """How many times faster than real time the transcription ran."""
        return (self.duration / self.elapsed) if self.elapsed > 0 else 0.0


def detect_device(requested: str = "auto") -> str:
    if requested in ("cpu", "cuda"):
        return requested
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception as exc:  # pragma: no cover - depends on the CUDA runtime
        log.debug("CUDA probe failed (%s); using CPU", exc)
    return "cpu"


def detect_compute_type(requested: str, device: str) -> str:
    if requested and requested != "auto":
        return requested
    # int8 on CPU is ~2x faster than float32 with no audible quality loss for
    # dictation; float16 is the natural choice on any recent GPU.
    return "float16" if device == "cuda" else "int8"


class Transcriber:
    """Loads a Whisper model and turns recorded samples into text.

    All methods are safe to call from any thread; the model itself is guarded
    by a lock because CTranslate2 models are not re-entrant.
    """

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._model = None
        self._loaded_key: tuple[str, str, str] | None = None
        self._lock = threading.Lock()
        self.device = "cpu"
        self.compute_type = "int8"

    # ------------------------------------------------------------------ model

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def key(self) -> tuple[str, str, str]:
        device = detect_device(self.config.device)
        return (self.config.name, device, detect_compute_type(self.config.compute_type, device))

    def load(self, on_status: Callable[[str], None] | None = None) -> None:
        """Load (downloading first, if needed) the configured model."""
        key = self.key()
        with self._lock:
            if self._model is not None and self._loaded_key == key:
                return
            name, device, compute_type = key
            self.device, self.compute_type = device, compute_type

            from faster_whisper import WhisperModel

            cache = paths.models_dir()
            cache.mkdir(parents=True, exist_ok=True)
            if on_status:
                on_status(f"Loading {name} ({device}/{compute_type})…")
            log.info("loading model %s on %s (%s)", name, device, compute_type)
            started = time.monotonic()

            kwargs: dict = {
                "device": device,
                "compute_type": compute_type,
                "download_root": str(cache),
            }
            if self.config.cpu_threads > 0 and device == "cpu":
                kwargs["cpu_threads"] = self.config.cpu_threads

            try:
                model = WhisperModel(name, **kwargs)
            except ValueError as exc:
                # Typical cause: the GPU cannot do float16, or int8_float16 on CPU.
                log.warning("model load failed with %s; retrying with safe defaults", exc)
                fallback = "int8" if device == "cpu" else "int8_float16"
                kwargs["compute_type"] = fallback
                model = WhisperModel(name, **kwargs)
                self.compute_type = fallback

            self._model = model
            self._loaded_key = key
            log.info("model ready in %.1fs", time.monotonic() - started)

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._loaded_key = None

    def warmup(self) -> None:
        """Run a tiny inference so the first real dictation is not the slow one."""
        try:
            self.load()
            silence = np.zeros(int(16000 * 0.5), dtype=np.float32)
            self.transcribe(silence, 16000)
            log.info("warmup complete")
        except Exception as exc:  # pragma: no cover - depends on the model files
            log.warning("warmup failed: %s", exc)

    # ------------------------------------------------------------- inference

    def transcribe(self, samples: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        if samples.size == 0:
            return TranscriptionResult(text="")
        if sample_rate != 16000:
            samples = _resample(samples, sample_rate, 16000)
            sample_rate = 16000

        self.load()
        duration = samples.size / float(sample_rate)
        started = time.monotonic()

        with self._lock:
            model = self._model
            assert model is not None
            language = self.config.language or None
            segments, info = model.transcribe(
                samples,
                language=language,
                beam_size=max(1, self.config.beam_size),
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                condition_on_previous_text=False,  # stops runaway repetition
                initial_prompt=self.config.initial_prompt or None,
            )
            # `segments` is a generator: consuming it here is what actually runs
            # the decoder, so it has to happen inside the lock.
            texts = [segment.text for segment in segments]

        elapsed = time.monotonic() - started
        text = "".join(texts).strip()
        result = TranscriptionResult(
            text=text,
            language=getattr(info, "language", "") or "",
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=duration,
            elapsed=elapsed,
            segments=[t.strip() for t in texts],
        )
        log.info(
            "transcribed %.1fs of audio in %.1fs (%.1fx realtime): %r",
            duration, elapsed, result.speed_factor, text[:80],
        )
        return result


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resampling — fine for speech, and avoids a scipy dependency."""
    if source_rate == target_rate or samples.size == 0:
        return samples
    ratio = target_rate / float(source_rate)
    target_length = int(round(samples.size * ratio))
    source_index = np.linspace(0, samples.size - 1, target_length, dtype=np.float64)
    return np.interp(source_index, np.arange(samples.size), samples).astype(np.float32)


def model_is_downloaded(name: str) -> bool:
    """True when the model is already in our cache (no network needed)."""
    root = paths.models_dir()
    if not root.exists():
        return False
    needle = name.replace("/", "--").lower()
    for entry in root.iterdir():
        if needle in entry.name.lower():
            return any(entry.rglob("model.bin")) or any(entry.rglob("*.bin"))
    return False
