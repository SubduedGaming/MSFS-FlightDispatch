"""The phone-facing API: pairing, bearer tokens, the /api/v1 alias, the live stream and career setup."""
from conftest import AI_URL, NO_AI_URL  # noqa: F401
import http.client
import json
import threading
import time

import pytest

from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.server.engine import Engine
from skydispatch.web.server import WebRemote


def make_engine(tmp_path, career=True):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = AI_URL
    s.ai.timeout_s = 1
    e = Engine(s, Database(tmp_path / "e.db"))
    if career:
        e.main.run(lambda: e.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000))
    return e


class Client:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None, headers or {})
        r = c.getresponse()
        raw = r.read()
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw
        return r.status, data, dict(r.getheaders())


@pytest.fixture
def server(tmp_path):
    e = make_engine(tmp_path)
    remote = WebRemote(e, "127.0.0.1", 0, "legacy-code")
    remote.start()
    yield e, Client(remote._server.server_address[1])
    remote.stop()
    e.shutdown()


def pair(engine, client, name="Pixel"):
    code = engine.pairing.new_code()
    st, data, _ = client.call("POST", "/api/v1/pair", {"code": code, "device_name": name}, {"X-SkyDispatch": "1"})
    assert st == 200, data
    return {"Authorization": f"Bearer {data['token']}"}, data


def test_ping_is_public_and_reports_the_api_version(server):
    _e, c = server
    st, data, _ = c.call("GET", "/api/v1/ping")
    assert st == 200 and data["api"] == 1 and data["authed"] is False and data["app"] == "SkyDispatch"


def test_api_needs_a_token_and_pairing_gives_one(server):
    e, c = server
    assert c.call("GET", "/api/v1/state")[0] == 401
    assert c.call("GET", "/api/v1/state", headers={"Authorization": "Bearer nope"})[0] == 401
    hdr, data = pair(e, c)
    assert data["api"] == 1 and "." in data["token"]
    st, state, _ = c.call("GET", "/api/v1/state", headers=hdr)
    assert st == 200 and state["pilot"]["name"] == "Test Pilot"
    assert c.call("GET", "/api/v1/ping", headers=hdr)[1]["authed"] is True
    assert [d.name for d in e.pairing.devices()] == ["Pixel"]


def test_bearer_posts_need_no_csrf_header_but_cookies_still_do(server):
    e, c = server
    hdr, _ = pair(e, c)
    assert c.call("POST", "/api/v1/job/abandon", {}, hdr)[0] == 200
    st, _, h = c.call("POST", "/api/v1/login", {"token": "legacy-code"}, {"X-SkyDispatch": "1"})
    assert st == 200
    cookie = h["Set-Cookie"].split(";")[0]
    assert c.call("POST", "/api/v1/job/abandon", {}, {"Cookie": cookie})[0] == 403     # browsers must still send the header
    assert c.call("POST", "/api/v1/pair", {"code": "x"})[0] == 403                     # nor can a web page pair blindly


def test_wrong_codes_are_rate_limited_and_a_code_is_single_use(server):
    e, c = server
    code = e.pairing.new_code()
    for _ in range(5):
        assert c.call("POST", "/api/v1/pair", {"code": "WRONGCOD", "device_name": "x"},
                      {"X-SkyDispatch": "1"})[0] == 401
    assert c.call("POST", "/api/v1/pair", {"code": code, "device_name": "x"}, {"X-SkyDispatch": "1"})[0] == 429


def test_a_code_works_once(server):
    e, c = server
    code = e.pairing.new_code()
    h = {"X-SkyDispatch": "1"}
    assert c.call("POST", "/api/v1/pair", {"code": code, "device_name": "A"}, h)[0] == 200
    assert c.call("POST", "/api/v1/pair", {"code": code, "device_name": "B"}, h)[0] == 401


def test_revoking_signs_the_phone_out_and_unpair_removes_itself(server):
    e, c = server
    hdr, data = pair(e, c)
    assert e.pairing.revoke(data["device_id"])
    assert c.call("GET", "/api/v1/state", headers=hdr)[0] == 401
    hdr2, data2 = pair(e, c, "Tablet")
    assert c.call("POST", "/api/v1/unpair", {}, hdr2)[0] == 200
    assert e.pairing.devices() == [] and c.call("GET", "/api/v1/state", headers=hdr2)[0] == 401


