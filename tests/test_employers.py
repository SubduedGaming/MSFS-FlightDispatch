import random

import pytest
from conftest import run_flight

from skydispatch.career import CareerError
from skydispatch.data.aircraft import get_type
from skydispatch.data.employers import get_employer
from skydispatch.jobs.employer_jobs import EmployerDispatch, NoFlightsAvailable, distance_for_block, parse_duration
from skydispatch.jobs.pricing import estimate_block_minutes
from skydispatch.pilot import quals
from skydispatch.sim.installed import format_ids
from skydispatch.sim.simulated import SimulatedProvider


@pytest.mark.parametrize("text,minutes", [
    ("2 hours", 120), ("an hour", 60), ("1 hour", 60), ("90 minutes", 90), ("45 min", 45), ("half an hour", 30),
    ("an hour and a half", 90), ("2.5 hours", 150), ("1h30", 90), ("one hour", 60), ("two hours", 120),
    ("about 3 hrs", 180), ("2", 120), ("30", 30), ("1 hour 15 minutes", 75), ("I have 20 mins", 20)])
def test_parse_duration(text, minutes):
    assert parse_duration(text) == minutes


def test_parse_duration_rejects_non_durations():
    assert parse_duration("what jobs do you have?") is None
    assert parse_duration("") is None


def test_apply_accepted_and_rejected(career):
    career.hiring.open("skyline", 5)                                   # companies only take applications while recruiting
    r = career.apply_to_employer("bluebird")
    assert r.accepted and career.db.is_employed_by("bluebird")
    with pytest.raises(CareerError):
        career.apply_to_employer("bluebird")                           # already employed
    r2 = career.apply_to_employer("skyline")
    assert not r2.accepted and "Total flight time" in r2.message
    assert not career.db.is_employed_by("skyline")
    assert [a["status"] for a in career.db.applications()] == ["rejected", "accepted"]
    with pytest.raises(CareerError, match="again after"):               # a rejection locks you out for a while
        career.apply_to_employer("skyline")
    career.db.set_meta("hire_cooldown:skyline", "")
    career.hiring.open("skyline", 5)
    quals.apply_experience_preset(career.db, "commercial", days_ago=3)
    assert career.apply_to_employer("skyline").accepted


def test_cannot_apply_when_fleet_not_installed(career):
    career.settings.sim.installed_aircraft = format_ids({"c172"})
    career.hiring.open("skyline", 5)
    assert career.employer_fleet_note(get_employer("skyline"))         # DA40/SR22 missing
    with pytest.raises(CareerError, match="not.*installed|installed"):
        career.apply_to_employer("skyline")
    assert career.apply_to_employer("bluebird").accepted               # C172 is installed


@pytest.mark.parametrize("minutes", [30, 60, 120, 240])
def test_offers_fit_the_time_available(career, minutes):
    career.apply_to_employer("bluebird")
    ed = EmployerDispatch(career.db, career.settings, random.Random(minutes))
    jobs = ed.offer_flights(get_employer("bluebird"), minutes, 3)
    assert 1 <= len(jobs) <= 3
    assert len({j.dest for j in jobs}) == len(jobs)
    for j in jobs:
        t = get_type(j.provided_type)
        assert t.id in ("c152", "c172") and j.employer_id == "bluebird" and j.origin == "EGLL"
        block = estimate_block_minutes(j.distance_nm, t.cruise_kts)
        assert block <= minutes * 1.0 + 1, (minutes, j.distance_nm, block)
        assert j.payout > 0 and j.aircraft_id is None


def test_offers_only_use_installed_aircraft(career):
    career.apply_to_employer("bluebird")
    career.settings.sim.installed_aircraft = format_ids({"c152"})
    ed = EmployerDispatch(career.db, career.settings, random.Random(2))
    assert {j.provided_type for j in ed.offer_flights(get_employer("bluebird"), 90, 3)} == {"c152"}
    career.settings.sim.installed_aircraft = format_ids({"baron"})
    with pytest.raises(NoFlightsAvailable, match="installed"):
        ed.offer_flights(get_employer("bluebird"), 90, 3)


def test_resigning_withdraws_open_offers(career):
    career.apply_to_employer("bluebird")
    job = EmployerDispatch(career.db, career.settings, random.Random(1)).offer_flights(get_employer("bluebird"), 60, 1)[0]
    career.resign("bluebird")
    assert career.db.job(job.id).status == "expired" and not career.db.is_employed_by("bluebird")


def test_distance_inverse():
    for cruise in (95, 200, 450):
        d = distance_for_block(120, cruise)
        assert estimate_block_minutes(d, cruise) == pytest.approx(120, abs=0.01)


