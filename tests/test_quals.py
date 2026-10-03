from datetime import datetime, timedelta, timezone

import pytest

from skydispatch.data.employers import EMPLOYERS, get_employer
from skydispatch.pilot import quals


def add_flight(db, days_ago, hours, type_id="c172", outcome="completed"):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()
    db.x("INSERT INTO flights (started_at, air_min, outcome, type_id) VALUES (?,?,?,?)", (ts, hours * 60, outcome, type_id))
    db.update_pilot(total_minutes=db.pilot().total_minutes + hours * 60)


def test_recency_halves_every_half_life():
    assert quals.recency_weight(0) == 1.0
    assert quals.recency_weight(45) == pytest.approx(0.5)
    assert quals.recency_weight(90) == pytest.approx(0.25)
    assert quals.recency_weight(-3) == 1.0


def test_recent_experience_decays_each_day_without_flying(career):
    add_flight(career.db, 0, 10)
    now = datetime.now(timezone.utc)
    values = [quals.compute(career.db, now + timedelta(days=d)).recent_h for d in (0, 1, 7, 45, 90)]
    assert values[0] == pytest.approx(10, abs=0.05)
    assert all(a > b for a, b in zip(values, values[1:]))            # strictly shrinking every step
    assert values[3] == pytest.approx(5, abs=0.1) and values[4] == pytest.approx(2.5, abs=0.1)
    assert quals.compute(career.db, now + timedelta(days=10)).days_since_last == pytest.approx(10, abs=0.01)


def test_flying_again_restores_recency(career):
    add_flight(career.db, 60, 10)
    stale = quals.compute(career.db).recent_h
    add_flight(career.db, 0, 4)
    assert quals.compute(career.db).recent_h > stale + 3.9


def test_hours_by_type_and_category(career):
    add_flight(career.db, 5, 3, "c172")
    add_flight(career.db, 5, 2, "baron")
    add_flight(career.db, 5, 4, "c208")
    q = quals.compute(career.db)
    assert q.by_type_h["c172"] == pytest.approx(3) and q.by_category_h["twin"] == pytest.approx(2)
    assert q.hours_for("category:turboprop") == pytest.approx(4) and q.hours_for("type:c172") == pytest.approx(3)
    assert q.total_h == pytest.approx(9)


def test_requirements_checklist(career):
    e = get_employer("skyline")
    q = quals.compute(career.db)
    checks = quals.check_requirements(e.reqs, q)
    assert [c.label for c in checks][:3] == ["Total flight time", "Recent experience", "Skill level"]
    assert not any(c.met for c in checks)
    assert quals.meets(get_employer("bluebird"), q)                  # entry-level: no requirements


def test_experience_presets_unlock_the_ladder(career):
    quals.apply_experience_preset(career.db, "commercial", days_ago=10)
    q = quals.compute(career.db)
    assert q.total_h == pytest.approx(800) and q.skill == 65
    assert quals.meets(get_employer("skyline"), q) and quals.meets(get_employer("northwind"), q)
    assert not quals.meets(get_employer("meridian"), q)              # airline needs jet time + more hours
    quals.apply_experience_preset(career.db, "atp")
    assert quals.meets(get_employer("meridian"), quals.compute(career.db))


def test_catalog_is_consistent():
    from skydispatch.data.aircraft import get_type
    ids = [e.id for e in EMPLOYERS]
    assert len(ids) == len(set(ids))
    for e in EMPLOYERS:
        assert e.fleet and all(get_type(t) for t in e.fleet)
        assert e.reqs.type_req == "" or e.reqs.type_req.split(":")[0] in ("category", "type")
    assert [e.tier for e in EMPLOYERS] == sorted(e.tier for e in EMPLOYERS)
