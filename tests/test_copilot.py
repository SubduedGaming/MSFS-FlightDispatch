import dataclasses
import json

import httpx
import pytest

from skydispatch.ai.llm import LMStudioClient
from skydispatch.copilot import advice
from skydispatch.copilot.copilot import CoPilot, THREAD
from skydispatch.copilot.monitor import CopilotMonitor
from skydispatch.data.aircraft import get_type
from skydispatch.db.models import Airport
from skydispatch.flight.recorder import Live
from skydispatch.sim.base import SimState
from skydispatch.sim.simulated import SimulatedProvider

C172 = get_type("c172")
DEST = Airport("EGHI", "Southampton", "", "GB", 50.95, -1.36, 44, 5653, "S")


def offline_copilot(career):
    def boom(req):
        raise httpx.ConnectError("down")
    return CoPilot(career, career.settings, LMStudioClient(career.settings.ai, transport=httpx.MockTransport(boom)))


# ---- pure advice -------------------------------------------------------------------
def test_top_of_descent_three_to_one():
    assert advice.tod_distance_nm(10000, 1000) == pytest.approx(27)
    assert advice.tod_distance_nm(500, 1000) == 0
    assert advice.descent_rate_fpm(9000, 27, 270) == pytest.approx(9000 / (27 / 270 * 60))


def test_fuel_report_levels():
    ok = advice.fuel_report(40, 9, 100, 110)           # 267 min endurance, 55 min needed
    assert ok.ok and ok.margin_min > 200
    tight = advice.fuel_report(5, 9, 60, 110)          # 33 min endurance, 33 needed
    assert not tight.ok and "too tight" in tight.text
    assert "hours" in advice.fuel_report(20, 10, None, 0).text


def test_descent_plan_before_and_inside_tod():
    before = advice.descent_plan(6500, 44, 60, 110, C172)
    assert "Top of descent in about" in before
    inside = advice.descent_plan(6500, 44, 8, 110, C172)
    assert "descend now" in inside
    assert "No descent needed" in advice.descent_plan(1100, 44, 20, 100, C172)


def test_approach_brief_warns_on_short_runway():
    short = Airport("EGXX", "Tiny", "", "GB", 51, 0, 100, 1800, "S")
    text = advice.approach_brief(short, get_type("c208"))
    assert "short for us" in text and "knots" in text
    assert "No destination" in advice.approach_brief(None, None)


def test_checklists_follow_the_phase_and_aircraft_class():
    _, piston = advice.checklist_for_phase("parked", C172)
    _, jet = advice.checklist_for_phase("parked", get_type("a320"))
    assert any("Mixture" in i for i in piston) and any("boost pumps" in i for i in jet)
    name, items = advice.checklist_for_phase("descent", C172, alt_agl_ft=1200, on_ground=False)
    assert name == "landing" and any("Gear down" in i for i in items)
    assert advice.checklist_for_phase("cruise", C172)[0] == "cruise"


# ---- monitor -----------------------------------------------------------------------
def st(**kw):
    base = dict(timestamp=0, on_ground=False, alt_msl=3000, alt_agl=3000, ias=100, gs=105, vs=0, fuel_gal=40,
                title="Cessna 172", engine_running=True)
    base.update(kw)
    return SimState(**base)


def live(remaining=50.0, phase="cruise"):
    return Live(phase=phase, remaining_nm=remaining)


def test_monitor_callouts_fire_once_and_are_throttled():
    m = CopilotMonitor(min_gap_s=7)
    assert [c.key for c in m.feed(st(alt_agl=100, vs=600), live(), C172, DEST, 0)] == ["positive_rate"]
    assert m.feed(st(alt_agl=200, vs=600), live(), C172, DEST, 1) == []             # once only
    # The 500 ft call comes 2 s after the 1000 ft call: it is held back (not lost) and released later.
    near = live(remaining=5, phase="descent")
    assert [c.key for c in m.feed(st(alt_agl=1000, vs=-500), near, C172, DEST, 100)] == ["app1000"]
    assert m.feed(st(alt_agl=500, vs=-500), near, C172, DEST, 102) == []
    assert [c.key for c in m.feed(st(alt_agl=490, vs=-500), near, C172, DEST, 110)] == ["app500"]


def test_monitor_sink_rate_is_urgent_and_repeats_slowly():
    m = CopilotMonitor(min_gap_s=7)
    near = live(remaining=4, phase="descent")
    first = [c.key for c in m.feed(st(alt_agl=800, vs=-1500), near, C172, DEST, 10)]
    assert "sink_rate" in first
    assert "sink_rate" not in [c.key for c in m.feed(st(alt_agl=700, vs=-1500), near, C172, DEST, 12)]   # rate-limited
    assert "sink_rate" in [c.key for c in m.feed(st(alt_agl=600, vs=-1500), near, C172, DEST, 30)]


def test_monitor_fuel_and_approach_speed():
    m = CopilotMonitor()
    keys = [c.key for c in m.feed(st(fuel_gal=4, alt_agl=3000), live(remaining=80), C172, DEST, 0)]
    assert keys == ["fuel_critical"]
    m2 = CopilotMonitor(min_gap_s=7)
    approach = live(remaining=5, phase="descent")
    got = [c.key for c in m2.feed(st(alt_agl=900, ias=120, vs=-300), approach, C172, DEST, 0)]
    got += [c.key for c in m2.feed(st(alt_agl=880, ias=118, vs=-300), approach, C172, DEST, 20)]
    assert "app_speed" in got


