"""Concrete keystroke backends, one per way of talking to the session.

Which of these can work depends entirely on the session:

===========  =========================================================
xdotool      X11 only. Types unicode directly, the gold standard there.
wtype        Wayland compositors with virtual-keyboard-v1 (KWin has it).
ydotool      Any session — writes to /dev/uinput through its own daemon.
uinput       Same idea as ydotool but built in, no daemon to install.
===========  =========================================================
"""

from __future__ import annotations

import time

from .. import env
from ..logging_setup import get
from .base import Backend, Capability, InjectionError, run

log = get("inject")


class XdotoolBackend(Backend):
    name = "xdotool"
    priority = 90

    def capability(self) -> Capability:
        if not env.has_tool("xdotool"):
            return Capability(self.name, False, detail="not installed (apt install xdotool)")
        if env.session().is_wayland:
            # It still works inside XWayland windows, but it cannot see native
            # Wayland ones, so we do not offer it as the automatic choice.
            return Capability(
                self.name, False,
                detail="X11 only — this is a Wayland session (XWayland windows would work)",
            )
        return Capability(self.name, True, can_type=True, can_key=True, can_click=True, detail="ready")

    def type_text(self, text: str, delay_ms: int = 4) -> None:
        run(["xdotool", "type", "--clearmodifiers", "--delay", str(max(0, delay_ms)), "--", text],
            timeout=max(10.0, len(text) * delay_ms / 1000 + 5))

    def send_paste(self) -> None:
        run(["xdotool", "key", "--clearmodifiers", "ctrl+v"])

    def click(self, button: int = 1) -> None:
        run(["xdotool", "click", "--clearmodifiers", str(button)])


class WtypeBackend(Backend):
    name = "wtype"
    priority = 80

    def capability(self) -> Capability:
        if not env.has_tool("wtype"):
            return Capability(self.name, False, detail="not installed (apt install wtype)")
        if not env.session().is_wayland:
            return Capability(self.name, False, detail="Wayland only — this is an X11 session")
        return Capability(self.name, True, can_type=True, can_key=True, detail="ready")

    def type_text(self, text: str, delay_ms: int = 4) -> None:
        # wtype builds its own keymap, so unicode and non-US layouts are fine.
        cmd = ["wtype"]
        if delay_ms > 0:
            cmd += ["-d", str(delay_ms)]
        cmd += ["--", text]
        run(cmd, timeout=max(10.0, len(text) * delay_ms / 1000 + 5))

    def send_paste(self) -> None:
        run(["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"])


class YdotoolBackend(Backend):
    name = "ydotool"
    priority = 70

    #: Linux input event codes, straight from linux/input-event-codes.h.
    KEY_LEFTCTRL = 29
    KEY_V = 47
    BTN_LEFT = 0xC0

    def capability(self) -> Capability:
        if not env.has_tool("ydotool"):
            return Capability(self.name, False, detail="not installed (apt install ydotool)")
        try:
            run(["ydotool", "key", "--help"], timeout=3.0)
        except InjectionError as exc:
            return Capability(self.name, False, detail=f"present but not usable: {exc}")
        return Capability(
            self.name, True, can_type=True, can_key=True, can_click=True,
            detail="ready (needs the ydotoold daemon running)",
        )

    def type_text(self, text: str, delay_ms: int = 4) -> None:
        run(["ydotool", "type", "--key-delay", str(max(1, delay_ms)), "--", text],
            timeout=max(10.0, len(text) * delay_ms / 1000 + 5))

    def send_paste(self) -> None:
        run([
            "ydotool", "key",
            f"{self.KEY_LEFTCTRL}:1", f"{self.KEY_V}:1",
            f"{self.KEY_V}:0", f"{self.KEY_LEFTCTRL}:0",
        ])

    def click(self, button: int = 1) -> None:
        run(["ydotool", "click", hex(self.BTN_LEFT)])


