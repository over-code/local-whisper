"""Command line interface.

``local-whisper`` with no arguments starts the daemon. Every other subcommand
is a thin client that writes one line to the daemon's socket — which is exactly
what the KDE global shortcut runs, so the keypress-to-recording path stays in
the single digits of milliseconds.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import APP_TITLE, __version__, env, ipc, paths
from .config import Config
from .logging_setup import setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-whisper",
        description=f"{APP_TITLE} — local voice dictation for Linux desktops.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typical use:\n"
            "  local-whisper daemon            start the tray app (or use the systemd service)\n"
            "  local-whisper toggle            start/stop dictation — bind this to a hotkey\n"
            "  local-whisper doctor            check microphone, hotkey and insertion support\n"
            "  local-whisper install-shortcut  register the KDE global shortcuts\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug output")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("daemon", help="run the tray application (default)")
    subparsers.add_parser("toggle", help="start dictation, or stop the running one")
    subparsers.add_parser("start", help="start dictating")
    subparsers.add_parser("stop", help="stop dictating and insert the text")
    subparsers.add_parser("cancel", help="throw away the current dictation")
    subparsers.add_parser("pause", help="ignore the hotkey until resumed")
    subparsers.add_parser("resume", help="react to the hotkey again")
    subparsers.add_parser("insert-last", help="insert the previous transcript again")
    subparsers.add_parser("settings", help="open the settings window")
    subparsers.add_parser("quit", help="stop the daemon")
    subparsers.add_parser("doctor", help="check this machine's dictation support")
    subparsers.add_parser("install-shortcut", help="register the KDE global shortcuts")
    subparsers.add_parser("remove-shortcut", help="remove the KDE global shortcuts")
    subparsers.add_parser("paths", help="print where the config, models and logs live")

    status = subparsers.add_parser("status", help="print the daemon's state")
    status.add_argument("--json", action="store_true", help="machine-readable output")

    history = subparsers.add_parser("history", help="print recent transcripts")
    history.add_argument("-n", "--limit", type=int, default=10)
    history.add_argument("--json", action="store_true")
    return parser


CLIENT_COMMANDS = {
    "toggle", "start", "stop", "cancel", "pause", "resume",
    "insert-last", "settings", "quit",
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Report-style commands print their own findings; stray log lines would
    # interleave with them (the rotating log file still gets everything).
    quiet = args.command in CLIENT_COMMANDS or args.command in {
        "doctor", "status", "history", "paths", "install-shortcut", "remove-shortcut",
    }
    setup(logging.DEBUG if args.verbose else logging.INFO, quiet=quiet and not args.verbose)
    command = args.command or "daemon"

    if command == "daemon":
        return _run_daemon()
    if command in CLIENT_COMMANDS:
        return _client(command)
    if command == "status":
        return _status(args.json)
    if command == "history":
        return _history(args.limit, args.json)
    if command == "doctor":
        return doctor()
    if command == "install-shortcut":
        return _install_shortcut(remove=False)
    if command == "remove-shortcut":
        return _install_shortcut(remove=True)
    if command == "paths":
        return _paths()
    parser.print_help()
    return 2


# ------------------------------------------------------------------- actions

def _run_daemon() -> int:
    from .daemon import run

    return run()


def _client(command: str) -> int:
    try:
        reply = ipc.send(command)
    except ConnectionError as exc:
        print(f"{exc}. Start it with: local-whisper daemon", file=sys.stderr)
        return 3
    if not reply.get("ok", False):
        print(reply.get("error", "the daemon rejected the command"), file=sys.stderr)
        return 1
    return 0


def _status(as_json: bool) -> int:
    try:
        reply = ipc.send("status")
    except ConnectionError as exc:
        if as_json:
            print(json.dumps({"running": False, "error": str(exc)}))
        else:
            print("not running")
        return 3
    if as_json:
        print(json.dumps({"running": True, **reply}, indent=2))
        return 0
    print(f"state       : {reply.get('state')}")
    print(f"model       : {reply.get('model')} ({'loaded' if reply.get('model_loaded') else 'not loaded'})"
          f" on {reply.get('device')}")
    hotkey = reply.get("hotkey", {})
    print(f"hotkey      : {hotkey.get('mode')} — {hotkey.get('detail')}")
    print(f"insertion   : {reply.get('insertion')}")
    if reply.get("last_text"):
        print(f"last text   : {reply['last_text'][:70]}")
    return 0


def _history(limit: int, as_json: bool) -> int:
    try:
        reply = ipc.send("history", {"limit": limit})
        entries = reply.get("entries", [])
    except ConnectionError:
        # The daemon is not running, but the database is still readable.
        from .history import History

        entries = [
            {"text": entry.text, "when": entry.when(), "model": entry.model}
            for entry in History().recent(limit)
        ]
    if as_json:
        print(json.dumps(entries, indent=2))
        return 0
    for entry in entries:
        print(f"{entry['when']}  {entry['text']}")
    return 0


def _install_shortcut(remove: bool) -> int:
    from .hotkey import kde

    config = Config.load()
    steps = kde.uninstall() if remove else kde.install(config.hotkey)
    for step in steps:
        print(step)
    if not remove:
        print()
        print(kde.manual_instructions(config.hotkey))
    return 0 if all(step.ok for step in steps) else 1


def _paths() -> int:
    print(f"config      : {paths.config_file()}")
    print(f"models      : {paths.models_dir()}")
    print(f"history     : {paths.history_db()}")
    print(f"log         : {paths.log_file()}")
    print(f"socket      : {paths.socket_path()}")
    return 0


# -------------------------------------------------------------------- doctor

def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "✓" if ok else "✗"
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    return ok


def doctor() -> int:
    """Explain what works on this machine and what to install to fix the rest."""
    config = Config.load()
    session = env.session()
    problems: list[str] = []

    print(f"{APP_TITLE} {__version__}")
    print()
    print("Session")
    _check(f"desktop: {session.desktop or 'unknown'}", True)
    _check(f"session type: {session.type}", session.type != "unknown",
           "neither WAYLAND_DISPLAY nor DISPLAY is set" if session.type == "unknown" else "")
    print()

    print("Daemon")
    running = ipc.is_running()
    _check("daemon running", running, "" if running else "start it with `local-whisper daemon`")
    print()

    print("Microphone")
    try:
        from .audio import list_input_devices

        devices = list_input_devices()
        if not _check(f"{len(devices)} input device(s)", bool(devices),
                      "install libportaudio2 and check your sound settings"):
            problems.append("sudo apt install libportaudio2")
        for _index, name in devices[:6]:
            print(f"      · {name}")
    except Exception as exc:
        _check("audio stack", False, str(exc))
        problems.append("sudo apt install libportaudio2")
    print()

    print("Speech model")
    from .stt.engine import detect_compute_type, detect_device, model_is_downloaded

    device = detect_device(config.model.device)
    _check(f"compute device: {device} ({detect_compute_type(config.model.compute_type, device)})", True)
    downloaded = model_is_downloaded(config.model.name)
    _check(f"model '{config.model.name}' downloaded", downloaded,
           "" if downloaded else "it will be fetched on first use (needs the network once)")
    print()

    print("Text insertion")
    from .inject import TextInjector

    injector = TextInjector(config.insert)
    any_backend = False
    for name, available, detail in injector.diagnostics():
        any_backend = any_backend or (available and name != "clipboard")
        _check(name, available, detail)
    print(f"  → plan: {injector.plan().describe()}")
    if not any_backend:
        if session.is_wayland:
            problems.append("sudo apt install wtype ydotool wl-clipboard   # Wayland text insertion")
        else:
            problems.append("sudo apt install xdotool xclip   # X11 text insertion")
    print()

    print("Hotkey")
    if config.hotkey.mode == "hold":
        readable = env.readable_input_devices()
        ok = _check(f"readable input devices: {len(readable)}", bool(readable),
                    "" if readable else "hold-to-talk needs membership in the 'input' group")
        if not ok:
            problems.append("sudo usermod -aG input $USER   # then log out and back in")
        try:
            from .hotkey import parse_combo

            parse_combo(config.hotkey.combo)
            _check(f"combination '{config.hotkey.combo}' is valid", True)
        except Exception as exc:
            _check(f"combination '{config.hotkey.combo}'", False, str(exc))
            problems.append("fix hotkey.combo in " + str(paths.config_file()))
    else:
        _check(f"KDE shortcut '{config.hotkey.kde_shortcut}' → local-whisper toggle", True,
               "register it with `local-whisper install-shortcut`")
    ok, detail = env.can_use_uinput()
    _check("/dev/uinput writable (built-in typing backend)", ok, detail)
    if not ok and session.is_wayland:
        problems.append(
            "sudo cp packaging/udev/99-local-whisper-uinput.rules /etc/udev/rules.d/ && "
            "sudo udevadm control --reload && sudo modprobe uinput   # optional: built-in typing"
        )
    print()

    if problems:
        print("Suggested fixes")
        for problem in dict.fromkeys(problems):
            print(f"  $ {problem}")
        return 1
    print("Everything checks out.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
