"""Voice on the phone: speech events and audio downloads out, recordings in. Piper and Whisper are faked."""
from conftest import AI_URL, NO_AI_URL  # noqa: F401
import http.client
import io
import json
import threading
import time
import wave

import pytest

from skydispatch.core.config import Settings, VoiceSettings
from skydispatch.db.database import Database
from skydispatch.server.engine import Engine
from skydispatch.server.speech import SpeechStore
from skydispatch.voice import tts
from skydispatch.voice.audio import MAX_SECONDS, AudioError, pcm_to_wav, wav_to_mono16k
from skydispatch.web.server import WebRemote

np = pytest.importorskip("numpy")


def sine(seconds=1.0, rate=16000, channels=1, freq=440.0):
    t = np.arange(int(seconds * rate)) / rate
    mono = (np.sin(2 * np.pi * freq * t) * 12000).astype("<i2")
    data = np.repeat(mono[:, None], channels, axis=1) if channels > 1 else mono
    return pcm_to_wav(data.tobytes(), rate, channels)


# ------------------------------------------------------------------------------------------ audio helpers
def test_wav_roundtrip_and_formats():
    out = wav_to_mono16k(sine(1.0))
    assert out.dtype == np.float32 and len(out) == 16000 and 0.3 < float(np.abs(out).max()) < 0.4
    stereo = wav_to_mono16k(sine(0.5, rate=44100, channels=2))
    assert abs(len(stereo) - 8000) <= 1 and float(np.abs(stereo).max()) > 0.3         # resampled and downmixed
    assert len(wav_to_mono16k(sine(0.5, rate=8000))) == 8000


@pytest.mark.parametrize("data", [b"", b"not a wav", sine(0.2)[:30]])
def test_unreadable_audio_is_refused(data):
    with pytest.raises(AudioError):
        wav_to_mono16k(data)


def test_unsupported_wav_formats_are_refused():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(1), w.setframerate(16000), w.writeframes(b"\x80" * 16000)
    with pytest.raises(AudioError, match="16-bit"):
        wav_to_mono16k(buf.getvalue())
    with pytest.raises(AudioError, match="mono or stereo"):
        wav_to_mono16k(sine(0.2, rate=4000))
    with pytest.raises(AudioError, match="longer than"):
        wav_to_mono16k(pcm_to_wav(b"\x00\x00" * 8000 * (MAX_SECONDS + 1), 8000))


def test_speech_store_keeps_the_latest_and_expires():
    store = SpeechStore(keep=3, ttl=0.05)
    first = store.add("general", "one", "", 1.0)
    assert store.get(first.id) is first and store.get("nope") is None
    time.sleep(0.08)
    assert store.get(first.id) is None
    store = SpeechStore(keep=3)
    ids = [store.add("general", str(i), "", 1.0).id for i in range(5)]
    assert [store.get(i) is not None for i in ids] == [False, False, True, True, True]


# ------------------------------------------------------------------------------------------ piper
class OldVoice:                                    # piper-tts 1.2
    config = type("C", (), {"sample_rate": 22050})()

    def synthesize_stream_raw(self, text):
        yield b"\x01\x00" * 100
        yield b"\x02\x00" * 50


class Chunk:
    def __init__(self, n, rate):
        self.audio_int16_bytes, self.sample_rate = b"\x03\x00" * n, rate


class NewVoice:                                    # piper-tts >= 1.3
    config = type("C", (), {"sample_rate": 16000})()

    def synthesize(self, text, syn_config=None):
        yield Chunk(80, 24000)
        yield Chunk(20, 24000)


@pytest.mark.parametrize("voice,rate,samples", [(OldVoice(), 22050, 150), (NewVoice(), 24000, 100)])
def test_piper_synthesize_returns_pcm_without_a_sound_card(voice, rate, samples):
    eng = tts.PiperEngine(VoiceSettings())
    eng._load = lambda override="": voice
    pcm, got_rate = eng.synthesize("Hello there", "en_GB-alan-medium", 1.2)
    assert got_rate == rate and len(pcm) == samples * 2


def test_piper_cannot_synthesize_when_not_installed():
    assert tts.PiperEngine(VoiceSettings()).can_synthesize() is False            # piper is not installed here


def test_manager_wraps_synthesis_in_a_wav(monkeypatch):
    mgr = tts.TTSManager(VoiceSettings())
    try:
        assert mgr.synthesize("hi") is None and mgr.can_synthesize() is False
        monkeypatch.setattr(tts.PiperEngine, "can_synthesize", lambda self, voice="": True)
        monkeypatch.setattr(tts.PiperEngine, "synthesize", lambda self, text, voice="", speed=1.0: (b"\x01\x00" * 400, 22050))
        wav = mgr.synthesize("Cleared for takeoff", "v")
        with wave.open(io.BytesIO(wav)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 22050, 400)
        mgr.cfg.tts_engine = "system"                                                # only Piper can render audio
        assert mgr.can_synthesize() is False
    finally:
        mgr.shutdown()


