import time
from types import SimpleNamespace

import httpx
import pytest

from skydispatch.core.config import Settings
from skydispatch.data.aircraft import get_type
from skydispatch.db.database import Database
from skydispatch.planning import loadout as lo
from skydispatch.planning import simbrief as sb
from skydispatch.sim.base import SimState
from skydispatch.sim.simconnect_provider import SimConnectProvider
from skydispatch.sim.simulated import SimulatedProvider
from skydispatch.ui.context import AppContext

C172 = get_type("c172")
A320 = get_type("a320")


def job(**kw):
    base = dict(id=7, origin="EGLL", dest="EGHI", pax=2, cargo_lb=100, distance_nm=60.0, employer_id=None, provided_type="")
    base.update(kw)
    return SimpleNamespace(**base)


def sample_ofp(units="LBS", origin="EGLL", dest="EGHI", status="Success", fuel=400.0):
    return {"fetch": {"status": status}, "params": {"units": units, "time_generated": str(int(time.time())), "static_id": "sd7"},
            "general": {"route": "DCT BKY DCT", "initial_altitude": "5000", "air_distance": "62", "route_distance": "63"},
            "origin": {"icao_code": origin}, "destination": {"icao_code": dest}, "alternate": {"icao_code": "EGKK"},
            "aircraft": {"icaocode": "C172", "reg": "G-ABCD"}, "times": {"est_time_enroute": "2700"},
            "fuel": {"plan_ramp": str(fuel), "enroute_burn": "120", "reserve": "90", "taxi": "5"},
            "weights": {"pax_count": "2", "cargo": "100", "payload": "480", "est_zfw": "1900", "est_tow": "2200"},
            "navlog": {"fix": [{"ident": "EGLL"}, {"ident": "BKY"}, {"ident": "EGHI"}]},
            "files": {"directory": "https://www.simbrief.com/ofp/flightplans/", "pdf": {"link": "EGLLEGHI.pdf"}}}


# ---------------------------------------------------------------------------- SimBrief
def test_dispatch_url_is_prefilled_for_the_job():
    url = sb.dispatch_url(job(), C172, "G-ABCD", "SKY 1", "sd7")
    assert url.startswith(sb.DISPATCH_URL + "?")
    for part in ("orig=EGLL", "dest=EGHI", "type=C172", "reg=G-ABCD", "fltnum=SKY1", "pax=2", "cargo=0.1", "static_id=sd7"):
        assert part in url


def test_parse_ofp_in_pounds_and_kilograms():
    o = sb.parse_ofp(sample_ofp())
    assert (o.origin, o.dest, o.alternate, o.aircraft) == ("EGLL", "EGHI", "EGKK", "C172")
    assert o.block_fuel_lb == 400 and o.ete_min == 45 and o.cruise_alt_ft == 5000 and o.pax == 2
    assert o.waypoints == ["EGLL", "BKY", "EGHI"] and o.pdf_url.endswith("EGLLEGHI.pdf")
    k = sb.parse_ofp(sample_ofp(units="KGS"))
    assert k.block_fuel_lb == pytest.approx(400 * 2.20462) and k.payload_lb == pytest.approx(480 * 2.20462)
    assert o.block_fuel_gal(C172) == pytest.approx(400 / 6.0)


def test_parse_ofp_reports_simbrief_errors():
    with pytest.raises(sb.SimBriefError, match="Unknown UserID"):
        sb.parse_ofp({"fetch": {"status": "Error: Unknown UserID"}})
    with pytest.raises(sb.SimBriefError):
        sb.parse_ofp({"fetch": {"status": "Success"}})


def test_ofp_matches_job_and_roundtrips():
    o = sb.parse_ofp(sample_ofp())
    assert o.matches(job()) and not o.matches(job(dest="EGLC"))
    assert sb.Ofp.from_json(o.to_json()) == o
    assert o.age_hours() < 0.1 and o.age_hours(time.time() + 7200) > 1.9


