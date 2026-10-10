import http.client
import json
import threading
import time

import pytest

from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.ui.context import AppContext
from skydispatch.web.api import RemoteApi
from skydispatch.web.server import WebRemote, session_value

TOKEN = "test-code-123"


@pytest.fixture
def ctx(qtbot, tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = "http://127.0.0.1:9/v1"
    s.ai.timeout_s = 1
    s.ui.first_run_complete = True
    c = AppContext(s, Database(tmp_path / "c.db"))
    c.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000)
    yield c
    c.stop_web()
    c.shutdown()


@pytest.fixture
def api(ctx):
    return RemoteApi(ctx)


def get(api, path, **q):
    return api.handle("GET", path, {k: str(v) for k, v in q.items()}, None)


def post(api, path, **body):
    return api.handle("POST", path, {}, body)


# ---------------------------------------------------------------------------- API routes
def test_state_and_dashboard(api):
    st, data = get(api, "/api/state")
    assert st == 200 and data["has_career"] and data["pilot"]["name"] == "Test Pilot"
    st, d = get(api, "/api/dashboard")
    assert st == 200 and d["hello"].startswith("Welcome back") and len(d["tiles"]) == 6 and d["job"] is None


def test_unknown_route_and_wrong_method(api):
    assert get(api, "/api/nope")[0] == 404
    assert api.handle("POST", "/api/state", {}, {})[0] == 405


def test_no_career_gives_a_clear_error(qtbot, tmp_path):
    ctx = AppContext(Settings(), Database(tmp_path / "empty.db"))
    try:
        st, data = RemoteApi(ctx).handle("GET", "/api/dashboard", {}, None)
        assert st == 409 and "setup" in data["error"].lower()
        assert RemoteApi(ctx).handle("GET", "/api/state", {}, None)[1]["has_career"] is False
    finally:
        ctx.shutdown()


def test_market_accept_and_flight_page(ctx, api):
    jid = ctx.db.add_job(kind="passenger", title="Web hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2,
                         cargo_lb=0, client="Acme", briefing="b", payout=900.0, min_category="piston", min_runway_ft=0,
                         min_reputation=0, deadline_minutes=120, status="offered",
                         created_at="2026-01-01T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00")
    st, m = get(api, "/api/market")
    assert st == 200 and any(j["id"] == jid for j in m["jobs"])
    st, detail = get(api, f"/api/market/{jid}")
    assert st == 200 and detail["planes"] and detail["planes"][0]["ok"]
    st, _ = post(api, f"/api/market/{jid}/accept", aircraft_id=detail["planes"][0]["id"])
    assert st == 200
    st, f = get(api, "/api/flight")
    assert st == 200 and f["has_job"] and f["route"]["from"]["icao"] == "EGLL"
    # accepting a second job while one is active is a rule violation -> 400 with a message, not a crash
    st, err = post(api, f"/api/market/{jid}/accept", aircraft_id=detail["planes"][0]["id"])
    assert st == 400 and err["error"]
    assert post(api, "/api/job/abandon")[0] == 200
    assert get(api, "/api/flight")[1]["has_job"] is False


def test_hangar_buy_refuel_sell(ctx, api):
    ctx.db.add_transaction(500_000, "career", "test funds")
    st, dealer = get(api, "/api/dealer")
    assert st == 200 and dealer["aircraft"]
    before = len(get(api, "/api/hangar")[1]["fleet"])
    st, bought = post(api, "/api/hangar/buy", type_id="c172", location="EGLL", nickname="Webby", used=True)
    assert st == 200
    fleet = get(api, "/api/hangar")[1]["fleet"]
    assert len(fleet) == before + 1
    new = next(a for a in fleet if a["registration"] == bought["registration"])
    assert new["nickname"] == "Webby"
    assert post(api, f"/api/hangar/{new['id']}/rename", nickname="Renamed")[0] == 200
    assert post(api, f"/api/hangar/{new['id']}/sell")[0] == 200
    assert len(get(api, "/api/hangar")[1]["fleet"]) == before
    st, err = post(api, "/api/hangar/buy", type_id="bogus", location="EGLL")
    assert st == 400
    assert post(api, "/api/hangar/9999/refuel")[0] == 404


