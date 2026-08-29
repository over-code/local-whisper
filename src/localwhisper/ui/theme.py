"""One palette, one stylesheet, and the icons — all generated in code.

Keeping the visuals in a single module means the overlay, the tray and the
settings window cannot drift apart, and shipping no image files keeps the
package a pure-Python install.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)


class Palette:
    """Dark, slightly translucent surfaces with one accent colour."""

    def __init__(self, accent: str = "#7C5CFF") -> None:
        self.accent = QColor(accent)
        self.accent_soft = QColor(accent)
        self.accent_soft.setAlpha(60)
        self.background = QColor("#15161C")
        self.surface = QColor("#1D1F27")
        self.surface_alt = QColor("#252833")
        self.border = QColor("#33374A")
        self.text = QColor("#ECEDF3")
        self.text_dim = QColor("#9BA0B4")
        self.recording = QColor("#FF5D6C")
        self.success = QColor("#4ADE9B")
        self.warning = QColor("#FFC85C")

    def state_color(self, state: str) -> QColor:
        return {
            "recording": self.recording,
            "transcribing": self.accent,
            "inserting": self.accent,
            "loading": self.warning,
            "done": self.success,
            "error": self.recording,
            "paused": self.text_dim,
        }.get(state, self.accent)


def stylesheet(palette: Palette) -> str:
    accent = palette.accent.name()
    return f"""
    QWidget {{
        background: {palette.background.name()};
        color: {palette.text.name()};
        font-size: 13px;
    }}
    QLabel, QCheckBox, QRadioButton, QGroupBox::title {{ background: transparent; }}
    QLabel[role="title"] {{ font-size: 20px; font-weight: 600; }}
    QLabel[role="subtitle"] {{ color: {palette.text_dim.name()}; }}
    QLabel[role="section"] {{ font-size: 14px; font-weight: 600; padding-top: 6px; }}

    QTabWidget::pane {{
        border: 1px solid {palette.border.name()};
        border-radius: 10px;
        background: {palette.surface.name()};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {palette.text_dim.name()};
        padding: 8px 16px;
        margin-right: 4px;
        border-radius: 8px;
    }}
    QTabBar::tab:selected {{
        background: {palette.surface_alt.name()};
        color: {palette.text.name()};
    }}
    QTabBar::tab:hover {{ color: {palette.text.name()}; }}

    QGroupBox {{
        border: 1px solid {palette.border.name()};
        border-radius: 10px;
        margin-top: 18px;
        padding: 12px;
        background: {palette.surface.name()};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {palette.text_dim.name()};
        font-weight: 600;
    }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QPushButton {{
        background: {palette.surface_alt.name()};
        border: 1px solid {palette.border.name()};
        border-radius: 8px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ border-color: {accent}; }}
    QPushButton:pressed {{ background: {palette.border.name()}; }}
    QPushButton[role="primary"] {{
        background: {accent};
        border: none;
        color: #FFFFFF;
        font-weight: 600;
    }}
    QPushButton[role="primary"]:hover {{ background: {palette.accent.lighter(115).name()}; }}
    QPushButton[role="danger"] {{ color: {palette.recording.name()}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {palette.surface_alt.name()};
        border: 1px solid {palette.border.name()};
        border-radius: 8px;
        padding: 6px 8px;
        min-height: 20px;
        selection-background-color: {accent};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus {{ border-color: {accent}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QSpinBox, QDoubleSpinBox {{ padding-right: 18px; min-width: 110px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 16px;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-bottom: 5px solid {palette.text_dim.name()}; width: 0; height: 0;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-top: 5px solid {palette.text_dim.name()}; width: 0; height: 0;
    }}
    QComboBox QAbstractItemView {{
        background: {palette.surface_alt.name()};
        border: 1px solid {palette.border.name()};
        selection-background-color: {accent};
        outline: none;
    }}

    QCheckBox {{ padding: 2px 0; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {palette.border.name()};
        border-radius: 5px;
        background: {palette.surface_alt.name()};
    }}
    QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}

    QTableWidget, QListWidget {{
        background: {palette.surface.name()};
        border: 1px solid {palette.border.name()};
        border-radius: 10px;
        gridline-color: {palette.border.name()};
        selection-background-color: {palette.accent_soft.name(QColor.HexArgb)};
        outline: none;
    }}
    QHeaderView::section {{
        background: {palette.surface_alt.name()};
        border: none;
        border-bottom: 1px solid {palette.border.name()};
        padding: 6px;
        color: {palette.text_dim.name()};
        font-weight: 600;
    }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {palette.border.name()};
        border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QSlider::groove:horizontal {{
        height: 4px; background: {palette.border.name()}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {accent}; width: 14px; margin: -6px 0; border-radius: 7px;
    }}
    QProgressBar {{
        border: none; border-radius: 4px; height: 6px;
        background: {palette.border.name()}; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
    QToolTip {{
        background: {palette.surface_alt.name()};
        color: {palette.text.name()};
        border: 1px solid {palette.border.name()};
        padding: 4px;
    }}
    """


# ----------------------------------------------------------------- icon maker

def microphone_pixmap(size: int, color: QColor, background: QColor | None = None) -> QPixmap:
    """A microphone glyph, drawn rather than loaded, so it scales to any tray."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    if background is not None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, background.lighter(120))
        gradient.setColorAt(1.0, background)
        painter.fillPath(path, QBrush(gradient))

    unit = size / 24.0
    pen = QPen(color, 2.0 * unit, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QBrush(color))
    # Capsule
    capsule = QRectF(9 * unit, 3.5 * unit, 6 * unit, 10 * unit)
    painter.drawRoundedRect(capsule, 3 * unit, 3 * unit)
    # Cradle + stand
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(6 * unit, 8 * unit, 12 * unit, 10 * unit), 180 * 16, 180 * 16)
    painter.drawLine(int(12 * unit), int(17 * unit), int(12 * unit), int(20 * unit))
    painter.drawLine(int(8.5 * unit), int(20 * unit), int(15.5 * unit), int(20 * unit))
    painter.end()
    return pixmap


def app_icon(palette: Palette) -> QIcon:
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128):
        icon.addPixmap(microphone_pixmap(size, QColor("#FFFFFF"), palette.accent))
    return icon


def tray_icon(palette: Palette, state: str = "idle") -> QIcon:
    color = QColor("#D8DAE6") if state in ("idle", "") else palette.state_color(state)
    icon = QIcon()
    for size in (16, 22, 24, 32, 48):
        icon.addPixmap(microphone_pixmap(size, color))
    return icon


def title_font(size: int = 16, weight: int = QFont.DemiBold) -> QFont:
    font = QFont()
    font.setPointSize(size)
    font.setWeight(QFont.Weight(weight))
    return font


DEFAULT_ICON_SIZE = QSize(22, 22)