def test_top_of_descent_callouts_when_still_level():
    m = CopilotMonitor(min_gap_s=0)
    tod = advice.tod_distance_nm(6500, DEST.elevation_ft + 1000)
    keys = [c.key for c in m.feed(st(alt_msl=6500, alt_agl=6456, vs=0), live(remaining=tod + 6), C172, DEST, 0)]
    keys += [c.key for c in m.feed(st(alt_msl=6500, alt_agl=6456, vs=0), live(remaining=tod + 0.2), C172, DEST, 10)]
    assert keys == ["tod_near", "tod_now"]
    # already descending when we reach top of descent: no "start down" call
    m2 = CopilotMonitor(min_gap_s=0)
    assert [c.key for c in m2.feed(st(alt_msl=6000, alt_agl=5956, vs=-600), live(remaining=tod - 2), C172, DEST, 0)] == []


def test_monitor_rearms_after_landing():
    m = CopilotMonitor(min_gap_s=0)
    m.feed(st(alt_agl=100, vs=600), live(), C172, DEST, 0)
    m.feed(st(on_ground=True, alt_agl=0), live(), C172, DEST, 5)
    assert [c.key for c in m.feed(st(alt_agl=100, vs=600), live(), C172, DEST, 10)] == ["positive_rate"]


# ---- copilot: offline + LLM --------------------------------------------------------
def start_flight(career):
    """Start a simulated job and feed enough samples that the recorder is running mid-flight."""
    jid = career.db.add_job(kind="passenger", title="Hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2,
                            payout=900, expires_at="2999-01-01T00:00:00+00:00", deadline_minutes=300)
    career.accept_job(jid, career.db.hangar()[0].id)
    p = SimulatedProvider(speed=10)
    o = career.db.airport("EGLL")
    p.set_position(o.lat, o.lon, "Cessna 172 Skyhawk", fuel_gal=40, elev_ft=o.elevation_ft)
    p.cruise_alt = 4500
    d = career.db.airport("EGHI")
    p.fly(d.lat, d.lon, d.elevation_ft)
    return p


def step(career, p, clock, dt=2.0):
    p._step(dt)
    clock += dt / p.speed
    snap = dataclasses.replace(p._state, timestamp=clock, sim_time_scale=p.speed)
    career.feed(snap)
    return clock, snap


def test_offline_copilot_answers_from_live_data(career):
    cp = offline_copilot(career)
    assert not cp.in_flight()
    assert "Standing by" in cp.offline_answer("hello there")
    p = start_flight(career)
    clock = 1000.0
    for _ in range(900):
        clock, snap = step(career, p, clock)
        if p._plan and p._plan["phase"] == "cruise":
            break
    assert cp.in_flight()
    assert "feet" in cp.ask("how are we doing?") and "knots" in cp.ask("status")
    assert "gallons" in cp.ask("what's our fuel looking like")
    assert "Top of descent" in cp.ask("when do we start down?") or "descent" in cp.ask("when do we start down?").lower()
    assert "Southampton" in cp.ask("brief the approach") or "EGHI" in cp.ask("brief the approach")
    assert "checklist" in cp.ask("run the checklist").lower()
    msgs = career.db.messages(50, THREAD)
    assert msgs[0]["role"] == "user" and all(m["role"] in ("user", "assistant") for m in msgs)


def test_llm_copilot_gets_live_data_in_prompt(career):
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        seen["system"] = body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                      "content": "**Descend** at 500 feet per minute."}}]})

    cp = CoPilot(career, career.settings, LMStudioClient(career.settings.ai, transport=httpx.MockTransport(handler)))
    p = start_flight(career)
    clock = 1000.0
    for _ in range(600):
        clock, _s = step(career, p, clock)
    reply = cp.ask("should I start down?")
    assert reply == "Descend at 500 feet per minute."                              # markdown stripped
    ctx = json.loads(seen["system"].split("LIVE DATA: ")[1])
    assert ctx["flight_in_progress"] and ctx["destination"]["icao"] == "EGHI"
    assert ctx["aircraft"]["name"] == "Cessna 172 Skyhawk" and "altitude_msl_ft" in ctx["live"]
    assert "Sam Ortega" in seen["system"]


def test_full_flight_produces_the_expected_callouts(career):
    cp = offline_copilot(career)
    p = start_flight(career)
    p.cruise_alt = 6500
    clock, keys = 1000.0, []
    for _ in range(6000):
        clock, snap = step(career, p, clock)
        keys += [c.key for c in cp.callouts(snap, clock)]
        if career.last_settlement:
            break
    assert career.last_settlement is not None
    assert keys[0] == "positive_rate"
    assert "tod_near" in keys and "app1000" in keys and "app500" in keys
    assert keys.index("tod_near") < keys.index("app1000") < keys.index("app500")
    assert len(keys) == len(set(keys))                                               # no repeats in a normal flight
    assert "sink_rate" not in keys                                                   # smooth flight, no nagging


def test_callouts_can_be_disabled_and_need_a_flight(career):
    cp = offline_copilot(career)
    assert cp.callouts(st(alt_agl=100, vs=600), 0) == []                             # no flight in progress
    p = start_flight(career)
    clock = 1000.0
    for _ in range(100):
        clock, snap = step(career, p, clock)
    career.settings.ai.copilot_callouts = False
    assert cp.callouts(snap, clock) == []