def test_accepting_requires_employment_and_installed_aircraft(career):
    career.apply_to_employer("bluebird")
    jobs = EmployerDispatch(career.db, career.settings, random.Random(1)).offer_flights(get_employer("bluebird"), 90, 2)
    career.db.resign("bluebird")                                       # employment ended behind the offer's back
    with pytest.raises(CareerError, match="don't work"):
        career.accept_job(jobs[0].id)
    career.db.hire("bluebird")
    career.settings.sim.installed_aircraft = format_ids({"baron"})
    with pytest.raises(CareerError, match="not installed"):
        career.accept_job(jobs[0].id)
    career.settings.sim.installed_aircraft = ""
    career.accept_job(jobs[0].id)
    assert career.db.job(jobs[1].id).status == "expired"               # sibling offers are withdrawn
    assert career.db.active_job().id == jobs[0].id


def test_company_flight_end_to_end(career):
    career.apply_to_employer("bluebird")
    jobs = EmployerDispatch(career.db, career.settings, random.Random(5)).offer_flights(get_employer("bluebird"), 60, 1)
    job = jobs[0]
    before = career.db.pilot()
    career.accept_job(job.id)
    t = get_type(job.provided_type)
    p = SimulatedProvider(speed=40)
    o = career.db.airport(job.origin)
    p.configure_aircraft(t.name + " (Simulated)", t.cruise_kts * 0.9, 4500, t.fuel_gph, t.fuel_cap_gal)
    p.set_position(o.lat, o.lon, fuel_gal=t.fuel_cap_gal, elev_ft=o.elevation_ft)
    result = run_flight(career, p, job.dest)
    assert result and result.metrics.outcome == "completed" and result.employer_id == "bluebird"
    assert result.costs == 0 and result.payout > 0                   # company pays running costs
    after = career.db.pilot()
    assert after.location_icao == job.dest                           # the pilot is now at the destination
    assert after.skill != before.skill                               # skill moves with flight quality
    assert after.balance == pytest.approx(before.balance + result.payout)
    flight = career.db.flights()[0]
    assert flight.employer_id == "bluebird" and flight.type_id == "c172"
    assert career.db.employment("bluebird")["flights"] == 1
    assert career.db.hangar()[0].hours_total == 0                    # own hangar plane untouched
    q = career.qualifications()
    assert q.by_type_h["c172"] > 0 and q.recent_h > 0


def test_skill_drops_after_a_bad_flight_and_rises_after_a_good_one(career):
    career.apply_to_employer("bluebird")
    skills = []
    for fpm in (-120, -120, -900):
        job = EmployerDispatch(career.db, career.settings, random.Random(9)).offer_flights(get_employer("bluebird"), 45, 1)[0]
        career.accept_job(job.id)
        t = get_type(job.provided_type)
        p = SimulatedProvider(speed=40)
        o = career.db.airport(job.origin)
        p.configure_aircraft(t.name, t.cruise_kts * 0.9, 4000, t.fuel_gph, t.fuel_cap_gal)
        p.set_position(o.lat, o.lon, fuel_gal=t.fuel_cap_gal, elev_ft=o.elevation_ft)
        p.landing_fpm = fpm
        career.last_settlement = None
        run_flight(career, p, job.dest)
        skills.append(career.db.pilot().skill)
    assert skills[0] > 40 and skills[1] > skills[0] and skills[2] < skills[1]


def test_freelance_jobs_and_hangar_respect_installed_aircraft(career):
    career.db.add_transaction(5_000_000, "t", "t")
    career.hangar.buy("baron", "EGLL")
    career.settings.sim.installed_aircraft = format_ids({"c172"})
    career.db.q("SELECT 1")
    from skydispatch.career import Career  # noqa: F401
    for j in career.db.jobs("offered"):
        career.db.set_job(j.id, status="expired")
    career.jobs.rng = random.Random(4)
    career.jobs.refresh(30)
    for j in career.db.jobs("offered"):
        assert j.min_category == "piston" and j.pax <= 3 and j.cargo_lb <= 120, j   # only C172-sized work
    baron = [a for a in career.db.hangar() if a.type_id == "baron"][0]
    job = career.db.jobs("offered")[0]
    state = {a.id: e for a, e in career.eligible_aircraft(job)}
    assert not state[baron.id].ok and any("not installed" in r for r in state[baron.id].reasons)


def test_old_database_upgrades_with_data_intact(tmp_path):
    import sqlite3
    from skydispatch.db.database import Database
    from skydispatch.db.schema import MIGRATIONS
    f = tmp_path / "old.db"
    con = sqlite3.connect(f)
    con.executescript(MIGRATIONS[0])
    con.execute("PRAGMA user_version = 1")
    con.execute("INSERT INTO pilot (id,name,callsign,home_icao,balance,created_at) VALUES (1,'Old','O1','EGLL',500,'2026-01-01')")
    con.execute("INSERT INTO messages (ts, role, content) VALUES ('2026-01-01','user','hi')")
    con.commit()
    con.close()
    db = Database(f)
    p = db.pilot()
    assert p.name == "Old" and p.skill == 40 and p.location_icao == "EGLL"
    assert db.messages(10)[0]["thread"] == "general" and db.messages(10)[0]["kind"] == "text"
    db.close()
