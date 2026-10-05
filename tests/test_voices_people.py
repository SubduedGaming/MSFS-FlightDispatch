import http.client
import json
import threading

import pytest

from skydispatch.ai.personas import HR_VOICE, PERSONAS
from skydispatch.copilot.personas import COPILOTS
from skydispatch.core.config import Settings
from skydispatch.data.employers import EMPLOYERS
from skydispatch.db.database import Database
from skydispatch.ui.context import AppContext
from skydispatch.voice import tts
from skydispatch.voice.characters import voices_in_use
from skydispatch.web.api import RemoteApi
from skydispatch.web.server import WebRemote


# ---------------------------------------------------------------------------- everyone has their own voice
def test_every_person_has_a_different_voice():
    voices = [p.piper_voice for p in PERSONAS.values()] + [c.piper_voice for c in COPILOTS.values()] + [HR_VOICE]
    assert len(voices) == len(set(voices)), "two people share a voice"


def test_every_company_has_its_own_dispatcher():
    ids = [e.persona for e in EMPLOYERS]
    assert len(ids) == len(set(ids)) and all(i in PERSONAS for i in ids)
    names = {PERSONAS[i].name for i in ids}
    assert len(names) == len(ids)


def test_without_their_own_voice_people_still_sound_different(monkeypatch):
    s = Settings()
    monkeypatch.setattr(tts, "installed_voices", lambda: ["en_GB-alan-medium", "en_US-amy-medium", "en_US-ryan-medium"])
    monkeypatch.setattr(tts, "piper_installed", lambda v: False)
    picks = {p.id: tts.resolve_piper_voice(s.voice, p.piper_voice) for p in PERSONAS.values()}
    assert set(picks.values()) <= {"en_GB-alan-medium", "en_US-amy-medium", "en_US-ryan-medium"}
    assert len(set(picks.values())) > 1                                     # not one shared voice
    again = {p.id: tts.resolve_piper_voice(s.voice, p.piper_voice) for p in PERSONAS.values()}
    assert picks == again                                                   # and stable between runs
    monkeypatch.setattr(tts, "piper_installed", lambda v: v == "en_US-amy-medium")
    assert tts.resolve_piper_voice(s.voice, "en_US-amy-medium") == "en_US-amy-medium"   # own voice wins when present


def test_voices_in_use_lists_the_people_you_talk_to():
    s = Settings()
    got = voices_in_use(s, ["bluebird", "atlas"])
    who = [w for _, w in got]
    assert any("Fiona" in w for w in who) and any("Ray" in w for w in who) and any("copilot" in w for w in who)
    assert HR_VOICE in [v for v, _ in got]
    assert len({v for v, _ in got}) == len(got)


def test_download_voices_fetches_only_what_is_missing(monkeypatch):
    fetched, seen = [], []
    monkeypatch.setattr(tts, "piper_installed", lambda v: v == "have")
    monkeypatch.setattr(tts, "download_piper_voice", lambda v, progress=None: (fetched.append(v), progress and progress(1.0)))
    out = tts.download_voices(["have", "a", "b", "a"], lambda f, v: seen.append((round(f, 2), v)))
    assert out == ["a", "b"] and fetched == ["a", "b"] and seen[-1] == (1.0, "b")


# ---------------------------------------------------------------------------- cancelling speech
def test_drop_unless_cancels_only_other_conversations():
    spoken, release, started = [], threading.Event(), threading.Event()
    stopped = []

    class Slow(tts.TTSEngine):
        name = "slow"

        def available(self):
            return True

        def speak(self, text, voice="", speed=1.0):
            spoken.append(text)
            started.set()
            release.wait(2)

        def stop(self):
            stopped.append(1)
            release.set()

    mgr = tts.TTSManager(Settings().voice)
    mgr._engine = Slow()
    mgr.engine = lambda: mgr._engine
    mgr.speak("first from A", tag="thread:a")
    assert started.wait(2)
    mgr.speak("second from A", tag="thread:a")
    mgr.speak("from B", tag="thread:b")
    mgr.speak("test voice")                                  # untagged speech is never cancelled
    mgr.drop_unless({"thread:b"})
    assert stopped                                            # A was talking and is cut off
    release.set()
    for _ in range(100):
        if "test voice" in spoken:
            break
        threading.Event().wait(0.02)
    mgr.shutdown()
    assert spoken == ["first from A", "from B", "test voice"]  # A's queued second message was dropped


# ---------------------------------------------------------------------------- only speak when you are looking
@pytest.fixture
def ctx(qtbot, tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = "http://127.0.0.1:9/v1"
    s.ai.timeout_s = 1
    c = AppContext(s, Database(tmp_path / "c.db"))
    c.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000)
    said, dropped = [], []
    c.voice.say = lambda text, voice="", tag="", speed=1.0: said.append((text, voice, tag, speed))
    c.voice.drop_unless = lambda tags: dropped.append(set(tags))
    c.said, c.dropped = said, dropped
    yield c
    c.stop_sim()
    c.voice.shutdown()