def test_messenger_threads_and_actions(ctx, api, monkeypatch):
    st, t = get(api, "/api/threads")
    assert st == 200 and t["threads"][0]["id"] == "general"
    st, th = get(api, "/api/thread/general")
    assert st == 200 and th["messages"] and th["name"]
    assert get(api, "/api/thread/employer:nobody")[0] == 404
    sent = []
    monkeypatch.setattr(ctx, "ask", lambda text, thread="general": sent.append((text, thread)))
    assert post(api, "/api/thread/general/send", text="  hello  ")[0] == 200
    assert sent == [("hello", "general")]
    assert post(api, "/api/thread/general/send", text="   ")[0] == 400
    assert post(api, "/api/thread/general/clear")[0] == 200


def test_job_board_apply_and_resign(api):
    st, jb = get(api, "/api/jobboard")
    assert st == 200 and jb["employers"] and len(jb["tiles"]) == 4
    st, e = get(api, "/api/employer/bluebird")
    assert st == 200 and e["name"] and "checks" in e
    assert get(api, "/api/employer/nope")[0] == 404
    st, r = post(api, "/api/employer/bluebird/apply")
    assert st == 200 and r["accepted"] and r["thread"] == "employer:bluebird"
    assert get(api, "/api/employer/bluebird")[1]["employed"] is True
    assert post(api, "/api/employer/bluebird/resign")[0] == 200


def test_logbook_finance_settings(ctx, api):
    st, lb = get(api, "/api/logbook")
    assert st == 200 and lb["flights"] == []
    csv_status, csv_body = get(api, "/api/logbook.csv")
    assert csv_status == 200 and str(csv_body).startswith("id,started")
    assert get(api, "/api/logbook/12345")[0] == 404
    st, fin = get(api, "/api/finance")
    assert st == 200 and len(fin["tiles"]) == 4
    st, s = get(api, "/api/settings")
    assert st == 200 and "auto_speak_replies" in s
    assert post(api, "/api/settings", auto_speak_replies=False, units_distance="km", units_weight="bogus")[0] == 200
    assert ctx.settings.voice.auto_speak_replies is False and ctx.settings.ui.units_distance == "km"
    assert ctx.settings.ui.units_weight == "lb"                      # invalid values are ignored


def test_events_are_published(ctx, api):
    seen = []
    api2 = RemoteApi(ctx, publish=lambda e, d: seen.append((e, d)))
    ctx.toast.emit("good", "hi")
    ctx.thread_changed.emit("general")
    ctx.career_event.emit("flight_event", {"kind": "takeoff", "detail": "airborne"})
    kinds = [e for e, _ in seen]
    assert {"toast", "thread", "career"} <= set(kinds)
    assert any(d.get("kind") == "takeoff" for e, d in seen if e == "career")
    assert api2._flight_events[-1]["detail"] == "airborne"


# ---------------------------------------------------------------------------- HTTP layer
def request(port, method, path, body=None, headers=None, results=None):
    def run():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        results.append((r.status, dict(r.getheaders()), r.read()))
        c.close()
    results = [] if results is None else results
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return results


@pytest.fixture
def remote(qtbot, ctx):
    r = WebRemote(ctx, "127.0.0.1", 0, TOKEN)
    r.start()
    port = r._server.server_address[1]
    yield r, port
    r.stop()


def call(qtbot, port, method, path, body=None, headers=None):
    out = request(port, method, path, body, headers)
    qtbot.waitUntil(lambda: bool(out), timeout=8000)
    status, hdrs, data = out[0]
    return status, hdrs, data


def login(qtbot, port):
    st, hdrs, _ = call(qtbot, port, "POST", "/api/login", {"token": TOKEN}, {"X-SkyDispatch": "1"})
    assert st == 200
    return {"Cookie": hdrs["Set-Cookie"].split(";")[0], "X-SkyDispatch": "1"}


def test_static_page_is_public_but_api_needs_login(qtbot, remote):
    _, port = remote
    st, hdrs, data = call(qtbot, port, "GET", "/")
    assert st == 200 and b"SkyDispatch" in data and "Content-Security-Policy" in hdrs
    assert call(qtbot, port, "GET", "/app.js")[0] == 200
    assert call(qtbot, port, "GET", "/api/state")[0] == 401
    st, _, data = call(qtbot, port, "GET", "/api/ping")
    assert st == 200 and json.loads(data)["authed"] is False


