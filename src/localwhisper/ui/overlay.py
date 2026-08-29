"""The floating status pill.

Dictation is a modal thing — while you hold the key, you need to know the
microphone is live, that the level is moving, and when the text is on its way.
The pill answers all three at a glance and then gets out of the way.

Two constraints shape the implementation:

* It must never take keyboard focus, or the text field you are dictating into
  would lose the caret. Hence ``WindowDoesNotAcceptFocus`` + ``WA_ShowWithoutActivating``.
* It must not force a repaint storm. The animation timer only runs while the
  pill is visible.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from ..config import UIConfig
from ..state import State
from .theme import Palette

BAR_COUNT = 34


class Overlay(QWidget):
    """Frameless, always-on-top status pill with a live level meter."""

    def __init__(self, palette: Palette, config: UIConfig) -> None:
        super().__init__(None)
        self.palette_ = palette
        self.config = config

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Local Whisper")

        self._state = State.IDLE
        self._detail = ""
        self._levels: deque[float] = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
        self._level = 0.0
        self._started_at = 0.0
        self._phase = 0.0
        self._result_text = ""

        self.resize(360, 68)

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps while visible
        self._timer.timeout.connect(self._tick)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.fade_out)

    # -------------------------------------------------------------- external

    def set_state(self, state: State, detail: str = "") -> None:
        previous, self._state = self._state, state
        self._detail = detail

        if state == State.RECORDING:
            self._started_at = time.monotonic()
            self._levels = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
            self._result_text = ""
            self.show_pill()
        elif state in (State.TRANSCRIBING, State.INSERTING, State.LOADING):
            self.show_pill()
        elif state == State.DONE:
            self._result_text = detail if self.config.show_result_preview else ""
            self.show_pill()
            self._hide_timer.start(max(400, self.config.result_preview_ms))
        elif state == State.ERROR:
            self.show_pill()
            self._hide_timer.start(3200)
        elif state in (State.IDLE, State.PAUSED):
            if previous == State.RECORDING:
                self._hide_timer.start(180)
            else:
                self._hide_timer.start(120)
        self.update()

    def set_level(self, level: float) -> None:
        # Smooth the meter: raw RMS jitters at 30 Hz and looks noisy.
        self._level = self._level * 0.55 + level * 0.45
        self._levels.append(self._level)

    # ---------------------------------------------------------- show / place

    def show_pill(self) -> None:
        if not self.config.show_overlay:
            return
        self._hide_timer.stop()
        self.reposition()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        if not self._timer.isActive():
            self._timer.start()

    def fade_out(self) -> None:
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        """Only a fade *out* ends with the widget hidden."""
        if float(self._fade.endValue() or 0.0) <= 0.01:
            self._timer.stop()
            self.hide()

    def reposition(self) -> None:
        """Put the pill on the screen the pointer is on.

        On X11 this is exact. On Wayland a client cannot choose its own
        position, so the compositor decides — see docs/wayland.md for the KWin
        rule that pins it where you want it.
        """
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = max(0, self.config.overlay_margin)

        if self.config.overlay_position == "cursor":
            point = QCursor.pos()
            x = point.x() - self.width() // 2
            y = point.y() + 28
        elif self.config.overlay_position == "top":
            x = area.center().x() - self.width() // 2
            y = area.top() + margin
        else:
            x = area.center().x() - self.width() // 2
            y = area.bottom() - self.height() - margin

        x = max(area.left() + 8, min(x, area.right() - self.width() - 8))
        y = max(area.top() + 8, min(y, area.bottom() - self.height() - 8))
        self.move(QPoint(int(x), int(y)))

    # ------------------------------------------------------------- animation

    def _tick(self) -> None:
        self._phase += 0.06
        if self._state in (State.TRANSCRIBING, State.INSERTING, State.LOADING):
            # No microphone input to show, so animate a travelling wave instead.
            self._levels.append(0.0)
        self.update()

    # ------------------------------------------------------------- rendering

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        radius = rect.height() / 2
        accent = self.palette_.state_color(self._state.value)

        self._paint_glow(painter, rect, radius, accent)
        self._paint_body(painter, rect, radius, accent)

        content = rect.adjusted(14, 0, -16, 0)
        dot_center = QRectF(content.left(), content.center().y() - 5, 10, 10)
        self._paint_indicator(painter, dot_center, accent)

        text_left = content.left() + 24
        if self._state == State.RECORDING:
            self._paint_wave(painter, QRectF(text_left, content.top() + 18, content.width() - 96, 32), accent)
            self._paint_timer(painter, content)
        elif self._state in (State.TRANSCRIBING, State.INSERTING, State.LOADING):
            self._paint_progress(painter, QRectF(text_left, content.center().y() + 8, content.width() - 30, 4), accent)
            self._paint_label(painter, QRectF(text_left, content.top() + 12, content.width() - 30, 20),
                              self._status_text())
        else:
            self._paint_label(painter, QRectF(text_left, content.top() + 12, content.width() - 30, 24),
                              self._status_text())
        painter.end()

    def _paint_glow(self, painter: QPainter, rect: QRectF, radius: float, accent: QColor) -> None:
        """A soft halo instead of a drop shadow — cheaper and compositor-safe."""
        glow = QColor(accent)
        for step in range(5, 0, -1):
            glow.setAlpha(6 * step if self._state == State.RECORDING else 3 * step)
            painter.setPen(QPen(glow, step * 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-step, -step, step, step), radius + step, radius + step)

    def _paint_body(self, painter: QPainter, rect: QRectF, radius: float, accent: QColor) -> None:
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        top = QColor(self.palette_.surface)
        top.setAlpha(242)
        bottom = QColor(self.palette_.background)
        bottom.setAlpha(242)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, QBrush(gradient))

        border = QColor(accent)
        border.setAlpha(120 if self._state == State.RECORDING else 70)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)

    def _paint_indicator(self, painter: QPainter, dot: QRectF, accent: QColor) -> None:
        if self._state == State.RECORDING:
            # Pulse in time with a 1.4 s breath.
            import math

            pulse = (math.sin(self._phase * 2.2) + 1) / 2
            halo = QColor(accent)
            halo.setAlpha(int(40 + 60 * pulse))
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            grow = 4 + 3 * pulse
            painter.drawEllipse(dot.adjusted(-grow, -grow, grow, grow))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(dot)

    def _paint_wave(self, painter: QPainter, area: QRectF, accent: QColor) -> None:
        import math

        painter.setPen(Qt.NoPen)
        count = len(self._levels)
        if count == 0:
            return
        spacing = area.width() / count
        width = max(2.0, spacing * 0.55)
        for index, level in enumerate(self._levels):
            # Older samples fade towards the left, newest bar is fully lit.
            age = index / max(1, count - 1)
            height = max(3.0, min(area.height(), level * area.height() * 1.8))
            x = area.left() + index * spacing
            y = area.center().y() - height / 2
            color = QColor(accent)
            color.setAlpha(int(70 + 165 * age))
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, y, width, height), width / 2, width / 2)
        # A faint idle ripple so the pill never looks frozen in a silent room.
        if max(self._levels) < 0.02:
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 70))
            for index in range(count):
                ripple = 3.0 + 2.0 * math.sin(self._phase * 3 + index * 0.4)
                x = area.left() + index * spacing
                painter.drawRoundedRect(
                    QRectF(x, area.center().y() - ripple / 2, width, ripple), width / 2, width / 2
                )

    def _paint_progress(self, painter: QPainter, area: QRectF, accent: QColor) -> None:
        import math

        painter.setPen(Qt.NoPen)
        track = QColor(self.palette_.border)
        track.setAlpha(140)
        painter.setBrush(track)
        painter.drawRoundedRect(area, 2, 2)

        span = area.width() * 0.32
        position = (math.sin(self._phase * 1.6) + 1) / 2 * (area.width() - span)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(area.left() + position, area.top(), span, area.height()), 2, 2)

    def _paint_label(self, painter: QPainter, area: QRectF, text: str) -> None:
        font = QFont()
        font.setPointSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QPen(self.palette_.text))
        metrics = QFontMetrics(font)
        elided = metrics.elidedText(text, Qt.ElideRight, int(area.width()))
        painter.drawText(area, Qt.AlignLeft | Qt.AlignVCenter, elided)

    def _paint_timer(self, painter: QPainter, content: QRectF) -> None:
        elapsed = time.monotonic() - self._started_at if self._started_at else 0.0
        font = QFont()
        font.setPointSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QPen(self.palette_.text_dim))
        area = QRectF(content.right() - 62, content.center().y() - 10, 62, 20)
        painter.drawText(area, Qt.AlignRight | Qt.AlignVCenter, _format_duration(elapsed))

        label = QFont()
        label.setPointSize(9)
        painter.setFont(label)
        painter.setPen(QPen(self.palette_.text))
        painter.drawText(
            QRectF(content.left() + 24, content.top() + 2, content.width() - 90, 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._detail or "Listening…",
        )

    def _status_text(self) -> str:
        if self._state == State.DONE and self._result_text:
            return f"✓  {self._result_text}"
        if self._state == State.ERROR:
            return f"⚠  {self._detail}" if self._detail else "⚠  Something went wrong"
        if self._detail and self._state != State.IDLE:
            return self._detail
        return self._state.label + "…"


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    tenths = int((seconds - int(seconds)) * 10)
    return f"{minutes}:{secs:02d}.{tenths}"