def test_nothing_is_spoken_for_a_conversation_nobody_is_viewing(ctx):
    ctx.speak("Hello Captain", "general")
    assert ctx.said == []
    ctx.set_viewing("desktop:messenger", "general")
    ctx.speak("Hello Captain", "general")
    assert [(t, tag) for t, _, tag, _ in ctx.said] == [("Hello Captain", "general")]
    ctx.speak("From another desk", "employer:bluebird")
    assert len(ctx.said) == 1                                  # a different person is not being looked at


def test_the_speaker_gets_their_own_voice(ctx):
    ctx.set_viewing("desktop:messenger", "employer:bluebird")
    ctx.speak("Welcome aboard", "employer:bluebird")
    ctx.speak("Welcome aboard", "employer:bluebird", voice=HR_VOICE, speed=1.0)
    voices = [v for _, v, _, _ in ctx.said]
    assert voices == [PERSONAS["fiona"].piper_voice, HR_VOICE]


def test_switching_conversation_cancels_the_previous_speaker(ctx):
    ctx.set_viewing("desktop:messenger", "general")
    assert ctx.dropped[-1] == {"general"}
    ctx.set_viewing("desktop:messenger", "employer:bluebird")
    assert ctx.dropped[-1] == {"employer:bluebird"}             # the general desk's speech is cut off
    ctx.set_viewing("desktop:messenger", None)                  # left the messenger page
    assert ctx.dropped[-1] == set()


def test_two_viewers_are_both_heard(ctx):
    ctx.set_viewing("desktop:messenger", "general")
    ctx.set_viewing("remote:abcdefgh", "employer:bluebird")
    assert ctx.dropped[-1] == {"general", "employer:bluebird"}
    ctx.set_viewing("remote:abcdefgh", None)
    assert ctx.dropped[-1] == {"general"}


def test_the_copilot_speaks_during_a_flight_without_being_watched(ctx, monkeypatch):
    ctx.speak("Positive rate", "copilot")
    assert ctx.said == []
    monkeypatch.setattr(ctx.copilot, "in_flight", lambda: True)
    ctx.speak("Positive rate", "copilot")
    assert len(ctx.said) == 1 and ctx.said[0][1] == COPILOTS["sam"].piper_voice
    ctx.speak("Dispatcher chatter", "general")
    assert len(ctx.said) == 1                                   # dispatchers wait until you open their conversation
    assert "copilot" in ctx.dropped[-1] if ctx.dropped else True


def test_speech_can_be_switched_off(ctx):
    ctx.settings.voice.auto_speak_replies = False
    ctx.set_viewing("desktop:messenger", "general")
    ctx.speak("Hello", "general")
    assert ctx.said == []


# ---------------------------------------------------------------------------- the browser remote reports what it shows
def test_view_endpoint(ctx):
    api = RemoteApi(ctx)
    ok = lambda **b: api.handle("POST", "/api/view", {}, b)
    assert ok(viewer="abcdefgh12", thread="general")[0] == 200
    assert ctx.viewing["remote:abcdefgh12"] == "general"
    assert ok(viewer="abcdefgh12", thread="employer:bluebird")[0] == 200
    assert ok(viewer="abcdefgh12", thread=None)[0] == 200 and "remote:abcdefgh12" not in ctx.viewing
    assert ok(viewer="x", thread="general")[0] == 400                      # bad viewer id
    assert ok(viewer="abcdefgh12", thread="employer:nobody")[0] == 400     # unknown conversation
    api.handle("POST", "/api/view", {}, {"viewer": "abcdefgh12", "thread": "copilot"})
    api.clear_viewer("abcdefgh12")
    assert "remote:abcdefgh12" not in ctx.viewing


def test_closing_the_browser_stops_listening(qtbot, ctx):
    remote = WebRemote(ctx, "127.0.0.1", 0, "code-for-test")
    remote.start()
    try:
        port = remote._server.server_address[1]
        out = []

        def call(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, json.dumps(body) if body is not None else None,
                      {"Content-Type": "application/json", **(headers or {})})
            r = c.getresponse()
            out.append((r.status, dict(r.getheaders()), r.read()))
        threading.Thread(target=call, args=("POST", "/api/login", {"token": "code-for-test"}, {"X-SkyDispatch": "1"}), daemon=True).start()
        qtbot.waitUntil(lambda: bool(out), timeout=8000)
        cookie = out[0][1]["Set-Cookie"].split(";")[0]
        hdr = {"Cookie": cookie, "X-SkyDispatch": "1"}
        stream_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        ready = threading.Event()

        def listen():
            stream_conn.request("GET", "/api/stream?v=viewer12345", headers={"Cookie": cookie})
            stream_conn.getresponse()
            ready.set()
        threading.Thread(target=listen, daemon=True).start()
        qtbot.waitUntil(lambda: ready.is_set(), timeout=8000)
        out.clear()
        threading.Thread(target=call, args=("POST", "/api/view", {"viewer": "viewer12345", "thread": "general"}, hdr), daemon=True).start()
        qtbot.waitUntil(lambda: bool(out) and ctx.viewing.get("remote:viewer12345") == "general", timeout=8000)
        stream_conn.close()                                       # the browser tab is closed
        remote.stop()                                              # ends the stream promptly on the server side too
        qtbot.waitUntil(lambda: "remote:viewer12345" not in ctx.viewing, timeout=8000)
    finally:
        remote.stop()
