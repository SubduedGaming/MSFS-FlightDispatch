"""The Engine runs the whole backend without Qt: no event loop, no GUI thread."""
import http.client
import json
import subprocess
import sys
import threading
import time

import pytest

from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.server.engine import Engine
from skydispatch.web.server import WebRemote

TOKEN = "headless-code"


@pytest.fixture
def engine(tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = "http://127.0.0.1:9/v1"          # nothing listens: exercises the offline path
    s.ai.timeout_s = 1
    s.ui.first_run_complete = True
    e = Engine(s, Database(tmp_path / "e.db"))
    e.main.run(lambda: e.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000))
    yield e
    e.shutdown()


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_importing_the_engine_does_not_load_qt():
    code = ("import sys, skydispatch.server.engine, skydispatch.web.server, skydispatch.web.api;"
            "sys.exit(1 if any(m.startswith('PySide6') for m in sys.modules) else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_run_async_calls_back_on_the_engine_thread(engine):
    seen = []
    engine.run_async(lambda: threading.current_thread().name,
                     lambda name: seen.append((name, threading.current_thread().name)))
    assert wait_for(lambda: seen)
    worker, callback = seen[0]
    assert worker.startswith("skydispatch-work") and callback == "skydispatch-engine"


def test_run_async_reports_errors_and_ordered_tasks_keep_order(engine):
    errors, order = [], []
    engine.run_async(lambda: 1 / 0, None, errors.append)
    for i in range(5):
        engine.run_async(lambda i=i: (time.sleep(0.03 * (5 - i)), i)[1], order.append, ordered=True)
    assert wait_for(lambda: errors and len(order) == 5)
    assert "division" in errors[0] and order == [0, 1, 2, 3, 4]


def test_sim_states_reach_the_career_on_the_engine_thread(engine):
    threads = []
    engine.sim_state.connect(lambda s: threads.append(threading.current_thread().name))
    engine.main.run(engine.start_sim)
    assert wait_for(lambda: len(threads) >= 3)
    assert set(threads) == {"skydispatch-engine"}


def test_chat_goes_offline_gracefully_and_publishes_events(engine):
    toasts, status, changed = [], [], []
    engine.ai_status.connect(lambda ok, msg: status.append(ok))
    engine.thread_changed.connect(changed.append)
    engine.main.run(lambda: engine.ask("hello", "general"))
    assert wait_for(lambda: status)
    assert status[0] is False and "general" in changed
    assert [m["role"] for m in engine.db.messages(10, "general")][:1] == ["user"]


def test_career_events_are_forwarded(engine):
    got = []
    engine.career_event.connect(lambda name, payload: got.append(name))
    engine.main.run(engine.career.refresh_market)
    engine.db.add_job(kind="passenger", title="Hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2, cargo_lb=0,
                      client="Acme", briefing="b", payout=900.0, min_category="piston", min_runway_ft=0,
                      min_reputation=0, deadline_minutes=120, status="offered",
                      created_at="2026-01-01T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00")
    jid = engine.db.jobs("offered")[0].id
    engine.main.run(lambda: engine.career.accept_job(jid, engine.db.hangar()[0].id))
    assert wait_for(lambda: "job_accepted" in got)


def test_web_remote_serves_the_api_from_a_headless_engine(engine):
    remote = WebRemote(engine, "127.0.0.1", 0, TOKEN)
    remote.start()
    try:
        port = remote._server.server_address[1]

        def call(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, json.dumps(body) if body is not None else None, headers or {})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        st, hdrs, _ = call("POST", "/api/login", {"token": TOKEN}, {"X-SkyDispatch": "1"})
        assert st == 200
        cookie = {"Cookie": hdrs["Set-Cookie"].split(";")[0]}
        st, _, data = call("GET", "/api/state", headers=cookie)
        assert st == 200 and json.loads(data)["pilot"]["name"] == "Test Pilot"
        assert call("GET", "/api/dashboard", headers=cookie)[0] == 200
    finally:
        remote.stop()


def test_shutdown_is_idempotent_and_stops_the_engine_thread(tmp_path):
    e = Engine(Settings(), Database(tmp_path / "s.db"))
    e.shutdown()
    e.shutdown()
    assert not e.main._thread.is_alive()
