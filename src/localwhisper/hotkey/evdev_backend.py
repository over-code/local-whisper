"""Push-to-talk hotkeys read straight from the kernel's input devices.

Why not a desktop shortcut? Because KDE (and every other desktop shortcut
system) only tells an application that a shortcut *fired* — there is no
"released" event, so hold-to-talk is impossible with it. Reading evdev gives us
both edges, and it works identically on X11 and Wayland.

The cost is a permission: the user must be able to read ``/dev/input/event*``,
which on Debian means being in the ``input`` group. ``local-whisper doctor``
checks this and the installer offers to set it up.
"""

from __future__ import annotations

import errno
import select
import threading
import time
from collections.abc import Callable

from ..logging_setup import get

log = get("hotkey.evdev")

#: Aliases the user may type in ``hotkey.combo``, mapped to the evdev names
#: that satisfy them. Any one of the listed keys counts as pressed.
KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "control": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "lctrl": ("KEY_LEFTCTRL",),
    "rctrl": ("KEY_RIGHTCTRL",),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "lalt": ("KEY_LEFTALT",),
    "ralt": ("KEY_RIGHTALT", "KEY_RIGHTALT"),
    "altgr": ("KEY_RIGHTALT",),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "lshift": ("KEY_LEFTSHIFT",),
    "rshift": ("KEY_RIGHTSHIFT",),
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "meta": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "win": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "space": ("KEY_SPACE",),
    "esc": ("KEY_ESC",),
    "escape": ("KEY_ESC",),
    "capslock": ("KEY_CAPSLOCK",),
    "tab": ("KEY_TAB",),
    "enter": ("KEY_ENTER",),
    "insert": ("KEY_INSERT",),
    "menu": ("KEY_COMPOSE",),
    "pause": ("KEY_PAUSE",),
    "scrolllock": ("KEY_SCROLLLOCK",),
}


class HotkeyError(RuntimeError):
    pass


def parse_combo(combo: str) -> list[tuple[int, ...]]:
    """"super+alt" -> [(KEY_LEFTMETA, KEY_RIGHTMETA), (KEY_LEFTALT, KEY_RIGHTALT)]

    Each element is a group of interchangeable keycodes; the combo is held when
    at least one key from every group is down.
    """
    try:
        from evdev import ecodes
    except ImportError as exc:  # pragma: no cover
        raise HotkeyError("python3-evdev is not installed") from exc

    groups: list[tuple[int, ...]] = []
    for token in (part.strip().lower() for part in combo.split("+")):
        if not token:
            continue
        names = KEY_ALIASES.get(token)
        if names is None:
            candidate = token.upper()
            for attempt in (f"KEY_{candidate}", candidate):
                if hasattr(ecodes, attempt):
                    names = (attempt,)
                    break
        if names is None:
            raise HotkeyError(f"unknown key in the hotkey combination: {token!r}")
        codes = tuple(getattr(ecodes, name) for name in names if hasattr(ecodes, name))
        if not codes:
            raise HotkeyError(f"unknown key in the hotkey combination: {token!r}")
        groups.append(codes)
    if not groups:
        raise HotkeyError("empty hotkey combination")
    return groups


def keyboard_devices() -> list:
    """Every readable input device that looks like a keyboard."""
    from evdev import InputDevice, ecodes, list_devices

    devices = []
    for path in list_devices():
        try:
            device = InputDevice(path)
        except OSError as exc:
            if exc.errno != errno.EACCES:
                log.debug("cannot open %s: %s", path, exc)
            continue
        capabilities = device.capabilities()
        keys = capabilities.get(ecodes.EV_KEY, [])
        # A keyboard has letter keys; this filters out mice, lid switches and
        # power buttons, which also report EV_KEY.
        if ecodes.KEY_A in keys and ecodes.KEY_Z in keys:
            devices.append(device)
        else:
            device.close()
    return devices


class EvdevHotkeyListener:
    """Watches every keyboard for one combination and reports both edges."""

    def __init__(
        self,
        combo: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ) -> None:
        self.combo = combo
        self.groups = parse_combo(combo)
        self.on_press = on_press
        self.on_release = on_release
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pressed: set[int] = set()
        self._active = False
        self.error: str | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        devices = keyboard_devices()
        if not devices:
            raise HotkeyError(
                "no readable keyboard devices — add yourself to the 'input' group "
                "(`sudo usermod -aG input $USER`) and log in again"
            )
        for device in devices:
            log.info("watching %s (%s)", device.path, device.name)
        self._devices = devices
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lw-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for device in getattr(self, "_devices", []):
            try:
                device.close()
            except Exception:  # pragma: no cover
                pass
        self._devices = []

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def held(self) -> bool:
        return self._active

    # ------------------------------------------------------------------ loop

    def _loop(self) -> None:
        from evdev import categorize, ecodes

        last_rescan = time.monotonic()
        while not self._stop.is_set():
            devices = {device.fd: device for device in self._devices}
            try:
                ready, _, _ = select.select(list(devices), [], [], 0.5)
            except (OSError, ValueError):
                ready = []

            for fd in ready:
                device = devices.get(fd)
                if device is None:
                    continue
                try:
                    for event in device.read():
                        if event.type == ecodes.EV_KEY:
                            self._on_key(categorize(event))
                except OSError:
                    # Device unplugged: drop it, the rescan below picks up new ones.
                    log.info("input device went away: %s", getattr(device, "path", "?"))
                    self._devices = [d for d in self._devices if d.fd != fd]

            # Keyboards come and go (docking stations, Bluetooth).
            if time.monotonic() - last_rescan > 5.0:
                last_rescan = time.monotonic()
                self._rescan()

    def _rescan(self) -> None:
        known = {device.path for device in self._devices}
        try:
            for device in keyboard_devices():
                if device.path in known:
                    device.close()
                else:
                    log.info("new keyboard: %s (%s)", device.path, device.name)
                    self._devices.append(device)
        except Exception as exc:  # pragma: no cover
            log.debug("device rescan failed: %s", exc)

    def _on_key(self, event) -> None:
        code = event.scancode
        if event.keystate == event.key_down:
            self._pressed.add(code)
        elif event.keystate == event.key_up:
            self._pressed.discard(code)
        else:
            return  # autorepeat

        satisfied = all(any(code in self._pressed for code in group) for group in self.groups)
        if satisfied and not self._active:
            self._active = True
            self._fire(self.on_press)
        elif not satisfied and self._active:
            self._active = False
            if self.on_release is not None:
                self._fire(self.on_release)

    @staticmethod
    def _fire(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:  # pragma: no cover - a UI bug must not kill the listener
            log.exception("hotkey callback failed")