def test_login_flow_and_csrf_header(qtbot, remote):
    _, port = remote
    assert call(qtbot, port, "POST", "/api/login", {"token": "wrong"}, {"X-SkyDispatch": "1"})[0] == 401
    assert call(qtbot, port, "POST", "/api/login", {"token": TOKEN})[0] == 403          # no CSRF header
    hdrs = login(qtbot, port)
    assert "HttpOnly" in call(qtbot, port, "POST", "/api/login", {"token": TOKEN}, {"X-SkyDispatch": "1"})[1]["Set-Cookie"]
    st, _, data = call(qtbot, port, "GET", "/api/state", headers=hdrs)
    assert st == 200 and json.loads(data)["pilot"]["name"] == "Test Pilot"
    assert call(qtbot, port, "POST", "/api/job/abandon", {}, {"Cookie": hdrs["Cookie"]})[0] == 403
    assert call(qtbot, port, "POST", "/api/job/abandon", {}, hdrs)[0] == 200
    assert call(qtbot, port, "GET", "/api/state", headers={"Cookie": "sd_session=forged"})[0] == 401


def test_changing_the_code_signs_everyone_out(qtbot, remote):
    r, port = remote
    hdrs = login(qtbot, port)
    r.token = "a-new-code"
    assert call(qtbot, port, "GET", "/api/state", headers=hdrs)[0] == 401
    assert session_value("a") != session_value("b")


def test_empty_token_never_authenticates(qtbot, ctx):
    r = WebRemote(ctx, "127.0.0.1", 0, "")
    r.start()
    try:
        port = r._server.server_address[1]
        assert call(qtbot, port, "POST", "/api/login", {"token": ""}, {"X-SkyDispatch": "1"})[0] == 401
        assert call(qtbot, port, "GET", "/api/state", headers={"Cookie": f"sd_session={session_value('')}"})[0] == 401
    finally:
        r.stop()