def test_fetch_latest(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kw):
        seen.update(url=url, params=params)
        return httpx.Response(200, json=sample_ofp(), request=httpx.Request("GET", url))
    monkeypatch.setattr(sb.httpx, "get", fake_get)
    assert sb.fetch_latest("georgeh", "sd7").origin == "EGLL"
    assert seen["params"] == {"json": "1", "username": "georgeh", "static_id": "sd7"}
    sb.fetch_latest("123456")
    assert seen["params"]["userid"] == "123456" and "username" not in seen["params"]
    with pytest.raises(sb.SimBriefError, match="username"):
        sb.fetch_latest("  ")


def test_fetch_latest_errors(monkeypatch):
    monkeypatch.setattr(sb.httpx, "get", lambda url, **kw: httpx.Response(
        400, json={"fetch": {"status": "Error: Unknown UserID"}}, request=httpx.Request("GET", url)))
    with pytest.raises(sb.SimBriefError, match="Unknown UserID"):
        sb.fetch_latest("nobody")

    def boom(url, **kw):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(sb.httpx, "get", boom)
    with pytest.raises(sb.SimBriefError, match="reach SimBrief"):
        sb.fetch_latest("x")
    monkeypatch.setattr(sb.httpx, "get", lambda url, **kw: httpx.Response(200, text="<html>", request=httpx.Request("GET", url)))
    with pytest.raises(sb.SimBriefError, match="unreadable"):
        sb.fetch_latest("x")


def test_only_simbrief_links_are_trusted():
    assert sb.is_trusted_link("https://www.simbrief.com/ofp/x.pdf")
    assert not sb.is_trusted_link("https://evilsimbrief.com/x") and not sb.is_trusted_link("http://www.simbrief.com/x")


# ---------------------------------------------------------------------------- loadout planning
def hangar(fuel):
    return SimpleNamespace(fuel_gal=fuel)


def test_hangar_aircraft_carries_the_fuel_it_has():
    plan = lo.plan_loadout(job(), C172, hangar(22.0))
    assert (plan.fuel_gal, plan.fuel_source, plan.pax, plan.cargo_lb) == (22.0, "hangar", 2, 100)
    assert plan.payload_lb == 2 * lo.PAX_LB + 100


def test_hangar_fuel_is_clamped_and_simbrief_shortfall_is_explained():
    assert lo.plan_loadout(job(), C172, hangar(999)).fuel_gal == C172.fuel_cap_gal
    plan = lo.plan_loadout(job(), C172, hangar(10.0), sb.parse_ofp(sample_ofp(fuel=600)))
    assert plan.fuel_gal == 10.0 and any("Refuel" in n for n in plan.notes)


def test_company_aircraft_uses_the_simbrief_block_fuel_or_an_estimate():
    j = job(employer_id="bluebird", provided_type="c172")
    ofp = sb.parse_ofp(sample_ofp(fuel=300))
    p = lo.plan_loadout(j, C172, None, ofp)
    assert p.fuel_source == "simbrief" and p.fuel_gal == pytest.approx(50.0)
    p2 = lo.plan_loadout(j, C172, None, None)
    assert p2.fuel_source == "estimate" and 0 < p2.fuel_gal <= C172.fuel_cap_gal
    wrong = sb.parse_ofp(sample_ofp(dest="EGLC"))
    assert lo.plan_loadout(j, C172, None, wrong).fuel_source == "estimate"      # a plan for another flight is ignored


def test_payload_is_limited_to_what_the_type_carries():
    p = lo.plan_loadout(job(pax=9, cargo_lb=5000), C172, hangar(20))
    assert p.pax == C172.pax and p.cargo_lb == C172.cargo_lb


# ---------------------------------------------------------------------------- station layout
def test_small_aircraft_layout_seats_then_baggage():
    w, notes = lo.station_weights(5, 2, 100)         # pilot, front, rear L, rear R, baggage
    assert w == {2: lo.PAX_LB, 3: lo.PAX_LB, 4: 0.0, 5: 100.0} and not notes
    assert 1 not in w                                # the pilot station is never touched


def test_airliner_layout_spreads_passengers_and_fills_the_holds():
    w, _ = lo.station_weights(14, 150, 1000)
    holds = (w[13], w[14])
    assert holds == (500.0, 500.0)
    cabin = [w[i] for i in range(2, 13)]
    assert sum(cabin) == pytest.approx(150 * lo.PAX_LB, rel=0.01)


