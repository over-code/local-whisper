import numpy as np
import pytest

from localwhisper.config import ModelConfig
from localwhisper.stt import engine


def test_compute_type_defaults():
    assert engine.detect_compute_type("auto", "cpu") == "int8"
    assert engine.detect_compute_type("auto", "cuda") == "float16"
    assert engine.detect_compute_type("float32", "cpu") == "float32"


def test_device_detection_respects_explicit_choice():
    assert engine.detect_device("cpu") == "cpu"
    assert engine.detect_device("cuda") == "cuda"


def test_resample_changes_length_and_keeps_shape():
    samples = np.sin(np.linspace(0, 20 * np.pi, 48000)).astype(np.float32)
    out = engine._resample(samples, 48000, 16000)
    assert out.dtype == np.float32
    assert abs(out.size - 16000) <= 1
    assert engine._resample(samples, 16000, 16000) is samples


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeInfo:
    language = "en"
    language_probability = 0.97


class FakeModel:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def transcribe(self, samples, **kwargs):
        self.calls.append(kwargs)
        return iter([FakeSegment(" Hello"), FakeSegment(" world.")]), FakeInfo()


@pytest.fixture
def fake_whisper(monkeypatch):
    import sys, types

    module = types.ModuleType("faster_whisper")
    created = {}

    def factory(name, **kwargs):
        model = FakeModel(name, **kwargs)
        created["model"] = model
        created["name"] = name
        return model

    module.WhisperModel = factory
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return created


def test_transcribe_joins_segments(fake_whisper):
    transcriber = engine.Transcriber(ModelConfig(name="tiny", device="cpu", language="en"))
    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32), 16000)
    assert result.text == "Hello world."
    assert result.language == "en"
    assert result.duration == pytest.approx(1.0)
    assert fake_whisper["name"] == "tiny"


def test_transcribe_passes_decoding_options(fake_whisper):
    config = ModelConfig(name="tiny", device="cpu", language="de", beam_size=3,
                         initial_prompt="Kubernetes")
    transcriber = engine.Transcriber(config)
    transcriber.transcribe(np.zeros(8000, dtype=np.float32), 16000)
    call = fake_whisper["model"].calls[0]
    assert call["language"] == "de"
    assert call["beam_size"] == 3
    assert call["initial_prompt"] == "Kubernetes"
    assert call["vad_filter"] is True
    assert call["condition_on_previous_text"] is False


def test_auto_language_is_passed_as_none(fake_whisper):
    transcriber = engine.Transcriber(ModelConfig(name="tiny", device="cpu", language=""))
    transcriber.transcribe(np.zeros(8000, dtype=np.float32), 16000)
    assert fake_whisper["model"].calls[0]["language"] is None


def test_empty_audio_short_circuits():
    transcriber = engine.Transcriber(ModelConfig(name="tiny", device="cpu"))
    assert transcriber.transcribe(np.zeros(0, dtype=np.float32)).text == ""


def test_model_reload_on_config_change(fake_whisper):
    config = ModelConfig(name="tiny", device="cpu")
    transcriber = engine.Transcriber(config)
    transcriber.load()
    assert transcriber.is_loaded
    config.name = "base"
    transcriber.load()
    assert fake_whisper["name"] == "base"
