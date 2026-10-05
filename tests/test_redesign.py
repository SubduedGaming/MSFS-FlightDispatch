import random
from datetime import datetime, timedelta, timezone

import pytest

from skydispatch import economy
from skydispatch.career import CareerError
from skydispatch.data.employers import EMPLOYERS, get_employer
from skydispatch.jobs import hiring as hiring_mod
from skydispatch.pilot import credentials as cred
from skydispatch.pilot import quals

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def days(n):
    return NOW + timedelta(days=n)


# ---------------------------------------------------------------------------- hiring: random vacancies
def test_a_new_career_can_start_because_entry_level_companies_are_recruiting(career):
    assert career.hiring.state("bluebird")["state"] == "open"
    assert career.hiring.state("harbour")["state"] == "open"
    assert career.hiring.state("atlas")["state"] == "closed"


def test_cannot_apply_when_a_company_is_not_recruiting(career):
    with pytest.raises(CareerError, match="not recruiting"):
        career.apply_to_employer("atlas")
    assert career.db.applications() == []


def test_vacancies_expire(career):
    career.hiring.open("skyline", 4, NOW)
    assert career.hiring.state("skyline", days(3))["state"] == "open"
    assert career.hiring.state("skyline", days(5))["state"] == "closed"
    career.hiring.refresh(days(5))
    assert career.db.q1("SELECT status FROM vacancies WHERE employer_id = 'skyline'")["status"] == "closed"


def test_refresh_rolls_once_per_elapsed_day_and_is_idempotent(career):
    h = hiring_mod.Hiring(career.db, career.settings, random.Random(3))
    first = h.refresh(NOW + timedelta(days=1, hours=1))
    assert h.refresh(NOW + timedelta(days=1, hours=2)) == []                # same day: no more rolls
    far = h.refresh(NOW + timedelta(days=200))
    n = career.db.q1("SELECT COUNT(*) c FROM vacancies")["c"]
    assert n < 2 * len(EMPLOYERS) + 12                                      # a long absence is capped, not 200 rolls
    assert isinstance(first, list) and isinstance(far, list)


def test_small_operators_recruit_more_often_than_airlines():
    assert hiring_mod.OPEN_CHANCE[1] > hiring_mod.OPEN_CHANCE[9]
    opened = {"bluebird": 0, "atlas": 0}
    for seed in range(40):
        from skydispatch.db.database import Database
        from skydispatch.core.config import Settings
        from skydispatch.career import Career
        db = Database()
        c = Career(db, Settings())
        c.start_career("T", "T1", "EGLL", "c172", 1000)
        db.x("DELETE FROM vacancies")
        db.x("DELETE FROM meta WHERE key LIKE 'hire_roll:%'")
        h = hiring_mod.Hiring(db, Settings(), random.Random(seed))
        h.refresh(NOW)
        h.refresh(NOW + timedelta(days=9))
        for eid in opened:
            opened[eid] += db.q1("SELECT COUNT(*) c FROM vacancies WHERE employer_id = ?", (eid,))["c"]
        db.close()
    assert opened["bluebird"] > opened["atlas"]


def test_getting_hired_fills_the_vacancy_and_rejection_locks_you_out(career):
    assert career.apply_to_employer("bluebird").accepted
    assert career.hiring.state("bluebird")["state"] == "closed"            # the slot is taken
    career.hiring.open("skyline", 6)
    r = career.apply_to_employer("skyline")
    assert not r.accepted and "again after" in r.message
    assert career.hiring.state("skyline")["state"] == "cooldown"
    career.hiring.open("skyline", 6)
    with pytest.raises(CareerError, match="turned you down"):
        career.apply_to_employer("skyline")


def test_describe_for_the_job_board(career):
    assert "Recruiting now" in career.hiring.describe("bluebird")
    assert "Not recruiting" in career.hiring.describe("atlas")


# ---------------------------------------------------------------------------- licence and medical
def test_new_pilots_start_with_a_current_licence_and_medical(career):
    c = career.credentials
    assert c.valid("licence") and c.valid("medical") and c.grounded_reason() is None
    assert 60 < (c.expires("medical") - datetime.now(timezone.utc)).days <= 90


def test_an_expired_medical_grounds_you_until_renewed(career):
    c = career.credentials
    assert "medical" in c.grounded_reason(days(120)).lower()
    job_id = career.db.add_job(kind="passenger", title="x", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=1, cargo_lb=0,
                               client="c", briefing="b", payout=500.0, min_category="piston", min_runway_ft=0,
                               min_reputation=0, deadline_minutes=120, status="offered",
                               created_at="2026-01-01T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00")
    career.db.x("UPDATE certificates SET expires_at = ? WHERE kind = 'medical'", ("2020-01-01T00:00:00+00:00",))
    with pytest.raises(CareerError, match="medical"):
        career.accept_job(job_id, career.db.hangar()[0].id)
    before = career.db.pilot().balance
    fee = c.renew("medical")
    assert career.db.pilot().balance == before - fee and c.valid("medical") and c.grounded_reason() is None
    assert career.accept_job(job_id, career.db.hangar()[0].id)