def test_layout_edge_cases():
    assert lo.station_weights(1, 2, 0)[0] == {}
    w, notes = lo.station_weights(3, 2, 50)           # no baggage station
    assert 50.0 not in w.values() and any("no baggage" in n for n in notes)
    w, notes = lo.station_weights(4, 5, 0)            # more passengers than seats
    assert any("seats" in n for n in notes) and all(v <= lo.SEAT_MAX_LB for v in w.values())


# ---------------------------------------------------------------------------- writing to the sim
class FakeSim:
    def __init__(self, tanks, stations, accept=True, accept_after_writes=0):
        self.v = {f"FUEL_TANK_{t}_CAPACITY": c for t, c in tanks.items()}
        self.v["PAYLOAD_STATION_COUNT"] = stations
        for i in range(1, stations + 1):
            self.v[f"PAYLOAD_STATION_WEIGHT:{i}"] = 0.0
        self.accept, self.writes = accept, []
        self.accept_after = accept_after_writes           # ignore this many writes first (a sim that is still settling)

    def get(self, name):
        if name == "FUEL_TOTAL_QUANTITY":
            return sum(v for k, v in self.v.items() if k.endswith("_QUANTITY"))
        return self.v.get(name)

    def set(self, name, value):
        self.writes.append((name, value))
        if self.accept and len(self.writes) > self.accept_after:
            self.v[name] = value
        return self.accept


def test_apply_loadout_fills_tanks_proportionally_and_loads_stations():
    sim = FakeSim({"LEFT_MAIN": 20.0, "RIGHT_MAIN": 20.0}, 5)
    res = lo.apply_loadout(sim, lo.Loadout(30.0, 2, 100, "hangar"), C172, sleep=lambda s: None)
    assert sim.v["FUEL_TANK_LEFT_MAIN_QUANTITY"] == sim.v["FUEL_TANK_RIGHT_MAIN_QUANTITY"] == 15.0
    assert res.fuel_gal == 30.0 and res.payload_lb == 2 * lo.PAX_LB + 100
    assert not any(n.endswith(":1") for n, _ in sim.writes)           # pilot station untouched


def test_apply_loadout_never_exceeds_the_sim_tank_capacity():
    sim = FakeSim({"LEFT_MAIN": 10.0, "RIGHT_MAIN": 10.0}, 5)
    res = lo.apply_loadout(sim, lo.Loadout(50.0, 0, 0), C172, sleep=lambda s: None)
    assert res.fuel_gal == 20.0 and any("holds only" in m for m in res.messages)


def test_apply_loadout_with_nothing_writable_raises():
    with pytest.raises(lo.LoadoutError):
        lo.apply_loadout(FakeSim({}, 0), lo.Loadout(10.0, 1, 0), C172, sleep=lambda s: None)
    res = lo.apply_loadout(FakeSim({}, 5), lo.Loadout(10.0, 1, 0), C172, sleep=lambda s: None)
    assert any("fuel tanks" in m for m in res.messages)               # payload still loads without readable tanks


def test_a_sim_that_ignores_writes_is_reported_not_called_loaded():
    sim = FakeSim({"LEFT_MAIN": 20.0, "RIGHT_MAIN": 20.0}, 5, accept=False)
    with pytest.raises(lo.LoadoutError, match="did not accept"):
        lo.apply_loadout(sim, lo.Loadout(30.0, 2, 100, "hangar"), C172, sleep=lambda s: None)
    assert len(sim.writes) > 8                                   # it tried twice


def test_a_sim_that_applies_the_load_late_is_retried_once():
    sim = FakeSim({"LEFT_MAIN": 20.0, "RIGHT_MAIN": 20.0}, 5, accept_after_writes=6)    # first pass dropped
    res = lo.apply_loadout(sim, lo.Loadout(30.0, 2, 100, "hangar"), C172, sleep=lambda s: None)
    assert res.fuel_ok and res.payload_ok and res.fuel_gal == 30.0 and not res.messages


