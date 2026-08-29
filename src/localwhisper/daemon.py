"""The resident application: tray icon, overlay, settings window and IPC.

Started by ``local-whisper daemon`` (or the systemd user service). Everything
else in the CLI is a one-line client that pokes this process.
"""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import APP_NAME, APP_TITLE, ipc, paths
from .app import DictationController
from .config import Config
from .logging_setup import get
from .state import State
from .ui.overlay import Overlay
from .ui.settings import SettingsWindow
from .ui.theme import Palette, app_icon, stylesheet
from .ui.tray import Tray

log = get("daemon")


class DictationDaemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.palette = Palette(config.ui.accent)

        self.qt = QApplication.instance() or QApplication(sys.argv)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setApplicationDisplayName(APP_TITLE)
        self.qt.setDesktopFileName("local-whisper")
        self.qt.setWindowIcon(app_icon(self.palette))
        self.qt.setStyleSheet(stylesheet(self.palette))
        # Closing the settings window must not end the session.
        self.qt.setQuitOnLastWindowClosed(False)

        self.controller = DictationController(config)
        self.overlay = Overlay(self.palette, config.ui)
        self.settings: SettingsWindow | None = None
        self.tray = Tray(
            self.palette,
            on_toggle=self.controller.toggle,
            on_settings=self.show_settings,
            on_history=self.show_history,
            on_pause=self.controller.set_paused,
            on_insert_last=self.controller.insert_last,
            on_quit=self.quit,
        )
        self.server = ipc.Server(self._handle_ipc)

        self._connect()

    # ---------------------------------------------------------------- wiring

    def _connect(self) -> None:
        controller = self.controller
        controller.stateChanged.connect(self._on_state)
        controller.levelChanged.connect(self.overlay.set_level)
        controller.errorRaised.connect(self._on_error)
        controller.modelStatus.connect(self._on_model_status)
        controller._commandReceived.connect(self._on_command)

    def _on_state(self, state: State, detail: str) -> None:
        self.overlay.set_state(state, detail)
        self.tray.update_state(state, detail)
        if self.settings is not None:
            self.settings.on_state(state, detail)

    def _on_error(self, message: str) -> None:
        log.error("%s", message)
        if self.config.ui.notify_errors and self.tray.isSystemTrayAvailable():
            self.tray.showMessage(APP_TITLE, message, QSystemTrayIcon.Warning, 5000)

    def _on_model_status(self, message: str) -> None:
        log.info("model: %s", message)
        if self.settings is not None:
            self.settings.on_model_status(message)

    def _on_command(self, command: str, payload: object) -> None:
        """Commands that need the windows, not just the controller."""
        if command == "settings":
            self.show_settings()
        elif command == "quit":
            self.quit()

    def _handle_ipc(self, command: str, payload: dict) -> dict:
        # Runs on the IPC thread — the controller marshals anything that
        # touches Qt objects onto the GUI thread for us.
        return self.controller.handle_ipc(command, payload)

    # --------------------------------------------------------------- windows

    def show_settings(self, tab: int = 0) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self.controller.config, self.palette, self.controller)
            self.settings.configSaved.connect(self._on_config_saved)
            self.settings.reinsertRequested.connect(self.controller.insert_text)
            self.controller.levelChanged.connect(self.settings.on_level)
        self.settings.tabs.setCurrentIndex(tab)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()
        self.settings.refresh_history()

    def show_history(self) -> None:
        self.show_settings(tab=5)

    def _on_config_saved(self, config: Config) -> None:
        self.config = config
        self.palette = Palette(config.ui.accent)
        self.overlay.config = config.ui
        self.overlay.palette_ = self.palette
        self.tray.palette_ = self.palette
        self.qt.setStyleSheet(stylesheet(self.palette))
        self.controller.reload_config(config)
        if self.tray.isSystemTrayAvailable():
            self.tray.showMessage(APP_TITLE, "Settings saved", QSystemTrayIcon.Information, 2000)

    # ------------------------------------------------------------- lifecycle

    def run(self) -> int:
        try:
            self.server.start()
        except ipc.ServerAlreadyRunning:
            QMessageBox.information(
                None, APP_TITLE,
                "Local Whisper is already running — look for the microphone in your system tray.",
            )
            return 1

        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("no system tray available; the overlay and hotkeys still work")
        self.tray.show()
        self.controller.start()

        status = self.controller.hotkeys.status
        if self.tray.isSystemTrayAvailable():
            self.tray.showMessage(
                APP_TITLE, f"Ready — {status.detail}", QSystemTrayIcon.Information, 3500
            )
        if not self.config.ui.start_hidden:
            self.show_settings()

        self._install_signal_handlers()
        log.info("%s %s is running", APP_TITLE, _version())
        try:
            return self.qt.exec()
        finally:
            self.shutdown()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.quit())
        # Qt's event loop blocks in C++, so Python signal handlers only run
        # when the interpreter gets a slice: this timer provides one.
        keepalive = QTimer(self.qt)
        keepalive.start(250)
        keepalive.timeout.connect(lambda: None)
        self._keepalive = keepalive

    def quit(self) -> None:
        log.info("shutting down")
        self.qt.quit()

    def shutdown(self) -> None:
        self.server.stop()
        self.controller.shutdown()
        self.overlay.hide()
        self.tray.hide()


def _version() -> str:
    from . import __version__

    return __version__


def run(config: Config | None = None) -> int:
    paths.ensure_dirs()
    config = config or Config.load()
    # Qt6 handles fractional scaling itself; this only affects our pixmaps.
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True) if hasattr(Qt, "AA_UseHighDpiPixmaps") else None
    daemon = DictationDaemon(config)
    return daemon.run()
