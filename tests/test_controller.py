"""End-to-end test of the dictation pipeline with the slow parts faked."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from localwhisper.app import DictationController  # noqa: E402
from localwhisper.audio.recorder import RecordingResult  # noqa: E402
from localwhisper.config import Config  # noqa: E402
from localwhisper.inject import InsertReport  # noqa: E402
from localwhisper.state import State  # noqa: E402
from localwhisper.stt.engine import TranscriptionResult  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class FakeRecorder:
    def __init__(self, duration=2.0, peak=0.4):
        self.result = RecordingResult(
            samples=np.zeros(int(16000 * duration), dtype=np.float32),
            sample_rate=16000, duration=duration, peak_level=peak,
        )
        self.started = False
        self.cancelled = False

    def start(self, allow_auto_stop=True):
        self.started = True

    def stop(self):
        self.started = False
        return self.result

    def cancel(self):
        self.cancelled = True
        self.started = False


def build(qt_app, transcript=" Hello there, um, world. ", duration=2.0, peak=0.4, **config_kwargs):
    config = Config()
    config.model.preload = False
    config.audio.sound_cues = False
    for key, value in config_kwargs.items():
        section, _, field = key.partition("__")
        setattr(getattr(config, section), field, value)

    controller = DictationController(config)
    controller.recorder = FakeRecorder(duration, peak)
    controller.transcriber.transcribe = lambda samples, rate=16000: TranscriptionResult(
        text=transcript, language="en", duration=duration, elapsed=0.2
    )
    inserted: list[str] = []
    controller.injector.insert = lambda text: (
        inserted.append(text) or InsertReport(True, "paste", "uinput", copied=True)
    )
    controller.inserted = inserted
    return controller


def pump(qt_app, controller, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_full_dictation_inserts_and_records_history(qt_app):
    controller = build(qt_app)
    controller.start_recording(hands_free=True)
    assert controller.state == State.RECORDING

    controller.stop_recording()
    assert pump(qt_app, controller, lambda: bool(controller.inserted)), "nothing was inserted"

    # Post-processing ran: the filler is gone, the trailing space is there.
    assert controller.inserted[0] == "Hello there, world. "
    entry = controller.history.latest()
    assert entry.text == "Hello there, world."
    assert entry.model == "small" and entry.method == "paste:uinput"
    controller.shutdown()


def test_short_tap_is_discarded(qt_app):
    controller = build(qt_app, duration=0.1)
    controller.start_recording()
    controller.stop_recording()
    assert controller.inserted == []
    assert controller.state == State.IDLE
    controller.shutdown()


def test_silent_take_is_not_transcribed(qt_app):
    controller = build(qt_app, duration=3.0, peak=0.001)
    controller.start_recording()
    controller.stop_recording()
    assert controller.inserted == []
    assert controller.state == State.DONE
    controller.shutdown()


def test_hallucinated_transcript_is_not_inserted(qt_app):
    controller = build(qt_app, transcript="Thank you.")
    controller.start_recording()
    controller.stop_recording()
    assert pump(qt_app, controller, lambda: controller.state == State.DONE)
    assert controller.inserted == []
    controller.shutdown()


def test_cancel_during_recording_drops_the_audio(qt_app):
    controller = build(qt_app)
    controller.start_recording()
    controller.cancel()
    assert controller.recorder.cancelled is True
    assert controller.state == State.IDLE
    assert controller.inserted == []
    controller.shutdown()


def test_pause_blocks_the_hotkey(qt_app):
    controller = build(qt_app)
    controller.set_paused(True)
    controller.start_recording()
    assert controller.state == State.PAUSED
    controller.set_paused(False)
    controller.start_recording()
    assert controller.state == State.RECORDING
    controller.shutdown()


def test_toggle_starts_then_stops(qt_app):
    controller = build(qt_app)
    controller.toggle()
    assert controller.state == State.RECORDING
    controller.toggle()
    assert pump(qt_app, controller, lambda: bool(controller.inserted))
    controller.shutdown()


def test_ipc_status_reports_the_pipeline(qt_app):
    controller = build(qt_app)
    reply = controller.handle_ipc("status", {})
    assert reply["ok"] and reply["model"] == "small"
    assert reply["hotkey"]["mode"] == "toggle"
    assert controller.handle_ipc("nonsense", {})["ok"] is False
    controller.shutdown()


def test_insert_last_reuses_the_previous_transcript(qt_app):
    controller = build(qt_app)
    controller.history.add("A previous dictation")
    controller.insert_last()
    assert controller.inserted[-1].startswith("A previous dictation")
    controller.shutdown()


def test_reload_config_applies_new_settings(qt_app):
    controller = build(qt_app)
    new = Config()
    new.model.preload = False
    new.text.remove_fillers = False
    new.model.name = "base"
    controller.reload_config(new)
    assert controller.post.config.remove_fillers is False
    assert controller.transcriber.config.name == "base"
    controller.shutdown()
