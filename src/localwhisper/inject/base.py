"""Shared plumbing for the text-injection backends."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from ..logging_setup import get

log = get("inject")


class InjectionError(RuntimeError):
    """Raised when a backend was tried and did not work."""


@dataclass
class Capability:
    """What one backend can do here, and why not, when it cannot."""

    name: str
    available: bool
    can_type: bool = False
    can_key: bool = False
    can_click: bool = False
    detail: str = ""


def run(cmd: list[str], *, stdin: bytes | None = None, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a helper binary, raising :class:`InjectionError` on any failure.

    Never uses a shell: dictated text goes in as an argv entry or on stdin, so
    a transcript containing ``$(rm -rf ~)`` is just text.
    """
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise InjectionError(f"{cmd[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise InjectionError(f"{cmd[0]} timed out") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise InjectionError(f"{cmd[0]} failed ({proc.returncode}): {stderr[:200]}")
    return proc


class Backend:
    """Base class for everything that can put keystrokes into the session."""

    name = "base"
    #: Higher wins when several backends are usable.
    priority = 0

    def capability(self) -> Capability:
        raise NotImplementedError

    def type_text(self, text: str, delay_ms: int = 4) -> None:
        raise InjectionError(f"{self.name} cannot type text")

    def send_paste(self) -> None:
        raise InjectionError(f"{self.name} cannot send key combinations")

    def click(self, button: int = 1) -> None:
        raise InjectionError(f"{self.name} cannot click")
