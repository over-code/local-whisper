"""Clipboard access that works from a background process.

On Wayland a normal toolkit clipboard write requires the writing window to
have focus — which a dictation daemon never does. ``wl-copy`` sidesteps that
with the wlr-data-control protocol, which KWin implements, so it is the
primary path on Plasma; X11 gets xclip/xsel and Qt is the last resort.
"""

from __future__ import annotations

from .. import env
from ..logging_setup import get
from .base import InjectionError, run

log = get("clipboard")


def _backends() -> list[str]:
    session = env.session()
    order = ["wl-copy", "xclip", "xsel"] if session.is_wayland else ["xclip", "xsel", "wl-copy"]
    return [name for name in order if env.has_tool(name)]


def available() -> bool:
    return bool(_backends()) or _qt_clipboard() is not None


def set_text(text: str) -> str:
    """Put ``text`` on the clipboard. Returns the backend that did it."""
    payload = text.encode("utf-8")
    errors: list[str] = []
    for backend in _backends():
        try:
            if backend == "wl-copy":
                # --type keeps rich-text apps from guessing wrong.
                run(["wl-copy", "--type", "text/plain;charset=utf-8"], stdin=payload)
            elif backend == "xclip":
                run(["xclip", "-selection", "clipboard", "-in"], stdin=payload)
            else:
                run(["xsel", "--clipboard", "--input"], stdin=payload)
            return backend
        except InjectionError as exc:
            errors.append(str(exc))
            continue

    clipboard = _qt_clipboard()
    if clipboard is not None:
        clipboard.setText(text)
        return "qt"
    raise InjectionError("no clipboard tool available (install wl-clipboard or xclip): " + "; ".join(errors))


def get_text() -> str:
    """Read the clipboard, returning "" when it is empty or unreadable."""
    session = env.session()
    order = ["wl-paste", "xclip", "xsel"] if session.is_wayland else ["xclip", "xsel", "wl-paste"]
    for backend in order:
        if not env.has_tool(backend):
            continue
        try:
            if backend == "wl-paste":
                proc = run(["wl-paste", "--no-newline"], timeout=3.0)
            elif backend == "xclip":
                proc = run(["xclip", "-selection", "clipboard", "-out"], timeout=3.0)
            else:
                proc = run(["xsel", "--clipboard", "--output"], timeout=3.0)
            return proc.stdout.decode("utf-8", "replace")
        except InjectionError:
            continue  # an empty clipboard makes these tools exit non-zero

    clipboard = _qt_clipboard()
    return clipboard.text() if clipboard is not None else ""


def _qt_clipboard():
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        return app.clipboard() if app is not None else None
    except Exception:  # pragma: no cover - Qt is optional for the CLI
        return None