def test_renewing_early_keeps_the_time_you_had_left(career):
    c = career.credentials
    with pytest.raises(cred.CredentialError, match="valid for"):
        c.renew("licence")                                                  # a year left: too early
    career.db.x("UPDATE certificates SET expires_at = ? WHERE kind = 'medical'", ((NOW + timedelta(days=10)).isoformat(),))
    c.renew("medical", NOW)
    assert (c.expires("medical") - NOW).days == 10 + cred.MEDICAL_DAYS


def test_renewal_needs_the_money(career):
    career.db.add_transaction(-career.db.pilot().balance, "t", "broke")
    career.db.x("UPDATE certificates SET expires_at = ? WHERE kind = 'medical'", ("2020-01-01T00:00:00+00:00",))
    with pytest.raises(cred.CredentialError, match="you have"):
        career.credentials.renew("medical")


def test_expiry_warnings_fire_once_per_state(career):
    c = career.credentials
    career.db.x("UPDATE certificates SET expires_at = ? WHERE kind = 'medical'", ((NOW + timedelta(days=5)).isoformat(),))
    w = c.expiring(NOW)
    assert [x["kind"] for x in w] == ["medical"] and w[0]["state"] == "soon"
    c.mark_warned("medical", "soon")
    assert c.expiring(NOW) == []
    assert c.expiring(NOW + timedelta(days=30))[0]["state"] == "expired"    # a new state warns again


# ---------------------------------------------------------------------------- ratings and training
def test_each_aircraft_class_needs_its_rating():
    from skydispatch.data.aircraft import get_type
    assert cred.rating_for_type(get_type("c172")) is None
    assert cred.rating_for_type(get_type("baron")) == "me"
    assert cred.rating_for_type(get_type("c208")) == "tp"
    assert cred.rating_for_type(get_type("cj4")) == "jet"
    assert cred.rating_for_type(get_type("a320")) == "airliner"


def test_companies_require_the_ratings_for_their_fleet():
    assert cred.required_ratings(get_employer("bluebird")) == []
    assert cred.required_ratings(get_employer("skyline")) == ["ir"]
    assert cred.required_ratings(get_employer("coastline")) == ["ir", "me"]
    assert cred.required_ratings(get_employer("summit")) == ["ir", "tp"]
    assert cred.required_ratings(get_employer("atlas")) == ["ir", "airliner"]


def test_a_company_will_not_hire_without_the_ratings(career):
    quals.apply_experience_preset(career.db, "private", days_ago=3)         # ~150 h with some twin time
    career.hiring.open("northwind", 6)
    q = career.qualifications()
    checks = {c.label: c.met for c in quals.check_requirements(get_employer("northwind").reqs, q, get_employer("northwind"))}
    assert checks["Turboprop type rating"] is False
    r = career.apply_to_employer("northwind")
    assert not r.accepted and "Turboprop type rating" in r.message


def test_course_flow_pay_wait_then_rating(career):
    c = career.credentials
    quals.apply_experience_preset(career.db, "student", days_ago=3)         # 40 h
    career.db.add_transaction(20000, "t", "funds")
    assert not c.has("ir")
    before = career.db.pilot().balance
    course = c.start_course("ir", NOW)
    assert career.db.pilot().balance == before - c.course_fee(course)
    assert c.active_course()[0].id == "ir"
    with pytest.raises(cred.CredentialError, match="already in a course"):
        c.start_course("me", NOW)
    assert c.process(NOW + timedelta(days=1)) == [] and not c.has("ir")
    done = c.process(NOW + timedelta(days=4))
    assert [d.id for d in done] == ["ir"] and c.has("ir") and c.active_course() is None


def test_course_prerequisites_hours_and_funds(career):
    c = career.credentials
    with pytest.raises(cred.CredentialError, match="flight hours"):
        c.start_course("ir")                                                 # a brand-new pilot: 0 h
    quals.apply_experience_preset(career.db, "private", days_ago=3)
    with pytest.raises(cred.CredentialError, match="already hold"):
        c.start_course("ir")                                                 # private preset already has it
    career.db.add_transaction(-career.db.pilot().balance, "t", "broke")
    with pytest.raises(cred.CredentialError, match="you have"):
        c.start_course("tp")
    career.db.add_transaction(100000, "t", "funds")
    with pytest.raises(cred.CredentialError, match="flight hours"):
        c.start_course("jet")                                                # 400 h needed
    quals.apply_experience_preset(career.db, "commercial", days_ago=3)         # 800 h, already rated for turboprops
    career.db.add_transaction(100000, "t", "funds")
    assert c.start_course("jet").id == "jet"


def test_difficulty_scales_fees_and_time(career):
    c = career.credentials
    career.settings.game.difficulty = "relaxed"
    relaxed = (c.course_fee(cred.get_course("jet")), c.course_days(cred.get_course("jet")))
    career.settings.game.difficulty = "realistic"
    hard = (c.course_fee(cred.get_course("jet")), c.course_days(cred.get_course("jet")))
    assert relaxed[0] < 24000 < hard[0] and relaxed[1] < 4 < hard[1]