def test_partial_acceptance_is_flagged_without_failing():
    class FuelOnly(FakeSim):
        def set(self, name, value):
            self.writes.append((name, value))
            if "PAYLOAD" not in name:
                self.v[name] = value
            return True
    res = lo.apply_loadout(FuelOnly({"LEFT_MAIN": 40.0}, 5), lo.Loadout(30.0, 2, 100, "hangar"), C172, sleep=lambda s: None)
    assert res.fuel_ok and not res.payload_ok and any("payload change" in m for m in res.messages)


def test_diagnostics_read_everything_and_write_nothing():
    sim = FakeSim({"LEFT_MAIN": 20.0, "RIGHT_MAIN": 20.0}, 3)
    sim.v.update({"FUEL_TANK_LEFT_MAIN_QUANTITY": 12.0, "FUEL_TOTAL_QUANTITY_WEIGHT": 72.0, "TOTAL_WEIGHT": 1900.0,
                  "EMPTY_WEIGHT": 1500.0, "PAYLOAD_STATION_WEIGHT:1": 170.0})
    lines = lo.diagnose(sim, "Cessna 172 Skyhawk")
    text = "\n".join(lines)
    assert "Payload stations: 3" in text and "station 1: 170.0 lb" in text and "Fuel tanks found: 2" in text
    assert "tank LEFT_MAIN: 12.0 of 20.0 gal" in text and "empty 1,500 lb" in text
    assert sim.writes == []
    assert "manages its own loading" in "\n".join(lo.diagnose(sim, "PMDG 777F"))


# ---------------------------------------------------------------------------- when it is safe
def state(**kw):
    base = dict(on_ground=True, gs=0.0, engine_running=False, title="Cessna 172 Skyhawk", fuel_gal=20.0, payload_lb=500.0)
    base.update(kw)
    return SimState(**base)


def test_ready_problems():
    assert lo.ready_problem(state(), C172) is None
    assert "not connected" in lo.ready_problem(None, C172)
    assert "parked" in lo.ready_problem(state(on_ground=False), C172)
    assert "parked" in lo.ready_problem(state(gs=20), C172)
    assert lo.ready_problem(state(engine_running=True), C172) is None            # MSFS often starts with engines on
    assert "not the contract's aircraft" in lo.ready_problem(state(title="Airbus A320neo"), C172)
    assert "not the contract's aircraft" in lo.ready_problem(state(title="Some Mod"), C172)


def test_add_ons_that_manage_their_own_loading_are_left_alone():
    from skydispatch.data.aircraft import get_type
    b77f = get_type("b77f")
    plan = lo.Loadout(20000.0, 0, 90000.0, "estimate")
    msg = lo.ready_problem(state(title="PMDG 777F"), b77f, plan)
    assert "EFB" in msg and "SimBrief" in msg and "20,000 gal" not in msg and "20000 gal" in msg and "90,000 lb" in msg
    assert lo.ready_problem(state(title="PMDG 777F"), b77f) is not None


def test_compare_with_unknown_payload_is_not_a_false_mismatch():
    plan = lo.Loadout(20.0, 2, 100)
    assert lo.compare(plan, state(fuel_gal=20.0, payload_lb=None)).matches


def test_compare_plan_with_sim():
    plan = lo.Loadout(20.0, 2, 100)
    assert lo.compare(plan, state(fuel_gal=20.4, payload_lb=480 + 190)).matches
    s = lo.compare(plan, state(fuel_gal=5.0, payload_lb=100))
    assert not s.fuel_ok and not s.payload_ok and not s.matches
    assert lo.compare(plan, None) is None


# ---------------------------------------------------------------------------- providers
def test_simulated_provider_loads_the_aircraft():
    sim = SimulatedProvider()
    sim._state.title = "Cessna 172 Skyhawk (Simulated)"
    res = sim.apply_loadout(lo.Loadout(18.0, 2, 50), C172).result(1)
    assert sim._state.fuel_gal == 18.0 and res.payload_lb == 2 * lo.PAX_LB + 50
    sim._state.on_ground = False
    with pytest.raises(lo.LoadoutError):
        sim.apply_loadout(lo.Loadout(18.0, 2, 50), C172)


