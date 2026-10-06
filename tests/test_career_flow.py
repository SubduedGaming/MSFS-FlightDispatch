from conftest import run_flight

from skydispatch.sim.simulated import SimulatedProvider


def _setup_plane(career, job, provider):
    a = career.db.aircraft(job.aircraft_id)
    o = career.db.airport(job.origin)
    provider.set_position(o.lat, o.lon, "Cessna 172 Skyhawk G1000", fuel_gal=a.fuel_gal, elev_ft=o.elevation_ft)
    provider.cruise_alt = 4500


def _pick_job(career):
    """A short, flyable-for-the-starter job (created explicitly so tests are deterministic)."""
    jid = career.db.add_job(kind="passenger", title="Test hop", origin="EGLL", dest="EGHI", distance_nm=60.0,
                            pax=2, payout=900.0, expires_at="2999-01-01T00:00:00+00:00", deadline_minutes=240)
    return career.db.job(jid)


def test_full_job_cycle(career):
    pilot0 = career.db.pilot()
    job = _pick_job(career)
    plane = career.db.hangar()[0]
    accepted = career.accept_job(job.id, plane.id)
    assert accepted.status == "accepted"

    p = SimulatedProvider(speed=10)
    _setup_plane(career, accepted, p)
    events = []
    career.subscribe(lambda n, d: events.append(n))
    result = run_flight(career, p, accepted.dest)

    assert result is not None, "flight never settled"
    assert result.metrics.outcome == "completed"
    assert result.arrival == accepted.dest
    assert result.payout > 0
    assert 70 <= result.score.score <= 100
    assert "flight_finished" in events
    pilot = career.db.pilot()
    assert pilot.balance > pilot0.balance - result.costs
    assert pilot.total_minutes > 0
    plane2 = career.db.aircraft(plane.id)
    assert plane2.location_icao == accepted.dest
    assert plane2.hours_total > 0
    flight = career.db.flights()[0]
    assert flight.outcome == "completed" and flight.job_id == accepted.id
    assert len(career.db.telemetry(flight.id)) > 5
    kinds = {e["kind"] for e in career.db.events(flight.id)}
    assert {"start", "takeoff", "landing", "arrived"} <= kinds
    assert career.db.job(accepted.id).status == "completed"
    assert career.db.aircraft_flown()[0]["sim_title"].startswith("Cessna 172")


def test_hard_landing_damages_aircraft_and_scores_lower(career):
    job = _pick_job(career)
    plane = career.db.hangar()[0]
    job = career.accept_job(job.id, plane.id)
    p = SimulatedProvider(speed=10)
    _setup_plane(career, job, p)
    p.landing_fpm = -750.0
    result = run_flight(career, p, job.dest)
    assert result.metrics.landing_fpm == -750.0
    assert result.score.score < 70
    assert career.db.aircraft(plane.id).condition < 90 - 5  # started at 92%


def test_free_flight_is_recorded_without_job(career):
    p = SimulatedProvider(speed=10)
    o = career.db.airport("EGLL")
    p.set_position(o.lat, o.lon, "Cessna 172 Skyhawk", fuel_gal=30, elev_ft=o.elevation_ft)
    p.cruise_alt = 3000
    result = run_flight(career, p, "EGHI")
    assert result is not None
    assert result.job is None and result.payout == 0
    f = career.db.flights()[0]
    assert f.job_id is None and f.dep == "EGLL" and f.arr == "EGHI"


def test_diverted_flight_fails_job(career):
    job = _pick_job(career)
    plane = career.db.hangar()[0]
    job = career.accept_job(job.id, plane.id)
    p = SimulatedProvider(speed=10)
    _setup_plane(career, job, p)
    other = "EGJJ" if job.dest != "EGJJ" else "EGTE"
    result = run_flight(career, p, other)
    assert result.metrics.outcome == "diverted"
    assert result.payout == 0
    assert career.db.job(job.id).status == "failed"


def test_cannot_accept_two_jobs_or_wrong_location(career):
    jobs = career.db.jobs("offered")
    plane = career.db.hangar()[0]
    ok = [j for j, in [(j,) for j in jobs]
          if all(e.ok for a, e in career.eligible_aircraft(j))]
    assert ok, "market should offer at least one flyable job"
    career.accept_job(ok[0].id, plane.id)
    import pytest
    from skydispatch.career import CareerError
    with pytest.raises(CareerError):
        career.accept_job(ok[1].id if len(ok) > 1 else ok[0].id, plane.id)


def test_restart_closes_interrupted_flight_and_rearms_job(career):
    job = _pick_job(career)
    plane = career.db.hangar()[0]
    career.accept_job(job.id, plane.id)
    fid = career.db.create_flight(job.id, plane.id, "Cessna", "EGLL")
    career.db.set_job(job.id, status="active")
    career._disarm()                               # simulate quitting the app mid-flight
    career.resume()
    assert career.db.flight(fid).outcome == "aborted"
    assert career.db.job(job.id).status == "accepted"
    assert career.recorder is not None and career.active_job.id == job.id


def test_end_flight_button_settles_and_pays(career):
    import dataclasses
    job = _pick_job(career)
    career.accept_job(job.id, career.db.hangar()[0].id)
    dest = career.db.airport(job.dest)
    o = career.db.airport(job.origin)
    from skydispatch.sim.base import SimState
    t = [0.0]

    def feed(**kw):
        t[0] += 1.0
        base = dict(timestamp=t[0], lat=o.lat, lon=o.lon, engine_running=True, parking_brake=False, gs=10, ias=10,
                    fuel_gal=40, title="Cessna 172 Skyhawk", on_ground=True)
        base.update(kw)
        career.feed(SimState(**base))

    feed()
    assert not career.can_end_flight() or career.recorder.landings == 0
    feed(gs=60, ias=60)
    feed(on_ground=False, ias=90, gs=90, vs=600, alt_msl=300, alt_agl=300)
    assert not career.can_end_flight()
    import pytest as _p
    from skydispatch.career import CareerError
    with _p.raises(CareerError):
        career.end_flight()
    for _ in range(40):
        feed(on_ground=False, ias=100, gs=100, vs=0, alt_msl=3000, alt_agl=3000, lat=dest.lat, lon=dest.lon)
    feed(on_ground=True, ias=60, gs=60, vs=-120, lat=dest.lat, lon=dest.lon)
    feed(gs=0, ias=0, lat=dest.lat, lon=dest.lon)
    assert career.can_end_flight()
    career.end_flight()
    assert career.last_settlement and career.last_settlement.metrics.outcome == "completed"
    assert career.db.job(job.id).status == "completed" and not career.can_end_flight()
