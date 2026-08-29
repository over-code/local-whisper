"""XDG base directory helpers.

Everything the app writes lives under the user's XDG directories, so a purge is
``rm -rf ~/.config/local-whisper ~/.local/share/local-whisper ~/.cache/local-whisper``.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import APP_NAME


def _xdg(env: str, default: str) -> Path:
    value = os.environ.get(env)
    base = Path(value) if value else Path.home() / default
    return base / APP_NAME


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def runtime_dir() -> Path:
    """Directory for the IPC socket and pid file.

    ``XDG_RUNTIME_DIR`` is per-user and tmpfs-backed, which is exactly what a
    socket wants; we fall back to the cache dir when it is unset (some minimal
    sessions, cron jobs).
    """
    value = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(value) / APP_NAME if value else cache_dir() / "run"
    return base


def config_file() -> Path:
    return config_dir() / "config.toml"


def history_db() -> Path:
    return data_dir() / "history.sqlite3"


def log_file() -> Path:
    return state_dir() / "local-whisper.log"


def socket_path() -> Path:
    return runtime_dir() / "daemon.sock"


def models_dir() -> Path:
    """Where faster-whisper (huggingface_hub) caches converted models."""
    return cache_dir() / "models"


def ensure_dirs() -> None:
    for path in (config_dir(), data_dir(), cache_dir(), state_dir(), runtime_dir(), models_dir()):
        path.mkdir(parents=True, exist_ok=True)
    # The socket lives here; keep it private to the user.
    try:
        runtime_dir().chmod(0o700)
    except OSError:
        pass