def test_simconnect_provider_queues_writes_for_its_own_thread():
    p = SimConnectProvider()
    with pytest.raises(lo.LoadoutError, match="not connected"):
        p.apply_loadout(lo.Loadout(10, 1, 0), C172)
    p.status = "connected"
    p._latest = state(on_ground=False)
    with pytest.raises(lo.LoadoutError, match="parked"):
        p.apply_loadout(lo.Loadout(10, 1, 0), C172)
    p._latest = state(title="PMDG 777-200LR")
    from skydispatch.data.aircraft import get_type
    with pytest.raises(lo.LoadoutError, match="EFB"):
        p.apply_loadout(lo.Loadout(10, 1, 0), get_type("b77l"))
    p._latest = state()
    fut = p.apply_loadout(lo.Loadout(10.0, 1, 0), C172)
    assert not fut.done()                              # nothing is written from the caller's thread
    p._drain_commands(FakeSim({"LEFT_MAIN": 20.0}, 5))
    assert fut.result(1).fuel_gal == 10.0
    fut2 = p.apply_loadout(lo.Loadout(10.0, 1, 0), C172)
    p._drain_commands(None, ConnectionError("lost"))
    with pytest.raises(ConnectionError):
        fut2.result(1)
    diag = p.diagnose_loadout()
    p._drain_commands(FakeSim({"LEFT_MAIN": 20.0}, 5))
    assert any("Payload stations: 5" in line for line in diag.result(1))


# ---------------------------------------------------------------------------- app context
@pytest.fixture
def ctx(qtbot, tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = "http://127.0.0.1:9/v1"
    s.ai.timeout_s = 1
    s.ui.first_run_complete = True
    s.plan.simbrief_user = "tester"
    c = AppContext(s, Database(tmp_path / "c.db"))
    c.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000)
    c.db.add_job(kind="passenger", title="Hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2, cargo_lb=100,
                 client="Acme", briefing="b", payout=900.0, min_category="piston", min_runway_ft=0, min_reputation=0,
                 deadline_minutes=120, status="offered", created_at="2026-01-01T00:00:00+00:00",
                 expires_at="2099-01-01T00:00:00+00:00")
    jid = c.db.jobs("offered")[0].id
    c.career.accept_job(jid, c.db.hangar()[0].id)
    yield c
    c.stop_sim()
    c.voice.shutdown()


def toasts(ctx):
    out = []
    ctx.toast.connect(lambda lvl, msg: out.append((lvl, msg)))
    return out


def test_context_builds_link_and_plan(ctx):
    assert "orig=EGLL" in ctx.simbrief_link() and "static_id=sd" in ctx.simbrief_link()
    plan = ctx.loadout_plan()
    assert plan.fuel_source == "hangar" and plan.pax == 2


def test_import_simbrief_keeps_a_matching_plan(ctx, qtbot, monkeypatch):
    out = toasts(ctx)
    monkeypatch.setattr(sb, "fetch_latest", lambda user, static_id="": sb.parse_ofp(sample_ofp()))
    with qtbot.waitSignal(ctx.plan_changed, timeout=5000):
        ctx.import_simbrief()
    assert ctx.current_ofp() is not None and ctx.db.get_meta("simbrief_ofp")
    assert any("imported" in m for _, m in out)


def test_import_simbrief_rejects_a_plan_for_another_flight(ctx, qtbot, monkeypatch):
    out = toasts(ctx)
    monkeypatch.setattr(sb, "fetch_latest", lambda user, static_id="": sb.parse_ofp(sample_ofp(dest="EGLC")))
    ctx.import_simbrief()
    qtbot.waitUntil(lambda: any("not this flight" in m for _, m in out), timeout=5000)
    assert ctx.current_ofp() is None


def test_import_survives_restart(ctx, tmp_path, monkeypatch):
    ctx.ofp = sb.parse_ofp(sample_ofp())
    ctx.db.set_meta("simbrief_ofp", __import__("json").dumps(ctx.ofp.to_json()))
    again = AppContext(ctx.settings, ctx.db)
    try:
        assert again.ofp == ctx.ofp
    finally:
        again.voice.shutdown()


