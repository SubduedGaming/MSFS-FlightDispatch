import threading

from skydispatch.core.config import Settings
from skydispatch.voice import tts
from skydispatch.voice.text import speakable


def test_speakable_expands_units_and_icao():
    out = speakable("Fly **EGLL** to KJFK, 300 nm at 120 kts, touchdown 250 fpm. https://x.y")
    assert "E G L L" in out and "K J F K" in out
    assert "nautical miles" in out and "knots" in out and "feet per minute" in out
    assert "**" not in out and "http" not in out


def test_piper_urls():
    m, c = tts.piper_urls("en_GB-alan-medium")
    assert m == "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx"
    assert c.endswith(".onnx.json")
    m2, _ = tts.piper_urls("en_GB-jenny_dioco-medium")
    assert "/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx" in m2


def test_tts_manager_queues_and_stops(monkeypatch):
    spoken, started = [], threading.Event()

    class Fake(tts.TTSEngine):
        name = "fake"
        def available(self): return True
        def speak(self, text, voice="", speed=1.0): spoken.append(text); started.set()

    s = Settings()
    mgr = tts.TTSManager(s.voice)
    monkeypatch.setattr(mgr, "engine", lambda: Fake())
    mgr.speak("Hello **captain**")
    assert started.wait(2)
    assert spoken == ["Hello captain"]
    s.voice.tts_enabled = False
    mgr.speak("muted")
    mgr.shutdown()
    assert spoken == ["Hello captain"]


def test_tts_falls_back_to_none_gracefully(monkeypatch):
    s = Settings()
    s.voice.tts_engine = "piper"
    mgr = tts.TTSManager(s.voice)
    assert mgr.engine() is None      # piper not installed here
    mgr.shutdown()


def test_piper_voice_resolution(monkeypatch):
    s = Settings()
    assert tts.resolve_piper_voice(s.voice, "en_US-amy-medium") == tts.DEFAULT_PIPER_VOICE   # persona voice not downloaded
    monkeypatch.setattr(tts, "piper_installed", lambda v: v == "en_US-amy-medium")
    assert tts.resolve_piper_voice(s.voice, "en_US-amy-medium") == "en_US-amy-medium"
    s.voice.piper_model = "en_US-ryan-medium"                                               # explicit choice wins
    assert tts.resolve_piper_voice(s.voice, "en_US-amy-medium") == "en_US-ryan-medium"


def test_device_name_listed_under_several_host_apis_resolves(monkeypatch):
    import sys
    import types

    from skydispatch.voice import stt

    devices = [
        {"name": "Mic (Headset)", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 1},   # WASAPI
        {"name": "Mic (Headset)", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},   # MME
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    ]
    fake = types.SimpleNamespace(
        query_devices=lambda: devices,
        query_hostapis=lambda i=None: [{"name": "MME"}, {"name": "WASAPI"}] if i is None else {"name": "MME"},
        default=types.SimpleNamespace(hostapi=0))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert stt.list_input_devices() == ["Mic (Headset)"]               # de-duplicated
    assert stt.resolve_device("Mic (Headset)", "input") == 1           # the default API's copy (MME)
    assert stt.resolve_device("Unplugged", "input") is None
    assert stt.resolve_device("", "output") is None