def test_presets_and_the_starter_aircraft_bring_the_right_ratings(career):
    quals.apply_experience_preset(career.db, "atp", days_ago=3)
    assert career.credentials.ratings() == {"ir", "me", "tp", "jet", "airliner"}
    from skydispatch.career import Career
    from skydispatch.core.config import Settings
    from skydispatch.db.database import Database
    c2 = Career(Database(), Settings())
    c2.start_career("T", "T1", "EGLL", "c172", 1000)
    assert c2.credentials.ratings() == set()                                 # a light single needs none


def test_existing_careers_keep_what_they_already_do(career):
    career.db.x("DELETE FROM certificates")
    career.db.set_meta("ratings_granted", "")
    quals.apply_experience_preset(career.db, "student", days_ago=3)
    career.db.x("DELETE FROM certificates")
    career.db.set_meta("ratings_granted", "")
    career.db.x("INSERT INTO employment (employer_id, hired_at, status) VALUES ('coastline', ?, 'active')", (NOW.isoformat(),))
    career.credentials.ensure_defaults()
    assert {"me", "ir"} <= career.credentials.ratings()                      # grandfathered from a current employer
    assert career.credentials.valid("licence") and career.credentials.valid("medical")


def test_freelance_jobs_need_the_rating_for_the_aircraft(career):
    career.db.add_transaction(2_000_000, "t", "funds")
    career.hangar.buy("baron", "EGLL")
    job_id = career.db.add_job(kind="passenger", title="x", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2, cargo_lb=0,
                               client="c", briefing="b", payout=500.0, min_category="piston", min_runway_ft=0,
                               min_reputation=0, deadline_minutes=120, status="offered",
                               created_at="2026-01-01T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00")
    baron = next(a for a in career.db.hangar() if a.type_id == "baron")
    elig = {a.id: e for a, e in career.eligible_aircraft(career.db.job(job_id))}[baron.id]
    assert not elig.ok and any("multi-engine rating" in r for r in elig.reasons)
    career.credentials.grant("me")
    assert {a.id: e for a, e in career.eligible_aircraft(career.db.job(job_id))}[baron.id].ok


# ---------------------------------------------------------------------------- freelance is for owner-operators
def test_freelance_needs_an_owned_aircraft(career):
    job_id = career.db.add_job(kind="passenger", title="x", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=1, cargo_lb=0,
                               client="c", briefing="b", payout=500.0, min_category="piston", min_runway_ft=0,
                               min_reputation=0, deadline_minutes=120, status="offered",
                               created_at="2026-01-01T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00")
    assert career.is_owner_operator()
    career.hangar.sell(career.db.hangar()[0].id)
    assert not career.is_owner_operator()
    with pytest.raises(CareerError, match="own an aircraft"):
        career.accept_job(job_id, None)


def test_walking_away_from_a_rostered_flight_costs_more_reputation(career):
    career.apply_to_employer("bluebird")
    from skydispatch.jobs.employer_jobs import EmployerDispatch
    job = EmployerDispatch(career.db, career.settings, random.Random(1)).offer_flights(get_employer("bluebird"), 60, 1)[0]
    career.accept_job(job.id)
    rep = career.db.pilot().reputation
    career.abandon_job()
    assert career.db.pilot().reputation == pytest.approx(rep - 3.0)


# ---------------------------------------------------------------------------- the money has somewhere to go
def test_monthly_bills_include_living_costs_and_each_aircraft(career):
    items = career.economy.monthly_items()
    assert items[0][2] == "living" and items[0][1] == economy.LIVING_COSTS["Student Pilot"]
    assert [i[2] for i in items] == ["living", "hangar"] and items[1][1] > 0
    career.economy.settings.game.difficulty = "relaxed"
    assert career.economy.monthly_items()[0][1] < economy.LIVING_COSTS["Student Pilot"]


def test_bills_fall_due_every_thirty_days(career):
    e = career.economy
    e.schedule(NOW)
    assert e.process(days(29)) == []
    before = career.db.pilot().balance
    charged = e.process(days(31))
    assert charged and career.db.pilot().balance == pytest.approx(before - sum(a for _, a in charged))
    assert e.process(days(32)) == []                                         # not charged twice
    assert e.next_due() > days(31)
    cats = {t["category"] for t in career.db.transactions()}
    assert {"living", "hangar"} <= cats


def test_a_long_absence_is_charged_at_most_twice(career):
    e = career.economy
    e.schedule(NOW)
    per_month = e.monthly_total()
    before = career.db.pilot().balance
    e.process(days(400))
    assert career.db.pilot().balance == pytest.approx(before - 2 * per_month)
    assert e.next_due() > days(400)                                          # the other months were forgiven


def test_housekeeping_runs_everything_and_reports_events(career):
    seen = []
    career.subscribe(lambda n, p: seen.append(n))
    career.db.add_transaction(30000, "t", "funds")
    quals.apply_experience_preset(career.db, "student", days_ago=3)
    career.credentials.start_course("ir", NOW)
    career.housekeeping(days(80))
    assert "training_done" in seen and "bills_charged" in seen and "credential_expiring" in seen
    assert career.credentials.has("ir")
