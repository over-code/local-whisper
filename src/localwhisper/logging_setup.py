"""Logging that is useful in a terminal and survives as a rotating file."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from . import paths

_CONFIGURED = False


def setup(level: int = logging.INFO, quiet: bool = False) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("localwhisper")
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    if not quiet:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)

    try:
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            paths.log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        rotating.setFormatter(fmt)
        logger.addHandler(rotating)
    except OSError:
        pass  # a read-only home should not stop dictation from working

    logger.propagate = False
    _CONFIGURED = True
    return logger


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"localwhisper.{name}")
