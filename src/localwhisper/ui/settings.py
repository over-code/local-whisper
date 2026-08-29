"""The settings window.

Six tabs, one job each, and every control writes straight into a
:class:`~localwhisper.config.Config` copy that is only committed when you press
Save — at which point the daemon reloads it live, without dropping the model.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_TITLE, __version__
from ..audio import list_input_devices
from ..config import Config
from ..env import session
from ..hotkey import kde
from ..logging_setup import get
from ..state import State
from ..stt.engine import MODELS
from .theme import Palette, app_icon, stylesheet

log = get("ui.settings")

LANGUAGES = [
    ("Auto-detect", ""), ("English", "en"), ("German", "de"), ("Spanish", "es"),
    ("French", "fr"), ("Italian", "it"), ("Portuguese", "pt"), ("Dutch", "nl"),
    ("Polish", "pl"), ("Russian", "ru"), ("Turkish", "tr"), ("Japanese", "ja"),
    ("Chinese", "zh"), ("Korean", "ko"), ("Hindi", "hi"), ("Arabic", "ar"),
]


class SettingsWindow(QDialog):
    """Non-modal settings dialog. Emits :attr:`configSaved` on Save."""

    configSaved = Signal(object)     # Config
    reinsertRequested = Signal(str)  # text from the history tab

    def __init__(self, config: Config, palette: Palette, controller=None) -> None:
        super().__init__(None)
        self.config = copy.deepcopy(config)
        self.palette_ = palette
        self.controller = controller

        self.setWindowTitle(f"{APP_TITLE} — Settings")
        self.setWindowIcon(app_icon(palette))
        self.setStyleSheet(stylesheet(palette))
        self.setMinimumSize(780, 700)
        self.resize(820, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        layout.addLayout(self._build_header())

        self.tabs = QTabWidget()
        # Every tab scrolls: a small screen or a large font must never clip a control.
        self.tabs.addTab(_scrollable(self._build_dictation_tab()), "Dictation")
        self.tabs.addTab(_scrollable(self._build_model_tab()), "Model")
        self.tabs.addTab(_scrollable(self._build_audio_tab()), "Audio")
        self.tabs.addTab(_scrollable(self._build_insertion_tab()), "Insertion")
        self.tabs.addTab(_scrollable(self._build_text_tab()), "Text")
        self.tabs.addTab(self._build_history_tab(), "History")
        layout.addWidget(self.tabs, 1)

        layout.addLayout(self._build_footer())
        self._load_into_widgets()

    # ---------------------------------------------------------------- header

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(app_icon(self.palette_).pixmap(44, 44))
        row.addWidget(icon)

        text = QVBoxLayout()
        title = QLabel(APP_TITLE)
        title.setProperty("role", "title")
        subtitle = QLabel(f"Local dictation · v{__version__} · {session().describe()}")
        subtitle.setProperty("role", "subtitle")
        text.addWidget(title)
        text.addWidget(subtitle)
        row.addLayout(text)
        row.addStretch(1)

        self.status_label = QLabel("Ready")
        self.status_label.setProperty("role", "subtitle")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.status_label)
        return row

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.footer_hint = QLabel("")
        self.footer_hint.setProperty("role", "subtitle")
        row.addWidget(self.footer_hint, 1)

        test = QPushButton("Test dictation")
        test.setToolTip("Start a dictation right now — handy for checking the microphone")
        test.clicked.connect(self._test_dictation)
        row.addWidget(test)

        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        row.addWidget(close)

        save = QPushButton("Save")
        save.setProperty("role", "primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(save)
        return row

    # ------------------------------------------------------------- tab: keys

    def _build_dictation_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        box = QGroupBox("Hotkey")
        form = _form(box)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Tap to start, tap to stop (works everywhere)", "toggle")
        self.mode_combo.addItem("Hold to talk (needs input-device access)", "hold")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Style", self.mode_combo)

        self.kde_shortcut = QLineEdit()
        self.kde_shortcut.setPlaceholderText("Meta+Alt+D")
        form.addRow("Desktop shortcut", self.kde_shortcut)

        self.kde_cancel_shortcut = QLineEdit()
        self.kde_cancel_shortcut.setPlaceholderText("Meta+Alt+X")
        form.addRow("Cancel shortcut", self.kde_cancel_shortcut)

        install_row = QHBoxLayout()
        install = QPushButton("Register with KDE")
        install.setProperty("role", "primary")
        install.clicked.connect(self._install_kde_shortcuts)
        install_row.addWidget(install)
        manual = QPushButton("Show manual steps")
        manual.clicked.connect(self._show_manual_steps)
        install_row.addWidget(manual)
        install_row.addStretch(1)
        form.addRow(install_row)

        self.combo_edit = QLineEdit()
        self.combo_edit.setPlaceholderText("super+alt")
        self.combo_edit.setToolTip(
            "Push-to-talk combination, read directly from the keyboard.\n"
            "Examples: super+alt · ctrl+alt+space · f9"
        )
        form.addRow("Hold-to-talk keys", self.combo_edit)

        self.double_tap = QCheckBox("Double-tap the hold keys to latch hands-free mode")
        form.addRow(self.double_tap)

        self.hotkey_hint = QLabel("")
        self.hotkey_hint.setWordWrap(True)
        self.hotkey_hint.setProperty("role", "subtitle")
        form.addRow(self.hotkey_hint)
        layout.addWidget(box)

        appearance = QGroupBox("Overlay")
        appearance_form = _form(appearance)
        self.show_overlay = QCheckBox("Show the floating status pill while dictating")
        appearance_form.addRow(self.show_overlay)

        self.overlay_position = QComboBox()
        self.overlay_position.addItem("Bottom of the screen", "bottom")
        self.overlay_position.addItem("Top of the screen", "top")
        self.overlay_position.addItem("Next to the mouse pointer", "cursor")
        appearance_form.addRow("Position", self.overlay_position)

        self.overlay_margin = QSpinBox()
        self.overlay_margin.setRange(0, 600)
        self.overlay_margin.setSuffix(" px")
        appearance_form.addRow("Distance from edge", self.overlay_margin)

        self.show_preview = QCheckBox("Show the transcript in the pill after inserting")
        appearance_form.addRow(self.show_preview)

        self.sound_cues = QCheckBox("Play a short sound when recording starts and stops")
        appearance_form.addRow(self.sound_cues)

        if session().is_wayland:
            note = QLabel(
                "On Wayland the compositor decides where windows go, so the pill may "
                "ignore this position. docs/wayland.md has a KWin rule that pins it."
            )
            note.setWordWrap(True)
            note.setProperty("role", "subtitle")
            appearance_form.addRow(note)
        layout.addWidget(appearance)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ tab: model

    def _build_model_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        box = QGroupBox("Whisper model")
        form = _form(box)
        self.model_combo = QComboBox()
        for info in MODELS:
            self.model_combo.addItem(f"{info.label} · {info.size_mb} MB", info.name)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        form.addRow("Model", self.model_combo)

        self.model_note = QLabel("")
        self.model_note.setWordWrap(True)
        self.model_note.setProperty("role", "subtitle")
        form.addRow(self.model_note)

        self.language_combo = QComboBox()
        for label, code in LANGUAGES:
            self.language_combo.addItem(label, code)
        self.language_combo.setToolTip(
            "Pinning the language is faster and more accurate than auto-detection "
            "for short utterances."
        )
        form.addRow("Language", self.language_combo)

        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatic (GPU when available)", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("CUDA GPU", "cuda")
        form.addRow("Compute device", self.device_combo)

        self.compute_combo = QComboBox()
        for label, value in (
            ("Automatic", "auto"), ("int8 (fastest on CPU)", "int8"),
            ("int8 + float16", "int8_float16"), ("float16 (GPU)", "float16"),
            ("float32 (most precise)", "float32"),
        ):
            self.compute_combo.addItem(label, value)
        form.addRow("Precision", self.compute_combo)

        self.beam_spin = QSpinBox()
        self.beam_spin.setRange(1, 10)
        self.beam_spin.setToolTip("Higher is slightly more accurate and slower. 5 is a good default.")
        form.addRow("Beam size", self.beam_spin)

        self.preload_check = QCheckBox("Load the model at startup (first dictation is instant)")
        form.addRow(self.preload_check)
        layout.addWidget(box)

        prompt_box = QGroupBox("Vocabulary hint")
        prompt_layout = QVBoxLayout(prompt_box)
        hint = QLabel(
            "Names and jargon you dictate often. Whisper biases towards these words — "
            "keep it short, a sentence or two works best."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "subtitle")
        prompt_layout.addWidget(hint)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("Kubernetes, Debian, KDE Plasma, Anthropic, PostgreSQL")
        self.prompt_edit.setMaximumHeight(90)
        prompt_layout.addWidget(self.prompt_edit)
        layout.addWidget(prompt_box)

        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout(status_box)
        self.model_status = QLabel("Not loaded")
        self.model_status.setWordWrap(True)
        status_layout.addWidget(self.model_status)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.hide()
        status_layout.addWidget(self.model_progress)
        load_row = QHBoxLayout()
        load_now = QPushButton("Load / download now")
        load_now.clicked.connect(self._load_model_now)
        load_row.addWidget(load_now)
        load_row.addStretch(1)
        status_layout.addLayout(load_row)
        layout.addWidget(status_box)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ tab: audio

    def _build_audio_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        box = QGroupBox("Microphone")
        form = _form(box)
        self.device_input = QComboBox()
        self.device_input.addItem("System default", "")
        for index, name in list_input_devices():
            self.device_input.addItem(name, name)
        form.addRow("Input device", self.device_input)

        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        form.addRow("Level", self.level_bar)
        layout.addWidget(box)

        limits = QGroupBox("Stopping")
        limits_form = _form(limits)
        self.silence_timeout = QDoubleSpinBox()
        self.silence_timeout.setRange(0.0, 30.0)
        self.silence_timeout.setSingleStep(0.5)
        self.silence_timeout.setSuffix(" s")
        self.silence_timeout.setSpecialValueText("Never (stop manually)")
        self.silence_timeout.setToolTip(
            "In tap-to-toggle and hands-free mode, stop automatically after this much silence."
        )
        limits_form.addRow("Stop after silence", self.silence_timeout)

        self.silence_threshold = QDoubleSpinBox()
        self.silence_threshold.setRange(0.001, 0.2)
        self.silence_threshold.setDecimals(3)
        self.silence_threshold.setSingleStep(0.002)
        self.silence_threshold.setToolTip("Raise this in a noisy room, lower it if it cuts you off.")
        limits_form.addRow("Silence threshold", self.silence_threshold)

        self.max_duration = QDoubleSpinBox()
        self.max_duration.setRange(5.0, 3600.0)
        self.max_duration.setSuffix(" s")
        limits_form.addRow("Maximum length", self.max_duration)

        self.min_duration = QDoubleSpinBox()
        self.min_duration.setRange(0.0, 3.0)
        self.min_duration.setSingleStep(0.05)
        self.min_duration.setSuffix(" s")
        self.min_duration.setToolTip("Shorter takes are treated as an accidental tap and discarded.")
        limits_form.addRow("Ignore takes under", self.min_duration)
        layout.addWidget(limits)
        layout.addStretch(1)
        return page

    # -------------------------------------------------------- tab: insertion

    def _build_insertion_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        box = QGroupBox("How the text gets into the field")
        form = _form(box)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Automatic (recommended)", "auto")
        self.method_combo.addItem("Type character by character", "type")
        self.method_combo.addItem("Clipboard + Ctrl+V", "paste")
        self.method_combo.addItem("Clipboard only (paste it yourself)", "clipboard")
        form.addRow("Method", self.method_combo)

        self.backend_combo = QComboBox()
        for label, value in (
            ("Automatic", "auto"), ("xdotool (X11)", "xdotool"), ("wtype (Wayland)", "wtype"),
            ("ydotool (any session)", "ydotool"), ("built-in uinput", "uinput"),
        ):
            self.backend_combo.addItem(label, value)
        form.addRow("Backend", self.backend_combo)

        self.click_to_focus = QCheckBox("Click where the mouse is first, to focus that field")
        self.click_to_focus.setToolTip(
            "Off by default: most apps keep the caret where you left it, and a stray "
            "click can move it. Turn this on if you like aiming with the mouse."
        )
        form.addRow(self.click_to_focus)

        self.restore_clipboard = QCheckBox("Put the previous clipboard back after pasting")
        form.addRow(self.restore_clipboard)

        self.always_copy = QCheckBox("Always keep the transcript on the clipboard")
        form.addRow(self.always_copy)

        self.type_delay = QSpinBox()
        self.type_delay.setRange(0, 100)
        self.type_delay.setSuffix(" ms")
        self.type_delay.setToolTip("Increase if an app drops characters (Electron apps sometimes do).")
        form.addRow("Delay per keystroke", self.type_delay)

        self.pre_delay = QDoubleSpinBox()
        self.pre_delay.setRange(0.0, 1.0)
        self.pre_delay.setSingleStep(0.02)
        self.pre_delay.setSuffix(" s")
        form.addRow("Pause before inserting", self.pre_delay)
        layout.addWidget(box)

        diagnostics = QGroupBox("What this session supports")
        diagnostics_layout = QVBoxLayout(diagnostics)
        self.diagnostics_table = QTableWidget(0, 3)
        self.diagnostics_table.setHorizontalHeaderLabels(["Backend", "Usable", "Detail"])
        self.diagnostics_table.verticalHeader().hide()
        self.diagnostics_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.diagnostics_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.diagnostics_table.setMaximumHeight(190)
        diagnostics_layout.addWidget(self.diagnostics_table)

        row = QHBoxLayout()
        refresh = QPushButton("Re-check")
        refresh.clicked.connect(self._refresh_diagnostics)
        row.addWidget(refresh)
        self.plan_label = QLabel("")
        self.plan_label.setProperty("role", "subtitle")
        row.addWidget(self.plan_label, 1)
        diagnostics_layout.addLayout(row)
        layout.addWidget(diagnostics)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------- tab: text

    def _build_text_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        box = QGroupBox("Clean-up")
        box_layout = QVBoxLayout(box)
        self.remove_fillers = QCheckBox("Remove filler sounds (uh, ähm, hmm)")
        self.tidy_whitespace = QCheckBox("Tidy spacing and punctuation")
        self.voice_commands = QCheckBox('Obey spoken commands ("new line", "new paragraph")')
        self.capitalize_first = QCheckBox("Capitalise the first letter")
        self.trailing_space = QCheckBox("Add a trailing space, so dictations do not run together")
        self.drop_hallucinations = QCheckBox('Discard Whisper\'s silence artefacts ("Thank you.")')
        for widget in (
            self.remove_fillers, self.tidy_whitespace, self.voice_commands,
            self.capitalize_first, self.trailing_space, self.drop_hallucinations,
        ):
            box_layout.addWidget(widget)
        layout.addWidget(box)

        replacements = QGroupBox("Always replace")
        replacements_layout = QVBoxLayout(replacements)
        hint = QLabel("Fix the words Whisper gets wrong for you — matched whole-word, case-insensitive.")
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        replacements_layout.addWidget(hint)

        self.replacements_table = QTableWidget(0, 2)
        self.replacements_table.setHorizontalHeaderLabels(["Heard", "Written"])
        self.replacements_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.replacements_table.verticalHeader().hide()
        replacements_layout.addWidget(self.replacements_table)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_replacement_row("", ""))
        row.addWidget(add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_replacement_row)
        row.addWidget(remove)
        row.addStretch(1)
        replacements_layout.addLayout(row)
        layout.addWidget(replacements, 1)
        return page

    # ---------------------------------------------------------- tab: history

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search your transcripts…")
        self.history_search.textChanged.connect(self.refresh_history)
        search_row.addWidget(self.history_search, 1)
        reload_button = QPushButton("Refresh")
        reload_button.clicked.connect(self.refresh_history)
        search_row.addWidget(reload_button)
        layout.addLayout(search_row)

        self.history_list = QListWidget()
        self.history_list.setWordWrap(True)
        self.history_list.itemDoubleClicked.connect(lambda item: self._reinsert(item))
        layout.addWidget(self.history_list, 1)

        self.history_stats = QLabel("")
        self.history_stats.setProperty("role", "subtitle")
        layout.addWidget(self.history_stats)

        row = QHBoxLayout()
        insert = QPushButton("Insert again")
        insert.setProperty("role", "primary")
        insert.clicked.connect(lambda: self._reinsert(self.history_list.currentItem()))
        row.addWidget(insert)

        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self._copy_selected)
        row.addWidget(copy_button)

        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete_selected)
        row.addWidget(delete)
        row.addStretch(1)

        clear = QPushButton("Clear history")
        clear.setProperty("role", "danger")
        clear.clicked.connect(self._clear_history)
        row.addWidget(clear)
        layout.addLayout(row)
        return page

    # ------------------------------------------------------------ data <-> ui

    def _load_into_widgets(self) -> None:
        config = self.config
        _select(self.mode_combo, config.hotkey.mode)
        self.kde_shortcut.setText(config.hotkey.kde_shortcut)
        self.kde_cancel_shortcut.setText(config.hotkey.kde_cancel_shortcut)
        self.combo_edit.setText(config.hotkey.combo)
        self.double_tap.setChecked(config.hotkey.double_tap_latch)

        self.show_overlay.setChecked(config.ui.show_overlay)
        _select(self.overlay_position, config.ui.overlay_position)
        self.overlay_margin.setValue(config.ui.overlay_margin)
        self.show_preview.setChecked(config.ui.show_result_preview)
        self.sound_cues.setChecked(config.audio.sound_cues)

        _select(self.model_combo, config.model.name)
        _select(self.language_combo, config.model.language)
        _select(self.device_combo, config.model.device)
        _select(self.compute_combo, config.model.compute_type)
        self.beam_spin.setValue(config.model.beam_size)
        self.preload_check.setChecked(config.model.preload)
        self.prompt_edit.setPlainText(config.model.initial_prompt)

        _select(self.device_input, config.audio.device)
        self.silence_timeout.setValue(config.audio.silence_timeout)
        self.silence_threshold.setValue(config.audio.silence_threshold)
        self.max_duration.setValue(config.audio.max_duration)
        self.min_duration.setValue(config.audio.min_duration)

        _select(self.method_combo, config.insert.method)
        _select(self.backend_combo, config.insert.backend)
        self.click_to_focus.setChecked(config.insert.click_to_focus)
        self.restore_clipboard.setChecked(config.insert.restore_clipboard)
        self.always_copy.setChecked(config.insert.always_copy)
        self.type_delay.setValue(config.insert.type_delay_ms)
        self.pre_delay.setValue(config.insert.pre_insert_delay)

        self.remove_fillers.setChecked(config.text.remove_fillers)
        self.tidy_whitespace.setChecked(config.text.tidy_whitespace)
        self.voice_commands.setChecked(config.text.voice_commands)
        self.capitalize_first.setChecked(config.text.capitalize_first)
        self.trailing_space.setChecked(config.text.trailing_space)
        self.drop_hallucinations.setChecked(config.text.drop_hallucinations)

        self.replacements_table.setRowCount(0)
        for source, target in config.text.replacements.items():
            self._add_replacement_row(source, target)

        self._on_mode_changed()
        self._on_model_changed()
        self._refresh_diagnostics()
        self.refresh_history()

    def _collect(self) -> Config:
        config = copy.deepcopy(self.config)
        config.hotkey.mode = self.mode_combo.currentData()
        config.hotkey.kde_shortcut = self.kde_shortcut.text().strip()
        config.hotkey.kde_cancel_shortcut = self.kde_cancel_shortcut.text().strip()
        config.hotkey.combo = self.combo_edit.text().strip() or "super+alt"
        config.hotkey.double_tap_latch = self.double_tap.isChecked()

        config.ui.show_overlay = self.show_overlay.isChecked()
        config.ui.overlay_position = self.overlay_position.currentData()
        config.ui.overlay_margin = self.overlay_margin.value()
        config.ui.show_result_preview = self.show_preview.isChecked()
        config.audio.sound_cues = self.sound_cues.isChecked()

        config.model.name = self.model_combo.currentData()
        config.model.language = self.language_combo.currentData()
        config.model.device = self.device_combo.currentData()
        config.model.compute_type = self.compute_combo.currentData()
        config.model.beam_size = self.beam_spin.value()
        config.model.preload = self.preload_check.isChecked()
        config.model.initial_prompt = self.prompt_edit.toPlainText().strip()

        config.audio.device = self.device_input.currentData() or ""
        config.audio.silence_timeout = self.silence_timeout.value()
        config.audio.silence_threshold = self.silence_threshold.value()
        config.audio.max_duration = self.max_duration.value()
        config.audio.min_duration = self.min_duration.value()

        config.insert.method = self.method_combo.currentData()
        config.insert.backend = self.backend_combo.currentData()
        config.insert.click_to_focus = self.click_to_focus.isChecked()
        config.insert.restore_clipboard = self.restore_clipboard.isChecked()
        config.insert.always_copy = self.always_copy.isChecked()
        config.insert.type_delay_ms = self.type_delay.value()
        config.insert.pre_insert_delay = self.pre_delay.value()

        config.text.remove_fillers = self.remove_fillers.isChecked()
        config.text.tidy_whitespace = self.tidy_whitespace.isChecked()
        config.text.voice_commands = self.voice_commands.isChecked()
        config.text.capitalize_first = self.capitalize_first.isChecked()
        config.text.trailing_space = self.trailing_space.isChecked()
        config.text.drop_hallucinations = self.drop_hallucinations.isChecked()

        replacements: dict[str, str] = {}
        for row in range(self.replacements_table.rowCount()):
            source_item = self.replacements_table.item(row, 0)
            target_item = self.replacements_table.item(row, 1)
            source = source_item.text().strip() if source_item else ""
            target = target_item.text().strip() if target_item else ""
            if source:
                replacements[source] = target
        config.text.replacements = replacements
        return config

    # --------------------------------------------------------------- actions

    def _save(self) -> None:
        config = self._collect()
        try:
            path = config.save()
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", f"Writing the configuration failed:\n{exc}")
            return
        self.config = config
        self.footer_hint.setText(f"Saved to {path}")
        QTimer.singleShot(4000, lambda: self.footer_hint.setText(""))
        self.configSaved.emit(config)

    def _on_mode_changed(self) -> None:
        hold = self.mode_combo.currentData() == "hold"
        self.combo_edit.setEnabled(hold)
        self.double_tap.setEnabled(hold)
        if hold:
            self.hotkey_hint.setText(
                "Hold-to-talk reads the keyboard directly, which needs your user to be in "
                "the 'input' group: sudo usermod -aG input $USER, then log out and back in. "
                "Run `local-whisper doctor` to check."
            )
        else:
            self.hotkey_hint.setText(
                "Tap-to-toggle uses a normal KDE shortcut that runs `local-whisper toggle`. "
                "It needs no special permissions and works on Wayland."
            )

    def _on_model_changed(self) -> None:
        name = self.model_combo.currentData()
        for info in MODELS:
            if info.name == name:
                self.model_note.setText(info.note)
                break

    def _install_kde_shortcuts(self) -> None:
        config = self._collect()
        steps = kde.install(config.hotkey)
        text = "\n".join(str(step) for step in steps)
        ok = all(step.ok for step in steps)
        box = QMessageBox(self)
        box.setWindowTitle("KDE shortcuts")
        box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
        box.setText("Shortcut registration finished." if ok else "Some steps did not work.")
        box.setDetailedText(text + "\n\n" + kde.manual_instructions(config.hotkey))
        box.exec()

    def _show_manual_steps(self) -> None:
        QMessageBox.information(
            self, "Set the shortcut by hand", kde.manual_instructions(self._collect().hotkey)
        )

    def _load_model_now(self) -> None:
        if self.controller is None:
            return
        self.model_progress.show()
        self.model_status.setText("Loading…")
        self.controller.reload_config(self._collect())
        self.controller.preload_model()

    def _refresh_diagnostics(self) -> None:
        if self.controller is None:
            return
        injector = self.controller.injector
        injector.invalidate()
        rows = injector.diagnostics()
        self.diagnostics_table.setRowCount(len(rows))
        for index, (name, available, detail) in enumerate(rows):
            self.diagnostics_table.setItem(index, 0, QTableWidgetItem(name))
            self.diagnostics_table.setItem(index, 1, QTableWidgetItem("yes" if available else "no"))
            self.diagnostics_table.setItem(index, 2, QTableWidgetItem(detail))
        self.diagnostics_table.resizeColumnsToContents()
        self.plan_label.setText("Plan: " + injector.plan().describe())

    def _add_replacement_row(self, source: str, target: str) -> None:
        row = self.replacements_table.rowCount()
        self.replacements_table.insertRow(row)
        self.replacements_table.setItem(row, 0, QTableWidgetItem(source))
        self.replacements_table.setItem(row, 1, QTableWidgetItem(target))

    def _remove_replacement_row(self) -> None:
        row = self.replacements_table.currentRow()
        if row >= 0:
            self.replacements_table.removeRow(row)

    def _test_dictation(self) -> None:
        if self.controller is not None:
            self.controller.toggle()

    # --------------------------------------------------------------- history

    def refresh_history(self) -> None:
        if self.controller is None:
            return
        self.history_list.clear()
        entries = self.controller.history.recent(200, self.history_search.text().strip())
        for entry in entries:
            preview = entry.text if len(entry.text) <= 240 else entry.text[:237] + "…"
            item = QListWidgetItem(f"{entry.when()}   ·   {entry.words} words\n{preview}")
            item.setData(Qt.UserRole, entry.id)
            item.setData(Qt.UserRole + 1, entry.text)
            self.history_list.addItem(item)
        stats = self.controller.history.stats()
        minutes = stats["audio_seconds"] / 60
        self.history_stats.setText(
            f"{int(stats['entries'])} dictations · {int(stats['words'])} words · {minutes:.1f} minutes of audio"
        )

    def _selected_text(self) -> str:
        item = self.history_list.currentItem()
        return str(item.data(Qt.UserRole + 1)) if item is not None else ""

    def _reinsert(self, item: QListWidgetItem | None) -> None:
        text = str(item.data(Qt.UserRole + 1)) if item is not None else self._selected_text()
        if not text:
            return
        self.hide()  # give focus back to the target window before typing
        QTimer.singleShot(220, lambda: self.reinsertRequested.emit(text))

    def _copy_selected(self) -> None:
        text = self._selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.footer_hint.setText("Copied to the clipboard")
            QTimer.singleShot(2500, lambda: self.footer_hint.setText(""))

    def _delete_selected(self) -> None:
        item = self.history_list.currentItem()
        if item is None or self.controller is None:
            return
        self.controller.history.delete(int(item.data(Qt.UserRole)))
        self.refresh_history()

    def _clear_history(self) -> None:
        if self.controller is None:
            return
        confirm = QMessageBox.question(
            self, "Clear history", "Delete every stored transcript? This cannot be undone."
        )
        if confirm == QMessageBox.Yes:
            self.controller.history.clear()
            self.refresh_history()

    # ------------------------------------------------------- live status feed

    def on_state(self, state: State, detail: str) -> None:
        self.status_label.setText(detail or state.label)
        if state != State.RECORDING:
            self.level_bar.setValue(0)

    def on_level(self, level: float) -> None:
        self.level_bar.setValue(int(min(1.0, level) * 100))

    def on_model_status(self, message: str) -> None:
        self.model_status.setText(message)
        self.model_progress.setVisible("Loading" in message or "Download" in message)


def _form(parent: QWidget) -> QFormLayout:
    """A form layout with room to breathe and labels that never overlap fields."""
    form = QFormLayout(parent)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(10)
    form.setContentsMargins(8, 14, 8, 8)
    return form


def _scrollable(page: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


def _select(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
