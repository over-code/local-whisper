"""Two short sine blips: one when recording opens, one when it closes.

Synthesised rather than shipped as files — it keeps the package tiny and the
cue is deliberately quiet and short (60 ms), because you hear it every time you
dictate.
"""

from __future__ import annotations

import threading

import numpy as np

from ..logging_setup import get

log = get("audio.cues")

_SAMPLE_RATE = 44100


def _tone(frequency: float, duration: float = 0.06, volume: float = 0.12) -> np.ndarray:
    t = np.linspace(0, duration, int(_SAMPLE_RATE * duration), endpoint=False)
    wave = np.sin(2 * np.pi * frequency * t)
    # A raised-cosine envelope removes the click at both ends.
    envelope = np.hanning(wave.size)
    return (wave * envelope * volume).astype(np.float32)


_START = None
_STOP = None
_CANCEL = None


def _cached() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    global _START, _STOP, _CANCEL
    if _START is None:
        _START = _tone(880.0)
        _STOP = _tone(587.33)
        _CANCEL = np.concatenate([_tone(440.0, 0.05), _tone(330.0, 0.05)])
    return _START, _STOP, _CANCEL


def play(kind: str = "start") -> None:
    """Play a cue without blocking the caller."""
    def worker() -> None:
        try:
            import sounddevice as sd

            start, stop, cancel = _cached()
            samples = {"start": start, "stop": stop, "cancel": cancel}.get(kind, start)
            sd.play(samples, _SAMPLE_RATE, blocking=True)
        except Exception as exc:  # pragma: no cover - audio output is optional
            log.debug("could not play the %s cue: %s", kind, exc)

    threading.Thread(target=worker, name="lw-cue", daemon=True).start()