def test_stream_accepts_a_bearer_token_and_delivers_events(server):
    e, c = server
    hdr, _ = pair(e, c)
    assert c.call("GET", "/api/v1/stream")[0] == 401
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
    conn.request("GET", "/api/v1/stream?v=abcdefgh12", headers=hdr)
    r = conn.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    assert r.fp.readline().startswith(b"retry:")
    r.fp.readline()
    e.toast.emit("good", "hello phone")
    end, seen = time.time() + 8, b""
    while time.time() < end and b"hello phone" not in seen:
        seen += r.fp.readline()
    conn.close()
    assert b"event: toast" in seen and b"hello phone" in seen


def test_pairing_info_for_the_admin_window(server):
    e, _c = server
    info = e.pairing_info(new_code=True)
    assert len(info["code"]) == 9 and info["port"] == e.settings.remote.port and "expires_in" in info
    if info["addresses"]:
        assert info["uri"].startswith("skydispatch://pair?host=") and info["code"].replace("-", "") in info["uri"]


# ------------------------------------------------------------------ career setup
@pytest.fixture
def empty_server(tmp_path):
    e = make_engine(tmp_path, career=False)
    remote = WebRemote(e, "127.0.0.1", 0, "legacy-code")
    remote.start()
    c = Client(remote._server.server_address[1])
    hdr, _ = pair(e, c)
    yield e, c, hdr
    remote.stop()
    e.shutdown()


def test_state_reports_no_career_and_options_are_offered(empty_server):
    _e, c, hdr = empty_server
    assert c.call("GET", "/api/v1/state", headers=hdr)[1]["has_career"] is False
    st, opts, _ = c.call("GET", "/api/v1/career/options", headers=hdr)
    assert st == 200 and {s["id"] for s in opts["starters"]} == {"c152", "c172", "dr40"}
    assert "new" in {x["id"] for x in opts["experience"]} and opts["defaults"]["aircraft"] == "c172"


def test_create_career_from_the_phone(empty_server):
    e, c, hdr = empty_server
    body = {"name": "Amelia Earhart", "home": "ksea", "aircraft": "c172", "callsign": "ame-1", "experience": "private",
            "difficulty": "relaxed", "balance": 40000, "currency": "£"}
    st, data, _ = c.call("POST", "/api/v1/career", body, hdr)
    assert st == 200 and data == {"ok": True}
    pilot = e.db.pilot()
    assert pilot.name == "Amelia Earhart" and pilot.home_icao == "KSEA" and pilot.callsign == "AME1"
    assert pilot.balance == 40000 and e.db.hangar()[0].type_id == "c172" and len(e.db.jobs("offered")) > 0
    assert e.settings.ui.currency == "£" and e.settings.game.difficulty == "relaxed"
    assert e.provider is not None                                   # the sim link is running
    st, state, _ = c.call("GET", "/api/v1/state", headers=hdr)
    assert state["has_career"] and state["pilot"]["name"] == "Amelia Earhart"


@pytest.mark.parametrize("patch,needle", [
    ({"name": "  "}, "pilot name"), ({"home": "ZZZZ"}, "airport"), ({"aircraft": "b738"}, "starter"),
    ({"experience": "god"}, "experience"), ({"difficulty": "nope"}, "difficulty"), ({"currency": "x"}, "currency"),
    ({"balance": -5}, "balance"), ({"balance": 99_999_999}, "balance")])
def test_create_career_validates_input(empty_server, patch, needle):
    e, c, hdr = empty_server
    body = {"name": "A", "home": "EGLL", "aircraft": "c172", **patch}
    st, data, _ = c.call("POST", "/api/v1/career", body, hdr)
    assert st == 400 and needle in data["error"].lower() and e.db.pilot() is None


def test_replacing_a_career_needs_confirmation(server):
    e, c = server
    hdr, _ = pair(e, c)
    body = {"name": "New Pilot", "home": "EGLL", "aircraft": "c152"}
    st, data, _ = c.call("POST", "/api/v1/career", body, hdr)
    assert st == 409 and "confirm_reset" in data["error"] and e.db.pilot().name == "Test Pilot"
    assert c.call("POST", "/api/v1/career", {**body, "confirm_reset": True}, hdr)[0] == 200
    assert e.db.pilot().name == "New Pilot"


def test_uninstalled_starter_is_refused(empty_server):
    e, c, hdr = empty_server
    e.settings.sim.installed_aircraft = "c172,b738"
    st, data, _ = c.call("POST", "/api/v1/career", {"name": "A", "home": "EGLL", "aircraft": "c152"}, hdr)
    assert st == 400 and "not installed" in data["error"]