def test_sync_loadout_into_the_simulated_aircraft(ctx, qtbot):
    out = toasts(ctx)
    ctx.provider = SimulatedProvider()
    ctx.provider._state.title = "Cessna 172 Skyhawk (Simulated)"
    ctx.sim_connected = True
    with qtbot.waitSignal(ctx.plan_changed, timeout=5000):
        ctx.sync_loadout()
    assert any("Aircraft loaded" in m for _, m in out)
    assert ctx.provider._state.fuel_gal == pytest.approx(ctx.loadout_plan().fuel_gal)


def test_sync_refuses_the_wrong_aircraft(ctx):
    out = toasts(ctx)
    ctx.provider = SimulatedProvider()
    ctx.provider._state.title = "Airbus A320neo"
    ctx.sync_loadout()
    assert any("not the contract's aircraft" in m for _, m in out)


def test_auto_sync_waits_for_the_aircraft_to_settle_then_runs_once(ctx, qtbot):
    ctx.provider = SimulatedProvider()
    ctx.sim_connected = True
    calls = []
    ctx.sync_loadout = lambda auto=False: calls.append(auto)
    c172 = dict(title="Cessna 172 Skyhawk")
    ctx._maybe_auto_sync(state(timestamp=100.0, **c172))
    assert calls == []                                      # MSFS may still be setting the flight up
    ctx._maybe_auto_sync(state(timestamp=102.0, **c172))
    assert calls == []
    ctx._maybe_auto_sync(state(timestamp=103.5, engine_running=True, **c172))     # engines running is fine
    assert calls == [True]
    ctx._maybe_auto_sync(state(timestamp=110.0, **c172))
    assert calls == [True]                                  # once per job
    ctx.settings.plan.auto_sync_loadout = False
    ctx._synced_job = None
    ctx._maybe_auto_sync(state(timestamp=120.0, **c172))
    assert calls == [True]


def test_auto_sync_resets_when_the_aircraft_moves(ctx):
    ctx.provider = SimulatedProvider()
    ctx.sim_connected = True
    calls = []
    ctx.sync_loadout = lambda auto=False: calls.append(auto)
    ctx._maybe_auto_sync(state(timestamp=100.0))
    ctx._maybe_auto_sync(state(timestamp=102.0, gs=15.0, title="Cessna 172 Skyhawk"))   # taxiing: the clock restarts
    ctx._maybe_auto_sync(state(timestamp=104.0))
    assert calls == []
    ctx._maybe_auto_sync(state(timestamp=108.0))
    assert calls == [True]


def test_auto_sync_says_why_it_is_waiting_once(ctx):
    out = toasts(ctx)
    ctx.provider = SimulatedProvider()
    ctx.sim_connected = True
    for t in (100.0, 101.0, 102.0):
        ctx._maybe_auto_sync(state(timestamp=t, title="Airbus A320neo"))
    waiting = [m for lvl, m in out if m.startswith("Not loading the aircraft yet")]
    assert len(waiting) == 1 and "not the contract's aircraft" in waiting[0]
    ctx._maybe_auto_sync(state(timestamp=103.0, title="PMDG 777F"))
    assert any("EFB" in m for _, m in out if m.startswith("Not loading"))


def test_a_partly_accepted_load_is_a_warning_not_a_success(ctx, qtbot):
    out = toasts(ctx)
    ctx.provider = SimulatedProvider()
    ctx.provider._state.title = "Cessna 172 Skyhawk (Simulated)"
    ctx.sim_connected = True
    partial = lo.LoadoutResult(fuel_gal=10.0, payload_lb=0.0, fuel_ok=True, payload_ok=False,
                               messages=["MSFS did not accept the payload change."])
    from concurrent.futures import Future
    fut = Future()
    fut.set_result(partial)
    ctx.provider.apply_loadout = lambda plan, atype: fut
    with qtbot.waitSignal(ctx.plan_changed, timeout=5000):
        ctx.sync_loadout()
    level, msg = out[-1]
    assert level == "warn" and "only partly accepted" in msg and "payload" in msg


