import pytest

from localwhisper.config import HotkeyConfig
from localwhisper.hotkey import HotkeyManager, kde
from localwhisper.hotkey.evdev_backend import HotkeyError, parse_combo

evdev = pytest.importorskip("evdev")


def test_parse_modifier_combo():
    groups = parse_combo("super+alt")
    assert groups == [
        (evdev.ecodes.KEY_LEFTMETA, evdev.ecodes.KEY_RIGHTMETA),
        (evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT),
    ]


def test_parse_named_and_function_keys():
    assert parse_combo("ctrl+alt+space")[-1] == (evdev.ecodes.KEY_SPACE,)
    assert parse_combo("f9") == [(evdev.ecodes.KEY_F9,)]


def test_unknown_key_is_reported():
    with pytest.raises(HotkeyError):
        parse_combo("banana")


class Fake:
    def __init__(self, mode="hold"):
        self.events = []
        self.recording = False
        config = HotkeyConfig(mode=mode, backend="none", double_tap_window=10.0)
        self.manager = HotkeyManager(
            config,
            on_start=self._start,
            on_stop=self._stop,
            on_toggle=lambda: self.events.append("toggle"),
            is_recording=lambda: self.recording,
        )

    def _start(self, hands_free):
        self.recording = True
        self.events.append(f"start(hands_free={hands_free})")

    def _stop(self):
        self.recording = False
        self.events.append("stop")


def test_hold_starts_and_stops():
    fake = Fake()
    fake.manager._last_release = -100  # make sure this is not seen as a double tap
    fake.manager._on_press()
    fake.manager._on_release()
    assert fake.events == ["start(hands_free=False)", "stop"]


def test_double_tap_latches_hands_free():
    fake = Fake()
    fake.manager._last_release = -100
    fake.manager._on_press()
    fake.manager._on_release()          # first tap: a discarded blip
    fake.manager._on_press()            # second tap within the window
    fake.manager._on_release()
    assert fake.manager.is_latched is True
    assert fake.recording is True       # still listening after the key came up

    fake.manager._on_press()            # a later press ends the hands-free take
    assert fake.events[-1] == "stop"
    assert fake.manager.is_latched is False


def test_toggle_mode_ignores_release():
    fake = Fake(mode="toggle")
    fake.manager._on_press()
    fake.manager._on_release()
    assert fake.events == ["toggle"]


def test_manager_without_evdev_reports_the_desktop_shortcut():
    manager = HotkeyManager(
        HotkeyConfig(mode="toggle", backend="none"),
        on_start=lambda hands_free: None, on_stop=lambda: None,
        on_toggle=lambda: None, is_recording=lambda: False,
    )
    status = manager.start()
    assert status.backend == "ipc" and "Meta+Alt+D" in status.detail


def test_kde_desktop_entry_is_well_formed():
    entry = kde._desktop_entry("Local Whisper: Toggle", "/usr/bin/local-whisper toggle", "Meta+Alt+D")
    assert entry.startswith("[Desktop Entry]")
    assert "X-KDE-Shortcuts=Meta+Alt+D" in entry
    assert "Exec=/usr/bin/local-whisper toggle" in entry
    assert "NoDisplay=true" in entry


def test_kde_install_writes_desktop_files(monkeypatch, tmp_path):
    monkeypatch.setattr(kde, "_kwriteconfig", lambda: None)  # no Plasma in CI
    monkeypatch.setattr(kde, "reload_kglobalaccel", lambda: kde.Step(True, "skipped"))
    steps = kde.install(HotkeyConfig())
    written = list((kde.applications_dir()).glob("local-whisper-*.desktop"))
    assert len(written) == 2
    assert any("Meta+Alt+D" in path.read_text() for path in written)
    assert steps  # and it reported what it did
