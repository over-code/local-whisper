"""Getting the transcript into the application you are looking at.

The injector picks a strategy per session and degrades honestly: if nothing can
synthesise keystrokes (a locked-down Wayland session with no helpers
installed), the text still lands on the clipboard and the UI says "press
Ctrl+V" instead of silently doing nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .. import env
from ..config import InsertConfig
from ..logging_setup import get
from . import clipboard
from .backends import ALL_BACKENDS, UinputBackend
from .base import Backend, Capability, InjectionError

log = get("inject")

__all__ = ["TextInjector", "InsertReport", "InjectionError", "clipboard"]


@dataclass
class InsertReport:
    ok: bool
    #: "type", "paste" or "clipboard"
    method: str = ""
    backend: str = ""
    message: str = ""
    copied: bool = False
    elapsed: float = 0.0


@dataclass
class Plan:
    """What the injector intends to do, given the session and the config."""

    method: str
    backend: Backend | None
    fallback: str
    capabilities: list[Capability] = field(default_factory=list)

    def describe(self) -> str:
        if self.backend is None:
            return "clipboard only — no keystroke backend available"
        return f"{self.method} via {self.backend.name}"


class TextInjector:
    def __init__(self, config: InsertConfig) -> None:
        self.config = config
        self._backends: list[Backend] = [cls() for cls in ALL_BACKENDS]
        self._plan: Plan | None = None

    # ------------------------------------------------------------ detection

    def capabilities(self) -> list[Capability]:
        return [backend.capability() for backend in self._backends]

    def invalidate(self) -> None:
        """Forget the cached plan (after a config change or a tool install)."""
        self._plan = None
        # Re-probe for tools the user may have installed since we last looked.
        clear = getattr(env.has_tool, "cache_clear", None)
        if clear is not None:
            clear()

    def plan(self) -> Plan:
        if self._plan is not None:
            return self._plan

        capabilities = self.capabilities()
        usable = [
            backend
            for backend, capability in zip(self._backends, capabilities)
            if capability.available
        ]
        preferred = self.config.backend
        if preferred and preferred != "auto":
            usable = [b for b in usable if b.name == preferred] or usable
        usable.sort(key=lambda b: b.priority, reverse=True)

        method = self.config.method
        backend = usable[0] if usable else None

        if method == "auto":
            if backend is None:
                method = "clipboard"
            elif backend.name in ("uinput",):
                # ASCII-only typing: paste is strictly better here.
                method = "paste"
            elif backend.name == "ydotool":
                method = "paste"
            else:
                method = "type"
        if method in ("type", "paste") and backend is None:
            method = "clipboard"

        self._plan = Plan(
            method=method,
            backend=backend,
            fallback="clipboard",
            capabilities=capabilities,
        )
        log.info("insertion plan: %s (session: %s)", self._plan.describe(), env.session().describe())
        return self._plan

    # ------------------------------------------------------------- insertion

    def insert(self, text: str) -> InsertReport:
        """Put ``text`` where the caret is. Never raises."""
        if not text:
            return InsertReport(ok=True, method="noop", message="nothing to insert")

        started = time.monotonic()
        plan = self.plan()
        copied = False

        if self.config.click_to_focus:
            self._click_to_focus(plan)

        if self.config.pre_insert_delay > 0:
            time.sleep(self.config.pre_insert_delay)

        # 1) Try the planned method.
        if plan.method == "type" and plan.backend is not None:
            try:
                plan.backend.type_text(text, self.config.type_delay_ms)
                if self.config.always_copy:
                    copied = self._copy_quietly(text)
                return InsertReport(True, "type", plan.backend.name, copied=copied,
                                    elapsed=time.monotonic() - started)
            except InjectionError as exc:
                log.warning("typing via %s failed: %s — falling back to paste", plan.backend.name, exc)

        # 2) Paste: clipboard + Ctrl+V, restoring the old clipboard afterwards.
        if plan.backend is not None and plan.method in ("type", "paste"):
            report = self._paste(text, plan)
            report.elapsed = time.monotonic() - started
            if report.ok:
                return report

        # 3) Clipboard only — the user finishes with Ctrl+V.
        try:
            backend_name = clipboard.set_text(text)
            return InsertReport(
                ok=True,
                method="clipboard",
                backend=backend_name,
                message="Copied to clipboard — press Ctrl+V to insert",
                copied=True,
                elapsed=time.monotonic() - started,
            )
        except InjectionError as exc:
            return InsertReport(
                ok=False,
                method="clipboard",
                message=f"Could not insert or copy the text: {exc}",
                elapsed=time.monotonic() - started,
            )

    def _paste(self, text: str, plan: Plan) -> InsertReport:
        previous = ""
        if self.config.restore_clipboard:
            try:
                previous = clipboard.get_text()
            except Exception:  # pragma: no cover
                previous = ""
        try:
            backend_name = clipboard.set_text(text)
        except InjectionError as exc:
            return InsertReport(False, "paste", message=str(exc))

        assert plan.backend is not None
        try:
            if isinstance(plan.backend, UinputBackend):
                # A push-to-talk hotkey may still be physically held down.
                plan.backend.release_modifiers()
            plan.backend.send_paste()
        except InjectionError as exc:
            log.warning("paste via %s failed: %s", plan.backend.name, exc)
            return InsertReport(False, "paste", plan.backend.name, str(exc), copied=True)

        if self.config.restore_clipboard and previous and previous != text:
            _restore_clipboard_later(previous)
        return InsertReport(True, "paste", f"{plan.backend.name}+{backend_name}", copied=True)

    def _click_to_focus(self, plan: Plan) -> None:
        """Click where the pointer is, so the field under the mouse takes focus."""
        for backend in sorted(self._backends, key=lambda b: b.priority, reverse=True):
            capability = backend.capability()
            if capability.available and capability.can_click:
                try:
                    backend.click(1)
                    time.sleep(0.05)
                    return
                except InjectionError as exc:
                    log.debug("click via %s failed: %s", backend.name, exc)
        log.debug("no backend can click; relying on the existing focus")

    def _copy_quietly(self, text: str) -> bool:
        try:
            clipboard.set_text(text)
            return True
        except InjectionError:
            return False

    # ------------------------------------------------------------ diagnostics

    def diagnostics(self) -> list[tuple[str, bool, str]]:
        rows = [(c.name, c.available, c.detail) for c in self.capabilities()]
        rows.append(("clipboard", clipboard.available(), "wl-copy / xclip / xsel / Qt"))
        return rows


def _restore_clipboard_later(previous: str, delay: float = 0.6) -> None:
    """Give the target app time to read the clipboard, then put the old value back."""
    import threading

    def worker() -> None:
        time.sleep(delay)
        try:
            clipboard.set_text(previous)
        except Exception as exc:  # pragma: no cover
            log.debug("clipboard restore failed: %s", exc)

    threading.Thread(target=worker, name="lw-clipboard-restore", daemon=True).start()
