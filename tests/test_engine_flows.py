"""Gameplay flows driven through the Engine alone (no window): what the phone's screens do by calling the API.

These replace the checks that used to sit behind the desktop pages in test_ui.py."""
from conftest import AI_URL, NO_AI_URL  # noqa: F401
import time

import pytest

from skydispatch.core.config import Settings
from skydispatch.data.employers import get_employer
from skydispatch.db.database import Database
from skydispatch.pilot import quals
from skydispatch.server.engine import Engine


@pytest.fixture
def engine(tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = AI_URL           # nothing listens: exercises the offline path
    s.ai.timeout_s = 1
    s.ui.first_run_complete = True
    e = Engine(s, Database(tmp_path / "f.db"))
    e.main.run(lambda: e.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000))
    yield e
    e.shutdown()


def wait_for(cond, timeout=10.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for {what}")


def on_engine(e, fn):
    return e.main.run(fn)


def fly_the_active_job(e, timeout):
    e.settings.sim.simulated_speed = 60
    on_engine(e, e.start_sim)
    wait_for(lambda: e.simulated is not None and e.sim_connected, 5, "the simulator to connect")
    assert on_engine(e, e.demo_fly_active_job) is None
    wait_for(lambda: e.career.last_settlement is not None, timeout, "the flight to finish")
    return e.career.last_settlement


def test_accept_a_freelance_job_and_fly_it(engine):
    e = engine
    jid = e.db.add_job(kind="passenger", title="Hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2,
                       payout=900.0, expires_at="2999-01-01T00:00:00+00:00", deadline_minutes=300)
    on_engine(e, lambda: e.career.accept_job(jid, e.db.hangar()[0].id))
    assert e.db.active_job().id == jid
    s = fly_the_active_job(e, 60)
    assert s.metrics.outcome == "completed" and e.db.job(jid).status == "completed"


def test_asking_the_dispatcher_offline_leaves_a_clear_message(engine):
    e = engine
    e.settings.ai.base_url = NO_AI_URL
    on_engine(e, lambda: e.ask("hello", "general"))
    wait_for(lambda: any(m["role"] == "system" for m in e.db.messages(50, "general")), what="the offline message")
    assert any(m["role"] == "user" and m["content"] == "hello" for m in e.db.messages(50, "general"))


def test_hangar_refuel_and_repair(engine):
    e = engine
    a = e.db.hangar()[0]
    e.db.update_aircraft(a.id, fuel_gal=5, condition=70)
    on_engine(e, lambda: e.career.hangar.refuel(a.id))
    assert e.db.aircraft(a.id).fuel_gal > 40
    on_engine(e, lambda: e.career.hangar.repair(a.id))
    assert e.db.aircraft(a.id).condition == 100


def test_applying_to_companies(engine):
    e = engine
    e.career.hiring.open("skyline", 5)                          # recruiting this week, but wants experience
    refused = on_engine(e, lambda: e.apply_to_employer("skyline"))
    assert not refused.accepted and not e.db.is_employed_by("skyline")
    hired = on_engine(e, lambda: e.apply_to_employer("bluebird"))     # no requirements
    assert hired.accepted and e.db.is_employed_by("bluebird")
    wait_for(lambda: e.db.last_message("employer:bluebird") is not None, what="the company's first message")


def test_a_dispatcher_assigns_a_flight_when_told_how_long_you_have(engine):
    e = engine
    on_engine(e, lambda: e.career.apply_to_employer("bluebird"))
    on_engine(e, lambda: e.dispatcher.start_thread(get_employer("bluebird"), "Welcome"))
    assert e.dispatcher.is_awaiting_availability("employer:bluebird")
    on_engine(e, lambda: e.answer_availability("employer:bluebird", 60))
    wait_for(lambda: e.db.active_job() is not None, what="an assigned flight")
    job = e.db.active_job()
    assert job.employer_id == "bluebird" and job.status == "accepted"


def test_copilot_answers_quick_actions_and_questions(engine):
    e = engine
    before = len(e.db.messages(50, "copilot"))
    on_engine(e, lambda: e.ask_copilot(quick="status"))
    wait_for(lambda: len(e.db.messages(50, "copilot")) >= before + 2, what="the copilot's status answer")
    # A free-text question goes to the AI model. None is running in the tests, so the pilot is told why (no canned reply).
    toasts = []
    e.toast.connect(lambda level, msg: toasts.append((level, msg)))
    e.settings.ai.base_url = NO_AI_URL
    on_engine(e, lambda: e.ask_copilot(text="what's our fuel?"))
    wait_for(lambda: any("could not answer" in m for _l, m in toasts), 15, "the co-pilot to say it could not answer")
    assert [m["role"] for m in e.db.messages(50, "copilot")][-1] == "user"


def test_installed_aircraft_detection_updates_the_settings(engine, tmp_path):
    e = engine
    base = tmp_path / "pk" / "Community" / "x" / "SimObjects" / "Airplanes" / "Asobo_C172sp"
    base.mkdir(parents=True)
    (base / "aircraft.cfg").write_text('[FLTSIM.0]\ntitle = "Cessna 172 Skyhawk"\n')
    e.settings.sim.packages_path = str(tmp_path / "pk")
    on_engine(e, lambda: e.detect_installed(force=True))
    wait_for(lambda: e.settings.sim.installed_aircraft == "c172", what="detection to finish")


def test_company_career_loop_end_to_end(engine):
    """Hire -> tell dispatcher your free time -> flight assigned -> fly it -> debrief, new question, callouts."""
    e = engine
    thread = "employer:bluebird"
    on_engine(e, lambda: e.career.apply_to_employer("bluebird"))
    on_engine(e, lambda: e.dispatcher.start_thread(get_employer("bluebird"), "Welcome aboard."))
    on_engine(e, lambda: e.answer_availability(thread, 30))           # a short window keeps the simulated flight quick
    wait_for(lambda: e.db.active_job() is not None, what="an assigned flight")
    job = e.db.active_job()
    assert job.employer_id == "bluebird" and job.status == "accepted"
    s = fly_the_active_job(e, 120)
    assert s.metrics.outcome == "completed" and s.employer_id == "bluebird" and s.costs == 0 and s.payout > 0
    wait_for(lambda: (e.db.last_message(thread) or {"kind": ""})["kind"] == "ask_time", 15, "the next question")
    assert e.dispatcher.is_awaiting_availability(thread)
    wait_for(lambda: e.db.flight(s.flight_id).debrief == "Roger that, Captain.", 15, "the model-written debrief")
    assert [m for m in e.db.messages(80, "copilot") if m["kind"] == "callout"], "the copilot said nothing in flight"
    q = e.career.qualifications()
    assert q.recent_h > 0 and q.by_type_h
    assert e.db.pilot().location_icao == job.dest


def test_training_summary_and_buttons(engine):
    e = engine
    text = repr(on_engine(e, e.training_summary))
    assert "Pilot licence" in text and "Medical certificate" in text and "Instrument Rating" in text
    assert "Living costs" in text and "Hangar and insurance" in text
    summary = on_engine(e, e.training_summary)
    assert not next(c for c in summary["courses"] if c["id"] == "ir")["can_start"]      # 0 flight hours: not eligible
    quals.apply_experience_preset(e.db, "student", days_ago=3)                          # 40 h
    e.db.add_transaction(20000, "t", "funds")
    e.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    on_engine(e, lambda: e.renew_credential("medical"))
    assert e.career.credentials.valid("medical")
    assert next(c for c in on_engine(e, e.training_summary)["courses"] if c["id"] == "ir")["can_start"]
    on_engine(e, lambda: e.start_course("ir"))
    assert e.career.credentials.active_course()[0].id == "ir"
    assert next(c for c in on_engine(e, e.training_summary)["courses"] if c["id"] == "ir")["in_training"]


def test_alerts_follow_the_licence_state(engine):
    e = engine
    e.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    assert any("medical" in a["text"].lower() for a in on_engine(e, e.alerts))
    on_engine(e, lambda: e.career.credentials.renew("medical"))
    assert not any("expired" in a["text"].lower() for a in on_engine(e, e.alerts))


def test_news_reaches_the_ops_desk_and_notifications(engine):
    e = engine
    shown = []
    e.toast.connect(lambda level, msg: shown.append((level, msg)))
    e.career._fire("vacancy_opened", employer_id="harbour")                 # needs 10 h: a new pilot is not told
    e.career._fire("vacancy_opened", employer_id="bluebird")                # no requirements: worth a note
    wait_for(lambda: any("recruiting" in m for _, m in shown), 5, "the recruiting note")
    assert any("Bluebird Bush Air" in m["content"] for m in e.db.messages(20, "general"))
    assert not any("Harbour Light" in m["content"] for m in e.db.messages(20, "general"))
    e.career._fire("bills_charged", items=[("Living costs", 900.0)])
    e.career._fire("credential_expiring", kind="medical", label="Medical certificate", state="soon", days_left=5, fee=350)
    wait_for(lambda: any("expires in 5 days" in m for _, m in shown), 5, "the licence warning")
