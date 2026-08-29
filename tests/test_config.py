from localwhisper.config import Config
from localwhisper import paths


def test_defaults_are_sane():
    config = Config()
    assert config.model.name == "small"
    assert config.hotkey.mode == "toggle"
    assert config.insert.method == "auto"


def test_save_and_load_roundtrip():
    config = Config()
    config.model.name = "medium"
    config.audio.silence_timeout = 4.0
    config.text.replacements = {"claude code": "Claude Code", "kd plasma": "KDE Plasma"}
    config.model.initial_prompt = 'Kubernetes, "quoted", back\\slash'
    path = config.save()

    loaded = Config.load(path)
    assert loaded.model.name == "medium"
    assert loaded.audio.silence_timeout == 4.0
    assert loaded.text.replacements["kd plasma"] == "KDE Plasma"
    assert loaded.model.initial_prompt == config.model.initial_prompt


def test_load_missing_file_returns_defaults():
    assert Config.load(paths.config_file()).model.name == Config().model.name


def test_unknown_and_malformed_keys_are_ignored():
    config = Config.from_dict({
        "model": {"name": "tiny", "nonsense": 5, "beam_size": "not a number"},
        "audio": "not even a table",
    })
    assert config.model.name == "tiny"
    assert config.model.beam_size == Config().model.beam_size
    assert config.audio.sample_rate == 16000


def test_broken_toml_falls_back_to_defaults(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("[model\nname = ", encoding="utf-8")
    assert Config.load(path).model.name == "small"


def test_int_written_for_float_field_is_accepted(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[audio]\nsilence_timeout = 3\n", encoding="utf-8")
    assert Config.load(path).audio.silence_timeout == 3.0
