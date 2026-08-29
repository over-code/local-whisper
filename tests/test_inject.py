"""Injection tests run without a desktop by faking the session and the tools."""

from __future__ import annotations

import pytest

from localwhisper import env
from localwhisper.config import InsertConfig
from localwhisper.inject import TextInjector, clipboard
from localwhisper.inject.base import Capability, InjectionError


def _clear(func) -> None:
    # monkeypatch may already have swapped the cached function for a lambda.
    clear = getattr(func, "cache_clear", None)
    if clear is not None:
        clear()


@pytest.fixture(autouse=True)
def clear_caches():
    _clear(env.session)
    _clear(env.has_tool)
    yield
    _clear(env.session)
    _clear(env.has_tool)


def fake_session(monkeypatch, session_type="x11"):
    monkeypatch.setenv("XDG_SESSION_TYPE", session_type)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    _clear(env.session)


def only_tool(monkeypatch, *names):
    monkeypatch.setattr(env, "has_tool", lambda name: name in names)


def test_x11_prefers_typing_with_xdotool(monkeypatch):
    fake_session(monkeypatch, "x11")
    only_tool(monkeypatch, "xdotool", "xclip")
    plan = TextInjector(InsertConfig()).plan()
    assert plan.method == "type" and plan.backend.name == "xdotool"


def test_wayland_ignores_xdotool(monkeypatch):
    fake_session(monkeypatch, "wayland")
    only_tool(monkeypatch, "xdotool", "wl-copy")
    plan = TextInjector(InsertConfig()).plan()
    assert plan.backend is None and plan.method == "clipboard"


def test_wayland_uses_wtype_when_present(monkeypatch):
    fake_session(monkeypatch, "wayland")
    only_tool(monkeypatch, "wtype", "wl-copy")
    plan = TextInjector(InsertConfig()).plan()
    assert plan.method == "type" and plan.backend.name == "wtype"


def test_uinput_backend_defaults_to_pasting(monkeypatch):
    """It can only type US-ASCII, so Ctrl+V is the better default."""
    fake_session(monkeypatch, "wayland")
    only_tool(monkeypatch, "wl-copy")
    injector = TextInjector(InsertConfig())
    monkeypatch.setattr(
        injector._backends[3], "capability",
        lambda: Capability("uinput", True, can_type=True, can_key=True, detail="ready"),
    )
    plan = injector.plan()
    assert plan.backend.name == "uinput" and plan.method == "paste"


def test_typing_failure_falls_back_to_paste(monkeypatch):
    fake_session(monkeypatch, "x11")
    only_tool(monkeypatch, "xdotool", "xclip")
    injector = TextInjector(InsertConfig())
    backend = injector.plan().backend

    def explode(*_args, **_kwargs):
        raise InjectionError("xdotool died")

    pasted: list[str] = []
    monkeypatch.setattr(backend, "type_text", explode)
    monkeypatch.setattr(backend, "send_paste", lambda: pasted.append("ctrl+v"))
    monkeypatch.setattr(clipboard, "set_text", lambda text: "xclip")
    monkeypatch.setattr(clipboard, "get_text", lambda: "")

    report = injector.insert("hello")
    assert report.ok and report.method == "paste" and pasted == ["ctrl+v"]


def test_everything_broken_still_copies(monkeypatch):
    fake_session(monkeypatch, "wayland")
    only_tool(monkeypatch)
    copied: list[str] = []
    monkeypatch.setattr(clipboard, "set_text", lambda text: copied.append(text) or "wl-copy")
    report = TextInjector(InsertConfig()).insert("fallback text")
    assert report.ok and report.method == "clipboard" and copied == ["fallback text"]
    assert "Ctrl+V" in report.message


def test_no_clipboard_at_all_reports_failure(monkeypatch):
    fake_session(monkeypatch, "wayland")
    only_tool(monkeypatch)

    def explode(_text):
        raise InjectionError("no clipboard tool available")

    monkeypatch.setattr(clipboard, "set_text", explode)
    report = TextInjector(InsertConfig()).insert("text")
    assert report.ok is False and "Could not insert" in report.message


def test_empty_text_is_a_noop():
    assert TextInjector(InsertConfig()).insert("").method == "noop"


def test_clipboard_is_restored_after_pasting(monkeypatch):
    fake_session(monkeypatch, "x11")
    only_tool(monkeypatch, "xdotool", "xclip")
    config = InsertConfig(method="paste", restore_clipboard=True)
    injector = TextInjector(config)
    writes: list[str] = []
    monkeypatch.setattr(clipboard, "get_text", lambda: "previous clipboard")
    monkeypatch.setattr(clipboard, "set_text", lambda text: writes.append(text) or "xclip")
    monkeypatch.setattr(injector.plan().backend, "send_paste", lambda: None)

    report = injector.insert("dictated")
    assert report.ok and writes[0] == "dictated"

    import time
    for _ in range(40):  # the restore runs on a short timer
        if len(writes) > 1:
            break
        time.sleep(0.05)
    assert writes[-1] == "previous clipboard"


def test_configured_backend_is_honoured(monkeypatch):
    fake_session(monkeypatch, "x11")
    only_tool(monkeypatch, "xdotool", "ydotool", "xclip")
    injector = TextInjector(InsertConfig(backend="ydotool"))
    monkeypatch.setattr(
        injector._backends[2], "capability",
        lambda: Capability("ydotool", True, can_type=True, can_key=True, detail="ready"),
    )
    assert injector.plan().backend.name == "ydotool"