class UinputBackend(Backend):
    """Keystrokes straight into the kernel through /dev/uinput.

    This is the same trick ydotool uses, minus the extra daemon. Because the
    kernel layer knows nothing about keyboard layouts, typing is limited to
    ASCII on a US-style layout — which is why the injector prefers *paste* with
    this backend: Ctrl+V is two keycodes and layout-independent.
    """

    name = "uinput"
    priority = 60

    def __init__(self) -> None:
        self._device = None

    def capability(self) -> Capability:
        import importlib.util

        if importlib.util.find_spec("evdev") is None:
            return Capability(self.name, False, detail="python3-evdev not installed")
        ok, detail = env.can_use_uinput()
        if not ok:
            return Capability(self.name, False, detail=detail)
        return Capability(self.name, True, can_type=True, can_key=True, detail="ready (/dev/uinput)")

    # -------------------------------------------------------------- internals

    def _ui(self):
        if self._device is not None:
            return self._device
        from evdev import UInput, ecodes

        # Announce every key we might send, or the kernel drops the events.
        capabilities = {
            ecodes.EV_KEY: sorted(
                {ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_V}
                | {code for code, _shift in _US_KEYMAP.values()}
            )
        }
        self._device = UInput(capabilities, name="local-whisper-virtual-keyboard")
        # Give the compositor a moment to notice the new keyboard, otherwise the
        # first keystroke after creation is swallowed.
        time.sleep(0.15)
        return self._device

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:  # pragma: no cover
                pass
            self._device = None

    def _tap(self, code: int, modifiers: tuple[int, ...] = ()) -> None:
        from evdev import ecodes

        ui = self._ui()
        for modifier in modifiers:
            ui.write(ecodes.EV_KEY, modifier, 1)
        ui.write(ecodes.EV_KEY, code, 1)
        ui.write(ecodes.EV_KEY, code, 0)
        for modifier in reversed(modifiers):
            ui.write(ecodes.EV_KEY, modifier, 0)
        ui.syn()

    def type_text(self, text: str, delay_ms: int = 4) -> None:
        from evdev import ecodes

        unsupported = {char for char in text if char not in _US_KEYMAP and char != "\n"}
        if unsupported:
            raise InjectionError(
                "the uinput backend can only type US-layout ASCII; "
                f"{''.join(sorted(unsupported))!r} needs the paste method"
            )
        for char in text:
            if char == "\n":
                self._tap(ecodes.KEY_ENTER)
            else:
                code, shift = _US_KEYMAP[char]
                self._tap(code, (ecodes.KEY_LEFTSHIFT,) if shift else ())
            if delay_ms:
                time.sleep(delay_ms / 1000)

    def send_paste(self) -> None:
        from evdev import ecodes

        self._tap(ecodes.KEY_V, (ecodes.KEY_LEFTCTRL,))

    def release_modifiers(self) -> None:
        """Force-release the modifiers a push-to-talk hotkey may still hold.

        Without this, a Ctrl+V sent while Super+Alt are physically down arrives
        as Super+Alt+Ctrl+V and most apps ignore it.
        """
        try:
            from evdev import ecodes

            ui = self._ui()
            for name in ("KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTALT", "KEY_RIGHTALT",
                         "KEY_LEFTMETA", "KEY_RIGHTMETA", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"):
                code = getattr(ecodes, name, None)
                if code is not None:
                    ui.write(ecodes.EV_KEY, code, 0)
            ui.syn()
        except Exception as exc:  # pragma: no cover - best effort
            log.debug("could not release modifiers: %s", exc)


def _build_us_keymap() -> dict[str, tuple[int, bool]]:
    """char -> (keycode, needs_shift) for a US layout."""
    try:
        from evdev import ecodes
    except ImportError:  # pragma: no cover - keymap is only used with evdev
        return {}

    mapping: dict[str, tuple[int, bool]] = {}
    for char in "abcdefghijklmnopqrstuvwxyz":
        code = getattr(ecodes, f"KEY_{char.upper()}")
        mapping[char] = (code, False)
        mapping[char.upper()] = (code, True)
    digits = {
        "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
        "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    }
    for digit, shifted in digits.items():
        code = getattr(ecodes, f"KEY_{digit}")
        mapping[digit] = (code, False)
        mapping[shifted] = (code, True)
    punctuation = {
        " ": ("KEY_SPACE", " ", None),
        "\t": ("KEY_TAB", "\t", None),
        "-": ("KEY_MINUS", "-", "_"),
        "=": ("KEY_EQUAL", "=", "+"),
        "[": ("KEY_LEFTBRACE", "[", "{"),
        "]": ("KEY_RIGHTBRACE", "]", "}"),
        "\\": ("KEY_BACKSLASH", "\\", "|"),
        ";": ("KEY_SEMICOLON", ";", ":"),
        "'": ("KEY_APOSTROPHE", "'", '"'),
        "`": ("KEY_GRAVE", "`", "~"),
        ",": ("KEY_COMMA", ",", "<"),
        ".": ("KEY_DOT", ".", ">"),
        "/": ("KEY_SLASH", "/", "?"),
    }
    for _key, (attr, plain, shifted) in punctuation.items():
        code = getattr(ecodes, attr)
        mapping[plain] = (code, False)
        if shifted:
            mapping[shifted] = (code, True)
    return mapping


_US_KEYMAP = _build_us_keymap()


ALL_BACKENDS: tuple[type[Backend], ...] = (
    XdotoolBackend,
    WtypeBackend,
    YdotoolBackend,
    UinputBackend,
)
