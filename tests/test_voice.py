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
        def speak(self, text): spoken.append(text); started.set()

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