def test_loading_is_refused_once_the_flight_has_started(ctx):
    out = toasts(ctx)
    ctx.provider = SimulatedProvider()
    ctx.career.recorder.started = True
    ctx.sync_loadout()
    assert any("already started" in m for _, m in out)


def test_verification_reloads_if_the_sim_undid_the_load(ctx, monkeypatch):
    ctx.provider = SimulatedProvider()
    ctx.provider._state.title = "Cessna 172 Skyhawk (Simulated)"
    ctx.provider._state.fuel_gal = 3.0                                   # MSFS reset the tanks
    ctx.provider._latest = ctx.provider._state
    calls = []
    monkeypatch.setattr(ctx, "sync_loadout", lambda auto=False: calls.append(auto))
    job_id = ctx.career.active_job.id
    ctx._verify_loadout(job_id)
    ctx._verify_loadout(job_id)
    ctx._verify_loadout(job_id)
    assert calls == [True, True]                                          # twice at most, then stop


def test_diagnostics_report_reaches_the_ui(ctx, qtbot):
    ctx.provider = SimulatedProvider()
    with qtbot.waitSignal(ctx.loadout_report, timeout=5000) as sig:
        ctx.diagnose_loadout()
    assert "Simulated aircraft" in sig.args[0]
    ctx.provider = None
    with qtbot.waitSignal(ctx.loadout_report, timeout=2000) as sig:
        ctx.diagnose_loadout()
    assert "not connected" in sig.args[0]


def test_refuel_to_plan_tops_up_the_hangar_aircraft(ctx, qtbot):
    plane = ctx.db.hangar()[0]
    ctx.db.update_aircraft(plane.id, fuel_gal=10.0)
    ctx.ofp = sb.parse_ofp(sample_ofp(fuel=300))           # 50 gal
    ctx.refuel_to_plan()
    assert ctx.db.aircraft(plane.id).fuel_gal == pytest.approx(50.0)
    out = toasts(ctx)
    ctx.refuel_to_plan()
    assert any("already has" in m for _, m in out)


# ---------------------------------------------------------------------------- airlines and flight numbers
def test_every_company_has_a_unique_icao_code_and_callsign():
    from skydispatch.data.employers import EMPLOYERS
    codes = [e.icao for e in EMPLOYERS]
    assert len(set(codes)) == len(codes) and all(len(c) == 3 and c.isalpha() and c.isupper() for c in codes)
    assert all(e.callsign for e in EMPLOYERS)


def test_flight_numbers_are_stable_per_route_and_alternate_by_direction():
    from skydispatch.data.employers import flight_label, flight_number
    out, back = flight_number("EGLL", "EGHI"), flight_number("EGHI", "EGLL")
    assert out == flight_number("EGLL", "EGHI") and abs(out - back) == 1 and 100 <= min(out, back) <= 999 and max(out, back) <= 999
    assert flight_number("egll", "eghi") == out
    assert flight_label(job(employer_id="bluebird")) == "BBA" + str(out)
    assert flight_label(job()) == ""                                   # freelance contracts have no airline


def test_simbrief_link_uses_the_airline_and_flight_number():
    url = sb.dispatch_url(job(employer_id="bluebird"), C172, "G-ABCD", "SKY1", "sd7", airline="bba", flight_number=214)
    assert "airline=BBA" in url and "fltnum=214" in url and "SKY1" not in url
    free = sb.dispatch_url(job(), C172, "G-ABCD", "SKY 1", "sd7")
    assert "airline" not in free and "fltnum=SKY1" in free


def test_context_link_for_a_company_flight(ctx):
    from skydispatch.data.employers import flight_number
    j = ctx.db.jobs("offered")[0] if ctx.db.jobs("offered") else None
    active = ctx.career.active_job
    ctx.db.x("UPDATE jobs SET employer_id = 'bluebird', provided_type = 'c172', aircraft_id = NULL WHERE id = ?", (active.id,))
    ctx.career._job = ctx.db.job(active.id)
    url = ctx.simbrief_link()
    assert "airline=BBA" in url and f"fltnum={flight_number('EGLL', 'EGHI')}" in url
    assert ctx.plan_summary()["flight"].startswith("BBA")
