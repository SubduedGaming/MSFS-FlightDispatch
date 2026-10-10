"""A pilot's qualifications: total time, recency-weighted recent experience, skill, and time on aircraft.

Recent experience decays with *real* days: each flight's hours count fully on the day flown and halve every
``half_life_days`` (default 45) after that, so a pilot who stops flying sees "recent experience" shrink every day.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..data.aircraft import get_type
from ..data.employers import Employer, Requirements

DEFAULT_HALF_LIFE_DAYS = 45.0
CATEGORIES = ("piston", "twin", "turboprop", "jet", "airliner")


def recency_weight(age_days: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    return 0.5 ** (max(0.0, age_days) / max(1.0, half_life_days))


@dataclass
class Qualifications:
    total_h: float = 0.0
    recent_h: float = 0.0               # recency-weighted hours
    last_90d_h: float = 0.0             # plain hours flown in the last 90 days (for display)
    skill: float = 40.0
    skill_level: str = "Developing"
    days_since_last: float | None = None
    by_category_h: dict[str, float] = field(default_factory=dict)
    by_type_h: dict[str, float] = field(default_factory=dict)
    ratings: set[str] = field(default_factory=set)      # instrument, multi-engine, turboprop, jet, airline

    def hours_for(self, type_req: str) -> float:
        kind, _, key = type_req.partition(":")
        if kind == "category":
            return self.by_category_h.get(key, 0.0)
        if kind == "type":
            return self.by_type_h.get(key, 0.0)
        return 0.0

    def to_json(self) -> str:
        return json.dumps({"total_h": round(self.total_h, 1), "recent_h": round(self.recent_h, 1),
                           "skill": round(self.skill, 1), "by_category_h": self.by_category_h})


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute(db, now: datetime | None = None, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> Qualifications:
    now = now or datetime.now(timezone.utc)
    pilot = db.pilot()
    q = Qualifications()
    if pilot is None:
        return q
    q.total_h = pilot.total_minutes / 60.0
    q.skill, q.skill_level = pilot.skill, pilot.skill_level
    newest_age: float | None = None
    for row in db.flight_history():
        hours = (row["air_min"] or 0) / 60.0
        age = max(0.0, (now - _parse(row["started_at"])).total_seconds() / 86400.0)
        q.recent_h += hours * recency_weight(age, half_life_days)
        if age <= 90:
            q.last_90d_h += hours
        newest_age = age if newest_age is None else min(newest_age, age)
        t = get_type(row["type_id"]) if row["type_id"] else None
        if t:
            q.by_type_h[t.id] = q.by_type_h.get(t.id, 0.0) + hours
            q.by_category_h[t.category] = q.by_category_h.get(t.category, 0.0) + hours
    q.days_since_last = newest_age
    q.ratings = {r["kind"] for r in db.q("SELECT kind FROM certificates") if r["kind"] not in ("licence", "medical")}
    return q


@dataclass
class Check:
    label: str
    required: str
    actual: str
    met: bool


def type_req_label(type_req: str) -> str:
    kind, _, key = type_req.partition(":")
    if kind == "category":
        return f"{key} aircraft"
    t = get_type(key)
    return t.name if t else key


def check_requirements(reqs: Requirements, q: Qualifications, employer: Employer | None = None) -> list[Check]:
    """What the company checks. Pass the employer to include the ratings its aircraft and routes need."""
    checks: list[Check] = []
    if reqs.min_total_h:
        checks.append(Check("Total flight time", f"{reqs.min_total_h:g} h", f"{q.total_h:.1f} h",
                            q.total_h >= reqs.min_total_h))
    if reqs.min_recent_h:
        checks.append(Check("Recent experience", f"{reqs.min_recent_h:g} h", f"{q.recent_h:.1f} h",
                            q.recent_h >= reqs.min_recent_h))
    if reqs.min_skill:
        checks.append(Check("Skill level", f"{reqs.min_skill:g}", f"{q.skill:.0f} ({q.skill_level})",
                            q.skill >= reqs.min_skill))
    if reqs.type_req and reqs.min_type_h:
        have = q.hours_for(reqs.type_req)
        checks.append(Check(f"Time on {type_req_label(reqs.type_req)}", f"{reqs.min_type_h:g} h", f"{have:.1f} h",
                            have >= reqs.min_type_h))
    if employer is not None:
        from .credentials import RATING_LABEL, required_ratings
        for r in required_ratings(employer):
            held = r in q.ratings
            checks.append(Check(RATING_LABEL[r], "required", "held" if held else "not held (see Training)", held))
    return checks


def meets(employer: Employer, q: Qualifications) -> bool:
    return all(c.met for c in check_requirements(employer.reqs, q, employer))


# ---- starting experience presets (wizard) -------------------------------------------
EXPERIENCE_PRESETS = {
    # id: (label, total hours, skill, {category: hours})
    "new": ("New to flying (0 h)", 0, 35, {}),
    "student": ("Student pilot (~40 h)", 40, 45, {"piston": 40}),
    "private": ("Private pilot (~150 h)", 150, 55, {"piston": 140, "twin": 10}),
    "commercial": ("Commercial pilot (~800 h)", 800, 65, {"piston": 450, "twin": 200, "turboprop": 150}),
    "atp": ("Airline transport pilot (~3,000 h)", 3000, 75,
            {"piston": 900, "twin": 400, "turboprop": 700, "jet": 600, "airliner": 400}),
}
_REPRESENTATIVE = {"piston": "c172", "twin": "baron", "turboprop": "c208", "jet": "cj4", "airliner": "b738"}


def apply_experience_preset(db, preset_id: str, days_ago: float = 14.0, now: datetime | None = None) -> None:
    """Give a new pilot a starting logbook: total time, skill and hours per aircraft class (as 'prior' flights)."""
    label, total_h, skill, cats = EXPERIENCE_PRESETS.get(preset_id, EXPERIENCE_PRESETS["new"])
    db.update_pilot(skill=float(skill), total_minutes=total_h * 60.0)
    started = (now or datetime.now(timezone.utc)).timestamp() - days_ago * 86400
    iso = datetime.fromtimestamp(started, timezone.utc).replace(microsecond=0).isoformat()
    db.set_meta("ratings_granted", "")             # let the new logbook decide which ratings the pilot already holds
    for cat, hours in cats.items():
        fid = db.x("INSERT INTO flights (job_id, aircraft_id, sim_title, dep, arr, started_at, ended_at, air_min, "
                   "block_min, outcome, summary, type_id) VALUES (NULL, NULL, 'Prior experience', '', '', ?, ?, ?, ?, "
                   "'prior', 'Logged before starting this career', ?)",
                   (iso, iso, hours * 60.0, hours * 60.0, _REPRESENTATIVE[cat]))
        assert fid
    from ..core.config import Settings
    from .credentials import Credentials
    Credentials(db, Settings()).ensure_defaults(now)
