"""System tray icon — the app's only permanent piece of UI.

Plasma shows this in the system tray; the icon colour follows the dictation
state so a glance tells you whether the microphone is live.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_TITLE
from ..state import State
from .theme import Palette, tray_icon


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        palette: Palette,
        on_toggle: Callable[[], None],
        on_settings: Callable[[], None],
        on_history: Callable[[], None],
        on_pause: Callable[[bool], None],
        on_insert_last: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__()
        self.palette_ = palette
        self.setIcon(tray_icon(palette, "idle"))
        self.setToolTip(f"{APP_TITLE} — ready")

        menu = QMenu()
        self.status_action = QAction("Ready", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()

        self.toggle_action = QAction("Start dictation", menu)
        self.toggle_action.triggered.connect(lambda: on_toggle())
        menu.addAction(self.toggle_action)

        insert_last = QAction("Insert last transcript", menu)
        insert_last.triggered.connect(lambda: on_insert_last())
        menu.addAction(insert_last)

        self.pause_action = QAction("Pause dictation", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(lambda checked: on_pause(checked))
        menu.addAction(self.pause_action)
        menu.addSeparator()

        settings = QAction("Settings…", menu)
        settings.triggered.connect(lambda: on_settings())
        menu.addAction(settings)

        history = QAction("History…", menu)
        history.triggered.connect(lambda: on_history())
        menu.addAction(history)
        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda: on_quit())
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        # A left click is the fastest way to start talking without a keyboard.
        self.activated.connect(
            lambda reason: on_toggle() if reason == QSystemTrayIcon.Trigger else None
        )

    def update_state(self, state: State, detail: str = "") -> None:
        self.setIcon(tray_icon(self.palette_, state.value))
        label = detail or state.label
        self.status_action.setText(label if len(label) < 60 else label[:57] + "…")
        self.setToolTip(f"{APP_TITLE} — {label}")
        self.toggle_action.setText(
            "Stop dictation" if state == State.RECORDING else "Start dictation"
        )