# ------------------------------------------------------------------------------------------ engine
@pytest.fixture
def engine(tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = AI_URL
    s.ai.timeout_s = 1
    s.voice.output = "phone"
    e = Engine(s, Database(tmp_path / "e.db"))
    e.main.run(lambda: e.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000))
    e.said = []
    e.voice.say = lambda text, voice="", tag="", speed=1.0: e.said.append((text, voice, tag, speed))
    e.voice.tts.can_synthesize = lambda voice="": True
    e.synth_calls = []

    def fake_synth(text, voice="", speed=1.0):
        e.synth_calls.append((text, voice, speed))
        return pcm_to_wav(b"\x01\x00" * 800, 22050)
    e.voice.tts.synthesize = fake_synth
    yield e
    e.shutdown()


def collect(engine):
    got, stops = [], []
    engine.speech.connect(got.append)
    engine.speech_stop.connect(stops.append)
    return got, stops


def test_phone_mode_sends_a_speech_event_instead_of_playing_on_the_pc(engine):
    got, _ = collect(engine)
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", "general"))
    engine.main.run(lambda: engine.speak("Wind is *calm*, see https://x.test for 3 nm.", "general"))
    assert engine.said == [] and len(got) == 1
    ev = got[0]
    assert ev["thread"] == "general" and ev["audio"] is True and ev["speed"] > 0 and len(ev["id"]) == 16
    assert "http" not in ev["text"] and "*" not in ev["text"] and "nautical miles" in ev["text"]
    assert engine.speech_store.get(ev["id"]).text == ev["text"]


def test_speech_only_when_that_conversation_is_being_looked_at(engine):
    got, _ = collect(engine)
    engine.main.run(lambda: engine.speak("Nobody is listening", "general"))
    assert got == []
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", "employer:bluebird"))
    engine.main.run(lambda: engine.speak("Wrong conversation", "general"))
    assert got == []
    engine.settings.voice.auto_speak_replies = False
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", "general"))
    engine.main.run(lambda: engine.speak("Switched off", "general"))
    assert got == []


def test_copilot_callouts_reach_the_phone_in_flight_without_a_viewer(engine, monkeypatch):
    got, _ = collect(engine)
    monkeypatch.setattr(engine.copilot, "in_flight", lambda: True)
    engine.main.run(lambda: engine.speak("Positive rate", "copilot"))
    assert [g["thread"] for g in got] == ["copilot"]


def test_audio_flag_tells_the_phone_when_it_must_speak_the_text_itself(engine):
    got, _ = collect(engine)
    engine.voice.tts.can_synthesize = lambda voice="": False
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", "general"))
    engine.main.run(lambda: engine.speak("Hello", "general"))
    assert got[0]["audio"] is False


def test_pc_mode_is_unchanged(engine):
    got, _ = collect(engine)
    engine.settings.voice.output = "pc"
    engine.main.run(lambda: engine.set_viewing("desktop:messenger", "general"))
    engine.main.run(lambda: engine.speak("Hello", "general"))
    assert len(engine.said) == 1 and engine.said[0][2] == "general" and got == []


def test_speech_stop_follows_what_is_being_viewed_and_silence(engine):
    _, stops = collect(engine)
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", "general"))
    assert stops[-1] == {"keep": ["general"]}
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", None))
    assert stops[-1] == {"keep": []}
    engine.main.run(engine.silence)
    assert stops[-1] == {"keep": []} and len(stops) == 3


def test_render_utterance_is_made_once_and_can_be_unavailable(engine):
    utt = engine.speech_store.add("general", "Hello", "v", 1.0)
    assert engine.render_utterance(utt) == engine.render_utterance(utt) and len(engine.synth_calls) == 1
    engine.voice.tts.synthesize = lambda *a, **k: None
    other = engine.speech_store.add("general", "Again", "v", 1.0)
    assert engine.render_utterance(other) is None


