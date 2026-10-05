import sys
import types

import numpy as np
import pytest

from skydispatch.core.config import VoiceSettings
from skydispatch.voice import gpu, stt


class Seg:
    text = " hello dispatcher "


def install_fake_whisper(monkeypatch, gpu_error="Library cublas64_12.dll is not found or cannot be loaded"):
    made = []

    class WhisperModel:
        def __init__(self, name, device="auto", compute_type="int8"):
            self.device = device
            made.append((device, compute_type))

        def transcribe(self, audio, **kw):
            if self.device != "cpu":                 # 'auto' and 'cuda' both end up on the broken GPU
                def boom():
                    raise RuntimeError(gpu_error)
                    yield                          # decoding is lazy: the error appears while iterating
                return boom(), None
            return iter([Seg()]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))
    return made


@pytest.fixture
def gpu_libs_present(monkeypatch):
    monkeypatch.setattr(gpu, "available", lambda: True)
    monkeypatch.setattr(gpu, "has_cuda_device", lambda: True)
    monkeypatch.setattr(gpu, "activate", lambda: None)


AUDIO = np.zeros(stt.SAMPLE_RATE, dtype="float32")


@pytest.mark.parametrize("device", ["auto", "cuda"])
def test_missing_cuda_libraries_fall_back_to_cpu(monkeypatch, gpu_libs_present, device):
    made = install_fake_whisper(monkeypatch)
    t = stt.Transcriber(VoiceSettings(stt_device=device))
    assert t.transcribe(AUDIO) == "hello dispatcher"
    assert made[-1] == ("cpu", "int8") and len(made) == 2
    assert t.transcribe(AUDIO) == "hello dispatcher"          # stays on the CPU, no further failed attempts
    assert len(made) == 2


def test_unrelated_errors_are_not_swallowed(monkeypatch, gpu_libs_present):
    install_fake_whisper(monkeypatch, gpu_error="out of memory while decoding")
    with pytest.raises(RuntimeError, match="out of memory"):
        stt.Transcriber(VoiceSettings(stt_device="auto")).transcribe(AUDIO)


def test_cpu_errors_are_raised_not_retried(monkeypatch):
    class WhisperModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            raise RuntimeError("Library cublas64_12.dll is not found")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))
    with pytest.raises(RuntimeError):
        stt.Transcriber(VoiceSettings(stt_device="cpu")).transcribe(AUDIO)


def test_preload_falls_back_when_the_model_fails_to_load(monkeypatch, gpu_libs_present):
    calls = []

    class WhisperModel:
        def __init__(self, name, device="auto", compute_type="int8"):
            calls.append(device)
            if device != "cpu":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))
    stt.Transcriber(VoiceSettings(stt_device="auto")).preload()
    assert calls == ["cuda", "cpu"]


@pytest.mark.parametrize("device", ["auto", "cuda"])
def test_without_the_libraries_the_cpu_is_used_directly(monkeypatch, device):
    made = install_fake_whisper(monkeypatch)
    monkeypatch.setattr(gpu, "available", lambda: False)
    t = stt.Transcriber(VoiceSettings(stt_device=device))
    assert t.transcribe(AUDIO) == "hello dispatcher"
    assert made == [("cpu", "int8")]                      # no failed GPU attempt first


def test_gpu_is_used_when_libraries_are_installed(monkeypatch, gpu_libs_present):
    made = []

    class WhisperModel:
        def __init__(self, name, device="auto", compute_type="int8"):
            made.append((device, compute_type))

        def transcribe(self, *a, **kw):
            return iter([Seg()]), None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))
    assert stt.Transcriber(VoiceSettings(stt_device="auto")).transcribe(AUDIO) == "hello dispatcher"
    assert made == [("cuda", "float16")]
