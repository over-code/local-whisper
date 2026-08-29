"""Desktop session detection.

Every hard part of this app (hotkeys, text injection, window placement) depends
on whether we are on X11 or Wayland and which compositor is in charge, so the
detection lives in one place and everything else asks it.
"""

from __future__ import annotations

import functools
import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    #: "wayland", "x11" or "unknown"
    type: str
    #: "kde", "gnome", "sway", "hyprland", … lower-cased, best effort
    desktop: str
    #: Whether we are inside a Plasma session (KDE-specific paths are enabled).
    is_kde: bool

    @property
    def is_wayland(self) -> bool:
        return self.type == "wayland"

    @property
    def is_x11(self) -> bool:
        return self.type == "x11"

    def describe(self) -> str:
        return f"{self.desktop or 'unknown desktop'} / {self.type}"


@functools.lru_cache(maxsize=1)
def session() -> Session:
    stype = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if stype not in ("wayland", "x11"):
        # Session type is often unset for apps started outside the session
        # manager; the display sockets are the reliable tell.
        if os.environ.get("WAYLAND_DISPLAY"):
            stype = "wayland"
        elif os.environ.get("DISPLAY"):
            stype = "x11"
        else:
            stype = "unknown"

    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or ""
    ).lower()
    is_kde = "kde" in desktop or "plasma" in desktop or bool(os.environ.get("KDE_FULL_SESSION"))
    return Session(type=stype, desktop=desktop, is_kde=is_kde)


@functools.lru_cache(maxsize=None)
def has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def available_tools() -> dict[str, bool]:
    """Snapshot of the external helpers we can make use of."""
    names = [
        "xdotool",
        "wtype",
        "ydotool",
        "ydotoold",
        "wl-copy",
        "wl-paste",
        "xclip",
        "xsel",
        "notify-send",
        "kwriteconfig6",
        "kwriteconfig5",
        "qdbus6",
        "qdbus",
        "paplay",
        "kdotool",
    ]
    return {name: has_tool(name) for name in names}


def can_use_uinput() -> tuple[bool, str]:
    """Can we open /dev/uinput for writing? (needed for the built-in typer)"""
    path = "/dev/uinput"
    if not os.path.exists(path):
        return False, "/dev/uinput does not exist (is the uinput module loaded?)"
    if os.access(path, os.W_OK):
        return True, "writable"
    return False, "no write permission on /dev/uinput (see `local-whisper doctor`)"


def readable_input_devices() -> list[str]:
    """Event devices we may read for the push-to-talk hotkey."""
    import glob

    return [p for p in sorted(glob.glob("/dev/input/event*")) if os.access(p, os.R_OK)]