# ------------------------------------------------------------------------------------------ http
class Client:
    def __init__(self, port, headers):
        self.port, self.headers = port, headers

    def call(self, method, path, body=None, headers=None, auth=True, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {**(self.headers if auth else {}), **(headers or {})}
        payload = raw if raw is not None else (json.dumps(body) if body is not None else None)
        c.request(method, path, payload, h)
        r = c.getresponse()
        data = r.read()
        ctype = r.getheader("Content-Type", "")
        return r.status, (json.loads(data) if ctype.startswith("application/json") else data), ctype


@pytest.fixture
def server(engine):
    remote = WebRemote(engine, "127.0.0.1", 0, "legacy")
    remote.start()
    port = remote._server.server_address[1]
    code = engine.pairing.new_code()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/v1/pair", json.dumps({"code": code, "device_name": "Pixel"}), {"X-SkyDispatch": "1"})
    token = json.loads(c.getresponse().read())["token"]
    engine.said.clear()
    yield engine, Client(port, {"Authorization": f"Bearer {token}"})
    remote.stop()


def fake_stt(engine, text="request taxi clearance", seen=None):
    engine.voice.stt_status = lambda: (True, "Speech recognition ready")

    def transcribe(audio):
        if seen is not None:
            seen.append(audio)
        return text
    engine.voice.stt.transcribe = transcribe


def test_audio_download(server):
    engine, c = server
    utt = engine.speech_store.add("general", "Hello captain", "", 1.0)
    path = f"/api/v1/voice/audio/{utt.id}"
    assert c.call("GET", path, auth=False)[0] == 401
    st, data, ctype = c.call("GET", path)
    assert st == 200 and ctype == "audio/wav"
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 22050 and w.getnframes() == 800
    assert c.call("GET", path)[1] == data and len(engine.synth_calls) == 1               # kept, not rendered twice
    assert c.call("GET", "/api/v1/voice/audio/0123456789abcdef")[0] == 404
    assert c.call("GET", "/api/v1/voice/audio/../state")[0] in (401, 404)
    engine.voice.tts.synthesize = lambda *a, **k: None
    other = engine.speech_store.add("general", "No piper here", "", 1.0)
    st, data, _ = c.call("GET", f"/api/v1/voice/audio/{other.id}")
    assert st == 503 and "speak the text" in data["error"].lower()


def test_transcribe_returns_text_and_can_send_it(server):
    engine, c = server
    seen = []
    fake_stt(engine, seen=seen)
    wav = sine(1.0, rate=44100, channels=2)
    assert c.call("POST", "/api/v1/voice/transcribe", raw=wav, auth=False)[0] == 403                    # no credentials at all
    assert c.call("POST", "/api/v1/voice/transcribe", raw=wav, auth=False, headers={"X-SkyDispatch": "1"})[0] == 401
    st, data, _ = c.call("POST", "/api/v1/voice/transcribe", raw=wav, headers={"Content-Type": "audio/wav"})
    assert st == 200 and data == {"text": "request taxi clearance", "sent": False}
    assert abs(len(seen[0]) - 16000) <= 1 and seen[0].dtype == np.float32               # resampled to 16 kHz mono
    assert engine.db.messages(10, "general") == []
    st, data, _ = c.call("POST", "/api/v1/voice/transcribe?send=general", raw=wav)
    assert st == 200 and data["sent"] is True
    end = time.time() + 5
    while time.time() < end and not engine.db.messages(10, "general"):
        time.sleep(0.05)
    assert engine.db.messages(10, "general")[0]["content"] == "request taxi clearance"


def test_transcribe_errors(server):
    engine, c = server
    fake_stt(engine)
    wav = sine(0.5)
    call = lambda path="/api/v1/voice/transcribe", **kw: c.call("POST", path, **kw)
    assert call(raw=b"")[0] == 400
    assert call(raw=b"definitely not audio")[0] == 400
    assert call("/api/v1/voice/transcribe?send=../etc", raw=wav)[0] == 400
    big = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)                  # claims a body that is too large
    big.putrequest("POST", "/api/v1/voice/transcribe")
    for k, v in {**c.headers, "Content-Length": str(3 * 1024 * 1024)}.items():
        big.putheader(k, v)
    big.endheaders()
    assert big.getresponse().status == 413
    big.close()
    engine.voice.stt.transcribe = lambda audio: ""
    st, data, _ = call(raw=wav)
    assert st == 422 and "didn't hear" in data["error"]
    engine.voice.stt.transcribe = lambda audio: 1 / 0
    assert call(raw=wav)[0] == 500
    engine.voice.stt_status = lambda: (False, "faster-whisper is not installed.")
    st, data, _ = call(raw=wav)
    assert st == 503 and "faster-whisper" in data["error"]
    fake_stt(engine)
    engine.settings.voice.stt_enabled = False
    assert call(raw=wav)[0] == 503


def test_speech_events_arrive_on_the_stream(server):
    engine, c = server
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
    conn.request("GET", "/api/v1/stream?v=abcdefgh12", headers=c.headers)
    r = conn.getresponse()
    assert r.status == 200
    r.fp.readline(), r.fp.readline()
    engine.main.run(lambda: engine.set_viewing("remote:abcdefgh12", "general"))
    engine.main.run(lambda: engine.speak("Hello from the dispatcher", "general"))
    end, seen = time.time() + 8, b""
    while time.time() < end and b"event: speech\n" not in seen:
        seen += r.fp.readline()
    while time.time() < end and b"Hello from the dispatcher" not in seen:
        seen += r.fp.readline()
    conn.close()
    assert b"event: speech_stop" in seen or b"event: speech" in seen
    assert b"Hello from the dispatcher" in seen


def test_speech_output_is_a_setting_the_phone_can_change(server):
    engine, c = server
    assert c.call("GET", "/api/v1/settings")[1]["speech_output"] == "phone"
    assert c.call("POST", "/api/v1/settings", {"speech_output": "pc"})[0] == 200
    assert engine.settings.voice.output == "pc"
    assert c.call("POST", "/api/v1/settings", {"speech_output": "bogus"})[0] == 200
    assert engine.settings.voice.output == "pc"
    c.call("POST", "/api/v1/settings", {"speech_output": "phone"})
    assert engine.settings.voice.output == "phone"
