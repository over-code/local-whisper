"""Test fixtures: keep every test off the real ~/.config and ~/.local."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                     "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        directory = tmp_path / variable.lower()
        directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(directory))
    yield tmp_path
