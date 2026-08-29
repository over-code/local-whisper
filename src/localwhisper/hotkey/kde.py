"""Register the toggle/cancel shortcuts with KDE Plasma.

Plasma routes global shortcuts through kglobalaccel, which reads
``~/.config/kglobalshortcutsrc`` and the ``.desktop`` files it points at. That
is the only shortcut mechanism that works unchanged on Plasma 6 under Wayland,
where an application can no longer grab keys for itself — KWin owns the
keyboard and hands the shortcut to us by launching ``local-whisper toggle``,
which is a socket write to the already-running daemon.

Push-to-talk cannot be expressed this way (Plasma never reports key *release*),
so hold-to-talk uses the evdev backend instead. Tap-to-toggle needs no special
permissions at all, which is why it is the default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import APP_TITLE
from ..config import HotkeyConfig
from ..logging_setup import get

log = get("hotkey.kde")

TOGGLE_DESKTOP_ID = "local-whisper-toggle.desktop"
CANCEL_DESKTOP_ID = "local-whisper-cancel.desktop"


@dataclass
class Step:
    ok: bool
    message: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return ("✓ " if self.ok else "✗ ") + self.message


def applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "applications"


def _executable() -> str:
    """Absolute path to the CLI, so the shortcut works without a login shell."""
    found = shutil.which("local-whisper")
    if found:
        return found
    # Installed into a venv that is not on the desktop session's PATH.
    import sys

    return f"{sys.executable} -m localwhisper"


def _desktop_entry(name: str, command: str, shortcut: str) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={name}",
            f"Exec={command}",
            "Icon=audio-input-microphone",
            "Terminal=false",
            "NoDisplay=true",
            "StartupNotify=false",
            f"X-KDE-Shortcuts={shortcut}",
            "Categories=Utility;AudioVideo;",
            "",
        ]
    )


def _kwriteconfig() -> str | None:
    for candidate in ("kwriteconfig6", "kwriteconfig5"):
        if shutil.which(candidate):
            return candidate
    return None


def _write_shortcut_entry(desktop_id: str, friendly: str, shortcut: str) -> Step:
    tool = _kwriteconfig()
    value = f"{shortcut},none,{friendly}"
    if tool is None:
        return Step(False, "kwriteconfig6 not found — add the shortcut in System Settings")
    try:
        subprocess.run(
            [tool, "--file", "kglobalshortcutsrc",
             "--group", "services", "--group", desktop_id,
             "--key", "_launch", value],
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            [tool, "--file", "kglobalshortcutsrc",
             "--group", "services", "--group", desktop_id,
             "--key", "_k_friendly_name", friendly],
            check=True, capture_output=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return Step(False, f"could not write kglobalshortcutsrc: {exc}")
    return Step(True, f"registered {shortcut} → {friendly}")


def _refresh_desktop_database() -> Step:
    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(tool):
            try:
                subprocess.run([tool], check=False, capture_output=True, timeout=30)
                return Step(True, f"refreshed the application cache ({tool})")
            except (OSError, subprocess.TimeoutExpired) as exc:
                return Step(False, f"{tool} failed: {exc}")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(applications_dir())],
                       check=False, capture_output=True, timeout=30)
        return Step(True, "refreshed the application cache")
    return Step(True, "no desktop cache tool needed")


def reload_kglobalaccel() -> Step:
    """Ask kglobalaccel to re-read its configuration.

    Restarting the daemon is the only reliable way; it is a lightweight service
    and Plasma restores every other shortcut when it comes back.
    """
    quit_tool = next((t for t in ("kquitapp6", "kquitapp5") if shutil.which(t)), None)
    daemon = next((t for t in ("kglobalacceld", "kglobalaccel6", "kglobalaccel5") if shutil.which(t)), None)
    if quit_tool is None or daemon is None:
        return Step(False, "restart the session (or run `kglobalacceld`) to pick up the shortcut")
    try:
        subprocess.run([quit_tool, "kglobalaccel"], check=False, capture_output=True, timeout=10)
        subprocess.Popen(
            [daemon],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return Step(True, f"restarted {daemon}")
    except OSError as exc:
        return Step(False, f"could not restart kglobalaccel: {exc}")


def install(config: HotkeyConfig) -> list[Step]:
    """Create the .desktop launchers and bind them to the configured keys."""
    steps: list[Step] = []
    directory = applications_dir()
    directory.mkdir(parents=True, exist_ok=True)
    executable = _executable()

    entries = [
        (TOGGLE_DESKTOP_ID, f"{APP_TITLE}: Toggle dictation", f"{executable} toggle", config.kde_shortcut),
        (CANCEL_DESKTOP_ID, f"{APP_TITLE}: Cancel dictation", f"{executable} cancel", config.kde_cancel_shortcut),
    ]
    for desktop_id, friendly, command, shortcut in entries:
        if not shortcut:
            continue
        path = directory / desktop_id
        try:
            path.write_text(_desktop_entry(friendly, command, shortcut), encoding="utf-8")
            path.chmod(0o644)
            steps.append(Step(True, f"wrote {path}"))
        except OSError as exc:
            steps.append(Step(False, f"could not write {path}: {exc}"))
            continue
        steps.append(_write_shortcut_entry(desktop_id, friendly, shortcut))

    steps.append(_refresh_desktop_database())
    steps.append(reload_kglobalaccel())
    return steps


def uninstall() -> list[Step]:
    steps: list[Step] = []
    for desktop_id in (TOGGLE_DESKTOP_ID, CANCEL_DESKTOP_ID):
        path = applications_dir() / desktop_id
        try:
            existed = path.exists()
            path.unlink(missing_ok=True)
            steps.append(Step(True, f"removed {path}" if existed else f"{path} was not there"))
        except OSError as exc:
            steps.append(Step(False, f"could not remove {path}: {exc}"))
        tool = _kwriteconfig()
        if tool:
            subprocess.run(
                [tool, "--file", "kglobalshortcutsrc", "--group", "services",
                 "--group", desktop_id, "--key", "_launch", "--delete"],
                check=False, capture_output=True, timeout=10,
            )
    steps.append(reload_kglobalaccel())
    return steps


def manual_instructions(config: HotkeyConfig) -> str:
    return (
        "Set the shortcut by hand:\n"
        "  System Settings → Keyboard → Shortcuts → Add New → Command or Script\n"
        f"  Command:  {_executable()} toggle\n"
        f"  Shortcut: {config.kde_shortcut}\n\n"
        "Repeat with `local-whisper cancel` for the cancel shortcut "
        f"({config.kde_cancel_shortcut})."
    )