def test_login_rate_limit(qtbot, remote):
    _, port = remote
    codes = [call(qtbot, port, "POST", "/api/login", {"token": f"bad{i}"}, {"X-SkyDispatch": "1"})[0] for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert call(qtbot, port, "POST", "/api/login", {"token": TOKEN}, {"X-SkyDispatch": "1"})[0] == 429


def test_oversized_and_bad_bodies_are_rejected(qtbot, remote):
    _, port = remote
    hdrs = login(qtbot, port)
    assert call(qtbot, port, "POST", "/api/job/abandon", {"x": "y" * 70_000}, hdrs)[0] == 400
    out = []

    def raw():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/job/abandon", "not json", {**hdrs, "Content-Type": "application/json"})
        out.append(c.getresponse().status)
    threading.Thread(target=raw, daemon=True).start()
    qtbot.waitUntil(lambda: bool(out), timeout=8000)
    assert out[0] == 400


def test_live_stream_delivers_events(qtbot, remote, ctx):
    r, port = remote
    hdrs = login(qtbot, port)
    lines: list[str] = []
    ready = threading.Event()

    def listen():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/stream", headers=hdrs)
        resp = c.getresponse()
        ready.set()
        while True:
            line = resp.fp.readline().decode()
            if not line:
                break
            lines.append(line.strip())
            if any(l.startswith("data:") and "stream-test" in l for l in lines):
                break
        c.close()
    threading.Thread(target=listen, daemon=True).start()
    qtbot.waitUntil(lambda: ready.is_set() and r.client_count == 1, timeout=8000)
    ctx.toast.emit("info", "stream-test")
    qtbot.waitUntil(lambda: any("stream-test" in l for l in lines), timeout=8000)
    assert "event: toast" in lines
    r.stop()                                      # closing the server ends open streams promptly
    qtbot.waitUntil(lambda: r.client_count == 0, timeout=8000)


def test_stream_requires_login(qtbot, remote):
    _, port = remote
    assert call(qtbot, port, "GET", "/api/stream")[0] == 401


def test_context_starts_and_stops_the_remote(qtbot, ctx):
    ctx.settings.remote.enabled = True
    ctx.settings.remote.port = 0
    ctx.start_web()
    assert ctx.web.running and ctx.settings.remote.token            # a code is generated on first start
    ctx.settings.remote.enabled = False
    ctx.start_web()
    assert not ctx.web.running


def test_port_in_use_reports_an_error(qtbot, ctx):
    a = WebRemote(ctx, "0.0.0.0", 0, TOKEN)           # same wildcard address the app uses (macOS lets 127.0.0.1 coexist)
    a.start()
    try:
        ctx.settings.remote.enabled = True
        ctx.settings.remote.port = a._server.server_address[1]
        ctx.start_web()
        assert not ctx.web.running and "port" in ctx.web_error.lower()
    finally:
        a.stop()


def test_plan_endpoints(ctx, api, monkeypatch):
    assert get(api, "/api/plan")[1] == {"has_job": False}
    st, err = get(api, "/api/plan/link")
    assert st == 400 and "job" in err["error"].lower()
    ctx.db.add_job(kind="passenger", title="Plan hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2, cargo_lb=100,
                   client="Acme", briefing="b", payout=900.0, min_category="piston", min_runway_ft=0, min_reputation=0,
                   deadline_minutes=120, status="offered", created_at="2026-01-01T00:00:00+00:00",
                   expires_at="2099-01-01T00:00:00+00:00")
    jid = ctx.db.jobs("offered")[0].id
    assert post(api, f"/api/market/{jid}/accept", aircraft_id=ctx.db.hangar()[0].id)[0] == 200
    st, plan = get(api, "/api/plan")
    assert st == 200 and plan["has_job"] and plan["ofp"] is None and plan["loadout"]["fuel_source"]
    st, link = get(api, "/api/plan/link")
    assert st == 200 and link["url"].startswith("https://dispatch.simbrief.com/") and "orig=EGLL" in link["url"]
    calls = []
    monkeypatch.setattr(ctx, "import_simbrief", lambda: calls.append("import"))
    monkeypatch.setattr(ctx, "sync_loadout", lambda auto=False: calls.append("sync"))
    assert post(api, "/api/plan/import")[0] == 200 and post(api, "/api/plan/sync")[0] == 200
    assert calls == ["import", "sync"]
    assert post(api, "/api/settings", simbrief_user=" georgeh ", auto_sync_loadout=False)[0] == 200
    assert ctx.settings.plan.simbrief_user == "georgeh" and ctx.settings.plan.auto_sync_loadout is False


# ---------------------------------------------------------------------------- v1.4 game redesign
def test_job_board_shows_hiring_state(ctx, api):
    st, jb = get(api, "/api/jobboard")
    states = {e["id"]: e["state"] for e in jb["employers"]}
    assert states["bluebird"].startswith("Recruiting") and states["atlas"] == "Not recruiting"
    st, e = get(api, "/api/employer/atlas")
    assert e["can_apply"] is False and "Not recruiting" in e["hiring"]
    assert any(c["label"] == "Instrument rating" for c in e["checks"])
    assert get(api, "/api/employer/bluebird")[1]["can_apply"] is True
    st, err = post(api, "/api/employer/atlas/apply")
    assert st == 400 and "not recruiting" in err["error"].lower()


def test_training_endpoints(ctx, api):
    from skydispatch.pilot import quals
    st, t = get(api, "/api/training")
    assert st == 200 and {c["kind"] for c in t["certs"]} == {"licence", "medical"} and len(t["courses"]) == 5
    assert t["bills"] and t["total"].startswith("$")
    quals.apply_experience_preset(ctx.db, "student", days_ago=3)
    ctx.db.add_transaction(20000, "t", "funds")
    assert post(api, "/api/training/start", course_id="ir")[0] == 200
    assert ctx.career.credentials.active_course()[0].id == "ir"
    ctx.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    assert post(api, "/api/training/renew", kind="medical")[0] == 200
    assert ctx.career.credentials.valid("medical")


def test_dashboard_carries_alerts_and_freelance_is_owner_only(ctx, api):
    ctx.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    d = get(api, "/api/dashboard")[1]
    assert any(a["level"] == "bad" and "medical" in a["text"].lower() for a in d["alerts"])
    assert get(api, "/api/market")[1]["owner_operator"] is True
    ctx.career.hangar.sell(ctx.db.hangar()[0].id)
    m = get(api, "/api/market")[1]
    assert m["owner_operator"] is False and m["jobs"] == []
