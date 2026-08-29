"""The controller: hotkey → microphone → Whisper → your text field.

Everything slow (model loading, transcription, keystroke injection) happens on
worker threads and reports back through Qt signals, so the overlay animates
smoothly while a model is decoding.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .audio import Recorder, RecordingResult, cues
from .config import Config
from .history import History
from .hotkey import HotkeyManager
from .inject import InsertReport, TextInjector
from .logging_setup import get
from .state import State
from .stt import PostProcessor, Transcriber

log = get("app")


class DictationController(QObject):
    """Owns the dictation lifecycle. One instance per daemon."""

    stateChanged = Signal(object, str)        # State, detail text
    levelChanged = Signal(float)              # 0..1 microphone level
    transcriptReady = Signal(str, object)     # text, InsertReport
    errorRaised = Signal(str)
    modelStatus = Signal(str)                 # human-readable model progress
    configReloaded = Signal(object)           # Config

    #: Internal: marshals IPC commands from the socket thread to the GUI thread.
    _commandReceived = Signal(str, object)
    #: Internal: the audio thread asking us to stop (silence timeout).
    _autoStopRequested = Signal()
    #: Internal: worker threads asking the GUI thread to start a timer.
    _idleRequested = Signal(int)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.state = State.IDLE
        self.paused = False
        self.last_text = ""
        self.last_report: InsertReport | None = None

        self.transcriber = Transcriber(config.model)
        self.post = PostProcessor(config.text)
        self.injector = TextInjector(config.insert)
        self.history = History(limit=config.ui.history_limit)
        self.recorder = Recorder(
            config.audio,
            on_level=self._on_level,
            on_auto_stop=self._autoStopRequested.emit,
        )
        self.hotkeys = HotkeyManager(
            config.hotkey,
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_toggle=self.toggle,
            is_recording=lambda: self.state == State.RECORDING,
        )

        self._hands_free = False
        self._busy = threading.Lock()
        #: Bumped by :meth:`cancel`; a worker whose token is stale drops its result.
        self._token = 0
        self._commandReceived.connect(self._handle_command_on_gui_thread)
        self._autoStopRequested.connect(self._on_auto_stop)
        self._idleRequested.connect(self._return_to_idle_after)

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        status = self.hotkeys.start()
        log.info("hotkeys: %s (%s)", status.backend, status.detail)
        self.injector.plan()  # log the insertion plan once, at startup
        if self.config.model.preload:
            self._preload_model()
        else:
            self._set_state(State.IDLE)

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self.recorder.cancel()
        self.history.close()

    def preload_model(self) -> None:
        """Load (and if necessary download) the model on a worker thread."""
        self._preload_model()

    def _preload_model(self) -> None:
        self._set_state(State.LOADING, f"Loading {self.config.model.name}…")

        def worker() -> None:
            try:
                self.transcriber.load(on_status=self.modelStatus.emit)
                self.transcriber.warmup()
                self.modelStatus.emit(
                    f"{self.config.model.name} ready on "
                    f"{self.transcriber.device} ({self.transcriber.compute_type})"
                )
                self._set_state(State.IDLE)
            except Exception as exc:
                log.exception("model preload failed")
                self.modelStatus.emit(f"Model failed to load: {exc}")
                self._fail(f"Could not load the {self.config.model.name} model: {exc}")

        threading.Thread(target=worker, name="lw-preload", daemon=True).start()

    # ----------------------------------------------------------------- state

    def _set_state(self, state: State, detail: str = "") -> None:
        self.state = state
        self.stateChanged.emit(state, detail or state.label)

    def _fail(self, message: str) -> None:
        log.error("%s", message)
        self._set_state(State.ERROR, message)
        self.errorRaised.emit(message)
        self._idleRequested.emit(2500)

    def _return_to_idle_after(self, milliseconds: int) -> None:
        """Always runs on the GUI thread — only it owns a Qt event loop."""
        QTimer.singleShot(max(0, milliseconds), self._back_to_idle)

    def _back_to_idle(self) -> None:
        if self.state in (State.ERROR, State.DONE):
            self._set_state(State.PAUSED if self.paused else State.IDLE)

    # ------------------------------------------------------------- recording

    def start_recording(self, hands_free: bool = False) -> None:
        if self.paused:
            log.debug("ignoring start: dictation is paused")
            return
        if self.state == State.RECORDING:
            return
        if self.state.is_busy:
            log.debug("ignoring start: still %s", self.state.value)
            return

        self._hands_free = hands_free or self.config.hotkey.mode == "toggle"
        try:
            self.recorder.start(allow_auto_stop=self._hands_free)
        except Exception as exc:
            self._fail(f"Microphone unavailable: {exc}")
            return
        if self.config.audio.sound_cues:
            cues.play("start")
        self._set_state(State.RECORDING, "Hands-free" if hands_free else "")

    def stop_recording(self) -> None:
        if self.state != State.RECORDING:
            return
        result = self.recorder.stop()
        if self.config.audio.sound_cues:
            cues.play("stop")

        if result.duration < self.config.audio.min_duration:
            # A tap rather than a dictation (this is also the first half of a
            # double-tap), so drop it without any fuss.
            log.debug("discarding a %.2fs clip", result.duration)
            self._set_state(State.IDLE)
            return
        if result.is_silent:
            self._set_state(State.DONE, "Nothing heard")
            QTimer.singleShot(1200, self._back_to_idle)
            return

        self._set_state(State.TRANSCRIBING)
        threading.Thread(
            target=self._transcribe_and_insert, args=(result,), name="lw-transcribe", daemon=True
        ).start()

    def cancel(self) -> None:
        if self.state == State.RECORDING:
            self.recorder.cancel()
            if self.config.audio.sound_cues:
                cues.play("cancel")
            self._set_state(State.IDLE, "Cancelled")
        elif self.state.is_busy:
            # A decode in flight cannot be interrupted, so invalidate it instead:
            # the worker will throw its result away when it finishes.
            self._token += 1
            self._set_state(State.IDLE, "Cancelled")

    def toggle(self) -> None:
        if self.state == State.RECORDING:
            self.stop_recording()
        else:
            self.start_recording(hands_free=True)

    def _on_level(self, level: float) -> None:
        self.levelChanged.emit(min(1.0, level * 8.0))  # scale RMS into a usable range

    def _on_auto_stop(self) -> None:
        if self.state == State.RECORDING:
            log.debug("stopping on silence")
            self.stop_recording()

    # ------------------------------------------------- transcription (worker)

    def _transcribe_and_insert(self, recording: RecordingResult) -> None:
        token = self._token
        with self._busy:
            try:
                result = self.transcriber.transcribe(recording.samples, recording.sample_rate)
            except Exception as exc:
                log.exception("transcription failed")
                self._fail(f"Transcription failed: {exc}")
                return

            if token != self._token:
                log.debug("dropping a cancelled transcription")
                return

            text = self.post.process(result.text)
            if not text.strip():
                self._set_state(State.DONE, "Nothing to insert")
                self._idleRequested.emit(1200)
                return

            self._set_state(State.INSERTING, text.strip()[:60])
            self._wait_for_hotkey_release()
            report = self.injector.insert(text)

            self.history.add(
                text.strip(),
                audio_seconds=recording.duration,
                transcribe_s=result.elapsed,
                model=self.config.model.name,
                language=result.language,
                inserted=report.ok and report.method != "clipboard",
                method=f"{report.method}:{report.backend}" if report.backend else report.method,
            )
            self.last_text = text
            self.last_report = report
            self.transcriptReady.emit(text.strip(), report)

            if not report.ok:
                self._fail(report.message or "Could not insert the text")
                return
            detail = report.message or text.strip()
            self._set_state(State.DONE, detail)
            self._idleRequested.emit(max(400, self.config.ui.result_preview_ms))

    def _wait_for_hotkey_release(self, timeout: float = 1.0) -> None:
        """Never type while Super/Alt are still down — the keystrokes would be
        interpreted as shortcuts by whatever window has focus."""
        deadline = time.monotonic() + timeout
        while self.hotkeys.key_held and time.monotonic() < deadline:
            time.sleep(0.02)

    # ------------------------------------------------------------------- IPC

    def handle_ipc(self, command: str, payload: dict) -> dict:
        """Called on the IPC thread: answer immediately, act on the GUI thread."""
        if command == "ping":
            return {"ok": True, "state": self.state.value, "version": _version()}
        if command == "status":
            return {"ok": True, **self.status_dict()}
        if command == "history":
            limit = int(payload.get("limit", 10))
            return {
                "ok": True,
                "entries": [
                    {"text": entry.text, "when": entry.when(), "model": entry.model}
                    for entry in self.history.recent(limit)
                ],
            }
        if command in ("toggle", "start", "stop", "cancel", "settings", "quit",
                       "reload", "pause", "resume", "insert-last"):
            self._commandReceived.emit(command, payload)
            return {"ok": True, "state": self.state.value}
        return {"ok": False, "error": f"unknown command: {command}"}

    def status_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "paused": self.paused,
            "model": self.config.model.name,
            "model_loaded": self.transcriber.is_loaded,
            "device": self.transcriber.device,
            "hotkey": {
                "mode": self.config.hotkey.mode,
                "backend": self.hotkeys.status.backend,
                "detail": self.hotkeys.status.detail,
            },
            "insertion": self.injector.plan().describe(),
            "last_text": self.last_text.strip(),
        }

    def _handle_command_on_gui_thread(self, command: str, payload: object) -> None:
        payload = payload if isinstance(payload, dict) else {}
        if command == "toggle":
            self.toggle()
        elif command == "start":
            self.start_recording(hands_free=True)
        elif command == "stop":
            self.stop_recording()
        elif command == "cancel":
            self.cancel()
        elif command == "pause":
            self.set_paused(True)
        elif command == "resume":
            self.set_paused(False)
        elif command == "insert-last":
            self.insert_last()
        elif command == "reload":
            self.reload_config()
        elif command in ("settings", "quit"):
            # The window and the application object live in the UI layer, which
            # connects to this signal as well.
            pass

    # -------------------------------------------------------------- commands

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused and self.state == State.RECORDING:
            self.cancel()
        self._set_state(State.PAUSED if paused else State.IDLE)

    def insert_last(self) -> None:
        entry = self.history.latest()
        if entry is None:
            return
        report = self.injector.insert(entry.text + (" " if self.config.text.trailing_space else ""))
        self.last_report = report
        self._set_state(State.DONE, report.message or "Re-inserted")
        QTimer.singleShot(1200, self._back_to_idle)

    def insert_text(self, text: str) -> InsertReport:
        report = self.injector.insert(text)
        self.last_report = report
        return report

    def reload_config(self, config: Config | None = None) -> None:
        """Apply a new configuration without restarting the daemon."""
        new = config or Config.load()
        model_changed = new.model != self.config.model
        hotkey_changed = new.hotkey != self.config.hotkey

        self.config = new
        self.post = PostProcessor(new.text)
        self.injector.config = new.insert
        self.injector.invalidate()
        self.recorder.config = new.audio
        self.history.limit = new.ui.history_limit
        self.transcriber.config = new.model

        if model_changed:
            self.transcriber.unload()
            if new.model.preload:
                self._preload_model()
        if hotkey_changed:
            self.hotkeys.restart(new.hotkey)
        self.configReloaded.emit(new)
        log.info("configuration reloaded")


def _version() -> str:
    from . import __version__

    return __version__
