"""Hotkey handling: one manager, two very different mechanisms.

* **toggle mode** — the KDE global shortcut runs ``local-whisper toggle``,
  which talks to the daemon over its socket. No permissions, works on Wayland.
* **hold mode** — an evdev listener sees key press *and* release, which is the
  only way to build real push-to-talk on Linux.

The manager owns whichever one the configuration asks for and reports a plain
sentence about what actually happened, which the settings window and
``local-whisper doctor`` both display.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import HotkeyConfig
from ..logging_setup import get
from . import kde
from .evdev_backend import EvdevHotkeyListener, HotkeyError, parse_combo

log = get("hotkey")

__all__ = ["HotkeyManager", "HotkeyError", "HotkeyStatus", "kde", "parse_combo"]


@dataclass
class HotkeyStatus:
    backend: str
    active: bool
    detail: str


class HotkeyManager:
    """Turns raw key events into start/stop/toggle intents."""

    def __init__(
        self,
        config: HotkeyConfig,
        on_start: Callable[[bool], None],
        on_stop: Callable[[], None],
        on_toggle: Callable[[], None],
        is_recording: Callable[[], bool],
    ) -> None:
        self.config = config
        self.on_start = on_start          # on_start(hands_free: bool)
        self.on_stop = on_stop
        self.on_toggle = on_toggle
        self.is_recording = is_recording

        self._listener: EvdevHotkeyListener | None = None
        self._latched = False
        self._latch_candidate = False
        self._last_release = 0.0
        self.status = HotkeyStatus("none", False, "not started")

    # ------------------------------------------------------------- lifecycle

    def start(self) -> HotkeyStatus:
        backend = self.config.backend
        wants_evdev = backend == "evdev" or (backend == "auto" and self.config.mode == "hold")

        if not wants_evdev:
            self.status = HotkeyStatus(
                "ipc", True,
                f"desktop shortcut ({self.config.kde_shortcut}) → `local-whisper toggle`",
            )
            return self.status

        try:
            listener = EvdevHotkeyListener(
                self.config.combo,
                on_press=self._on_press,
                on_release=self._on_release,
            )
            listener.start()
        except HotkeyError as exc:
            log.warning("evdev hotkey unavailable: %s", exc)
            self.status = HotkeyStatus(
                "ipc", True,
                f"push-to-talk unavailable ({exc}); falling back to the desktop shortcut",
            )
            return self.status
        except Exception as exc:  # pragma: no cover - unexpected evdev failure
            log.exception("hotkey listener failed to start")
            self.status = HotkeyStatus("ipc", True, f"push-to-talk failed to start: {exc}")
            return self.status

        self._listener = listener
        self.status = HotkeyStatus("evdev", True, f"hold {self.config.combo} to dictate")
        return self.status

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._latched = False
        self.status = HotkeyStatus(self.status.backend, False, "stopped")

    def restart(self, config: HotkeyConfig | None = None) -> HotkeyStatus:
        if config is not None:
            self.config = config
        self.stop()
        return self.start()

    @property
    def key_held(self) -> bool:
        """True while the push-to-talk keys are physically down."""
        return bool(self._listener is not None and self._listener.held)

    # ------------------------------------------------------------- callbacks

    def _on_press(self) -> None:
        if self.config.mode != "hold":
            self.on_toggle()
            return

        if self._latched:
            # Second tap of a hands-free session: finish it.
            self._latched = False
            self._latch_candidate = False
            self.on_stop()
            return

        now = time.monotonic()
        self._latch_candidate = (
            self.config.double_tap_latch
            and (now - self._last_release) < self.config.double_tap_window
        )
        self.on_start(self._latch_candidate)

    def _on_release(self) -> None:
        if self.config.mode != "hold":
            return
        self._last_release = time.monotonic()
        if self._latch_candidate:
            # Double tap: keep recording until the next press.
            self._latched = True
            self._latch_candidate = False
            log.debug("hands-free latched")
            return
        if self.is_recording():
            self.on_stop()

    @property
    def is_latched(self) -> bool:
        return self._latched
