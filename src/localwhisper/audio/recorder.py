"""Microphone capture built on PortAudio (via sounddevice).

Records mono float32 at 16 kHz — exactly what Whisper wants, so no resampling
step sits between the microphone and the model. The audio callback also feeds a
cheap RMS level to the UI, which is what makes the overlay's waveform move.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..config import AudioConfig
from ..logging_setup import get

log = get("audio")

LevelCallback = Callable[[float], None]
AutoStopCallback = Callable[[], None]


@dataclass
class RecordingResult:
    #: Mono float32 samples in [-1, 1].
    samples: np.ndarray
    sample_rate: int
    duration: float
    #: True when the recorder stopped itself (silence timeout or max duration).
    auto_stopped: bool = False
    #: Peak RMS seen during the take; used to detect "you never spoke".
    peak_level: float = 0.0

    @property
    def is_too_short(self) -> bool:
        return self.duration < 0.2

    @property
    def is_silent(self) -> bool:
        return self.peak_level < 0.005


def list_input_devices() -> list[tuple[int, str]]:
    """(index, label) for every device that can record."""
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - depends on system audio libs
        log.warning("sounddevice unavailable: %s", exc)
        return []
    devices = []
    try:
        for index, info in enumerate(sd.query_devices()):
            if int(info.get("max_input_channels", 0)) > 0:
                devices.append((index, str(info.get("name", f"device {index}"))))
    except Exception as exc:  # pragma: no cover
        log.warning("could not query audio devices: %s", exc)
    return devices


def resolve_device(name: str) -> int | str | None:
    """Turn a configured device name into something PortAudio accepts."""
    if not name:
        return None
    if name.isdigit():
        return int(name)
    lowered = name.lower()
    for index, label in list_input_devices():
        if lowered in label.lower():
            return index
    log.warning("input device %r not found, falling back to the default", name)
    return None


class Recorder:
    """Start/stop microphone capture with silence-based auto-stop.

    Thread-safety: ``start``/``stop`` are called from the GUI thread, the
    callback runs on PortAudio's thread. The only shared state is the chunk
    list (append-only, guarded by a lock) and a couple of floats.
    """

    def __init__(
        self,
        config: AudioConfig,
        on_level: LevelCallback | None = None,
        on_auto_stop: AutoStopCallback | None = None,
    ) -> None:
        self.config = config
        self.on_level = on_level
        self.on_auto_stop = on_auto_stop

        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._started_at = 0.0
        self._last_voice_at = 0.0
        self._peak = 0.0
        self._auto_stopped = False
        #: Auto-stop only applies to hands-free takes, not to push-to-talk.
        self.allow_auto_stop = True

    # ------------------------------------------------------------------ state

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._recording else 0.0

    # ------------------------------------------------------------------ start

    def start(self, allow_auto_stop: bool = True) -> None:
        if self._recording:
            return
        import sounddevice as sd

        self.allow_auto_stop = allow_auto_stop
        with self._lock:
            self._chunks = []
        self._peak = 0.0
        self._auto_stopped = False
        now = time.monotonic()
        self._started_at = now
        self._last_voice_at = now
        self._recording = True

        try:
            self._stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=int(self.config.sample_rate * 0.03),  # 30 ms blocks
                device=resolve_device(self.config.device),
                callback=self._callback,
            )
            self._stream.start()
        except Exception:
            self._recording = False
            self._stream = None
            raise
        log.debug("recording started (auto_stop=%s)", allow_auto_stop)

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            log.debug("audio status: %s", status)
        if not self._recording:
            return
        block = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(block)

        level = float(np.sqrt(np.mean(np.square(block)))) if frames else 0.0
        self._peak = max(self._peak, level)
        if self.on_level is not None:
            try:
                self.on_level(level)
            except Exception:  # pragma: no cover - UI callbacks must not kill audio
                log.exception("level callback failed")

        now = time.monotonic()
        if level >= self.config.silence_threshold:
            self._last_voice_at = now

        if now - self._started_at >= self.config.max_duration:
            self._request_auto_stop("maximum duration reached")
            return
        if (
            self.allow_auto_stop
            and self.config.silence_timeout > 0
            and now - self._started_at > 1.0  # give the speaker a moment to begin
            and now - self._last_voice_at >= self.config.silence_timeout
        ):
            self._request_auto_stop("silence timeout")

    def _request_auto_stop(self, reason: str) -> None:
        if self._auto_stopped:
            return
        self._auto_stopped = True
        log.debug("auto-stop: %s", reason)
        if self.on_auto_stop is not None:
            # Fired from the audio thread; the app marshals it to the GUI thread.
            try:
                self.on_auto_stop()
            except Exception:  # pragma: no cover
                log.exception("auto-stop callback failed")

    # ------------------------------------------------------------------- stop

    def stop(self) -> RecordingResult:
        duration = self.elapsed
        self._recording = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover
                log.exception("closing the audio stream failed")

        with self._lock:
            chunks = self._chunks
            self._chunks = []
        samples = (
            np.concatenate(chunks).astype(np.float32)
            if chunks
            else np.zeros(0, dtype=np.float32)
        )
        if samples.size:
            duration = samples.size / float(self.config.sample_rate)
        log.debug("recording stopped: %.2fs, peak=%.4f", duration, self._peak)
        return RecordingResult(
            samples=samples,
            sample_rate=self.config.sample_rate,
            duration=duration,
            auto_stopped=self._auto_stopped,
            peak_level=self._peak,
        )

    def cancel(self) -> None:
        """Stop and throw the audio away."""
        if self._recording or self._stream is not None:
            self.stop()
