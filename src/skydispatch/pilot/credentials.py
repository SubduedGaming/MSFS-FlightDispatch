"""Licence, medical and ratings: what a pilot is allowed to fly, and what keeping and extending that costs.

* The **licence** (valid a year) and **medical** (valid 90 days) must be current to be rostered or to take contracts.
  Renewing either costs money. Both lapse in real time.
* **Ratings** never expire. They are earned by paying for a **course** (ground school takes real days) and are what
  companies and aircraft require: instrument (``ir``), multi-engine (``me``), turboprop (``tp``), jet (``jet``) and
  airline (``airliner``).

All dates are real calendar time, matching how recent experience already decays.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..core.config import Settings
from ..data.aircraft import AIRLINER, JET, PISTON, TURBOPROP, TWIN, AircraftType, get_type
from ..data.employers import Employer
from ..db.database import Database

log = logging.getLogger(__name__)

LICENCE_DAYS = 365
MEDICAL_DAYS = 90
RENEW_WINDOW_DAYS = 30          # a certificate can be renewed this long before it lapses
WARN_DAYS = 14                  # warn the pilot this long before it lapses
LICENCE_FEE = 700.0
MEDICAL_FEE = 350.0

RATING_LABEL = {"ir": "Instrument rating", "me": "Multi-engine rating", "tp": "Turboprop type rating",
                "jet": "Jet type rating", "airliner": "Airline type rating"}
_RATING_FOR_CATEGORY = {PISTON: None, TWIN: "me", TURBOPROP: "tp", JET: "jet", AIRLINER: "airliner"}
_BY_ORDER = ("ir", "me", "tp", "jet", "airliner")


class CredentialError(Exception):
    """A rule violation that should be shown to the user."""


@dataclass(frozen=True)
class Course:
    id: str                    # the rating it grants
    name: str
    blurb: str
    fee: float
    days: float                # real days of ground school
    min_total_h: float
    requires: tuple[str, ...] = ()


COURSES: tuple[Course, ...] = (
    Course("ir", "Instrument Rating", "Fly in cloud and low visibility. Companies that run to a schedule expect it.",
           9000, 3, 40),
    Course("me", "Multi-Engine Rating", "Twin-engine handling and engine-out procedures.", 6500, 2, 60),
    Course("tp", "Turboprop Type Rating", "Turbine engines, propeller systems and higher-performance flying.",
           13000, 3, 150, ("ir",)),
    Course("jet", "Jet Type Rating", "High-speed, high-altitude jet operations.", 24000, 4, 400, ("ir",)),
    Course("airliner", "Airline Type Rating", "Large transport aircraft, crew procedures and long-haul operations.",
           38000, 5, 900, ("ir", "jet")),
)
_COURSE = {c.id: c for c in COURSES}


def get_course(course_id: str) -> Course | None:
    return _COURSE.get(course_id)


def rating_for_type(t: AircraftType | None) -> str | None:
    """The rating needed to fly this aircraft, or None for light singles."""
    return _RATING_FOR_CATEGORY.get(t.category) if t else None


def required_ratings(employer: Employer) -> list[str]:
    """What a company wants: a rating for each kind of aircraft it flies, plus an instrument rating from tier 3 up."""
    need = {r for t in employer.fleet_types() if (r := rating_for_type(t))}
    if employer.tier >= 3:
        need.add("ir")
    return [r for r in _BY_ORDER if r in need]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Credentials:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings

    # ------------------------------------------------------------ scaling
    def _scale(self) -> tuple[float, float]:
        """(fee scale, time scale) for the difficulty setting."""
        return {"relaxed": (0.6, 0.34), "realistic": (1.25, 1.5)}.get(self.settings.game.difficulty, (1.0, 1.0))

    def course_fee(self, course: Course) -> float:
        return round(course.fee * self._scale()[0], -1)

    def course_days(self, course: Course) -> float:
        return round(course.days * self._scale()[1], 2)

    def renewal_fee(self, kind: str) -> float:
        return round({"licence": LICENCE_FEE, "medical": MEDICAL_FEE}[kind] * self._scale()[0], -1)

    # ------------------------------------------------------------ certificates
    def _row(self, kind: str):
        return self.db.q1("SELECT * FROM certificates WHERE kind = ?", (kind,))

    def _put(self, kind: str, expires: datetime | None, now: datetime | None = None) -> None:
        self.db.x("INSERT INTO certificates (kind, obtained_at, expires_at) VALUES (?,?,?) "
                  "ON CONFLICT(kind) DO UPDATE SET expires_at = excluded.expires_at",
                  (kind, _iso(now or _now()), _iso(expires) if expires else None))

    def ratings(self) -> set[str]:
        return {r["kind"] for r in self.db.q("SELECT kind FROM certificates") if r["kind"] in RATING_LABEL}

    def has(self, rating: str) -> bool:
        return rating in self.ratings()

    def grant(self, rating: str, now: datetime | None = None) -> None:
        if rating in RATING_LABEL and not self._row(rating):
            self._put(rating, None, now)

    def expires(self, kind: str) -> datetime | None:
        row = self._row(kind)
        return _parse(row["expires_at"]) if row and row["expires_at"] else None

    def valid(self, kind: str, now: datetime | None = None) -> bool:
        exp = self.expires(kind)
        return exp is not None and exp > (now or _now())

    def grounded_reason(self, now: datetime | None = None) -> str | None:
        """Why the pilot cannot be rostered or take a contract right now, or None."""
        now = now or _now()
        if not self.db.pilot():
            return None
        for kind, label in (("medical", "medical certificate"), ("licence", "licence")):
            if not self.valid(kind, now):
                exp = self.expires(kind)
                when = f" on {exp:%d %b %Y}" if exp else ""
                return f"Your {label} expired{when}. Renew it in Training before you can fly contracts."
        return None

    def status(self, now: datetime | None = None) -> list[dict]:
        """The licence and medical as rows for the UI."""
        now = now or _now()
        out = []
        for kind, label in (("licence", "Pilot licence"), ("medical", "Medical certificate")):
            exp = self.expires(kind)
            left = (exp - now).days if exp else None
            out.append({"kind": kind, "label": label, "expires": exp, "days_left": left, "valid": bool(exp and exp > now),
                        "can_renew": exp is None or left is None or left <= RENEW_WINDOW_DAYS,
                        "fee": self.renewal_fee(kind)})
        return out

    def renew(self, kind: str, now: datetime | None = None) -> float:
        """Pay for and extend the licence or medical. Returns the fee."""
        now = now or _now()
        if kind not in ("licence", "medical"):
            raise CredentialError("Only the licence and medical can be renewed")
        info = next(s for s in self.status(now) if s["kind"] == kind)
        if not info["can_renew"]:
            raise CredentialError(f"Your {info['label'].lower()} is valid for {info['days_left']} more days. "
                                  f"You can renew it in the last {RENEW_WINDOW_DAYS} days.")
        fee = self.renewal_fee(kind)
        self._pay(fee, "medical" if kind == "medical" else "licence", f"{info['label']} renewal")
        base = info["expires"] if info["expires"] and info["expires"] > now else now     # renewing early keeps the time left
        self._put(kind, base + timedelta(days=MEDICAL_DAYS if kind == "medical" else LICENCE_DAYS), now)
        self.db.set_meta(f"alert_{kind}", "")
        return fee

    def _pay(self, amount: float, category: str, description: str) -> None:
        pilot = self.db.pilot()
        if not pilot or pilot.balance < amount:
            raise CredentialError(f"You need {self.settings.ui.currency}{amount:,.0f} for this "
                                  f"(you have {self.settings.ui.currency}{(pilot.balance if pilot else 0):,.0f}).")
        self.db.add_transaction(-amount, category, description)

    # ------------------------------------------------------------ defaults and old careers
    def ensure_defaults(self, now: datetime | None = None) -> None:
        """New and migrated careers start with a current licence and medical, and keep the ratings they already
        use: anything they have flown, and what their current employers require."""
        now = now or _now()
        if not self.db.pilot():
            return
        if not self._row("licence"):
            self._put("licence", now + timedelta(days=LICENCE_DAYS), now)
        if not self._row("medical"):
            self._put("medical", now + timedelta(days=MEDICAL_DAYS), now)
        if self.db.get_meta("ratings_granted", "") == "1":
            return
        from ..pilot import quals
        q = quals.compute(self.db)
        for cat, hours in q.by_category_h.items():
            if hours > 0 and (r := _RATING_FOR_CATEGORY.get(cat)):
                self.grant(r, now)
        if any(h > 0 for c, h in q.by_category_h.items() if c != PISTON) or q.total_h >= 50:
            self.grant("ir", now)
        from ..data.employers import get_employer
        for emp in self.db.employments():
            e = get_employer(emp["employer_id"])
            for r in (required_ratings(e) if e else []):
                self.grant(r, now)
        self.db.set_meta("ratings_granted", "1")

    def grant_for_aircraft(self, type_id: str) -> None:
        """The aircraft a new pilot starts with comes with the rating to fly it."""
        r = rating_for_type(get_type(type_id))
        if r:
            self.grant(r)

    # ------------------------------------------------------------ training
    def active_course(self):
        row = self.db.q1("SELECT * FROM training WHERE status = 'active' ORDER BY id DESC LIMIT 1")
        return (get_course(row["course_id"]), _parse(row["completes_at"])) if row and get_course(row["course_id"]) else None

    def course_problem(self, course: Course) -> str | None:
        """Why this course cannot be started now, or None."""
        pilot = self.db.pilot()
        if self.has(course.id):
            return "You already hold this rating."
        if self.active_course():
            return "You are already in a course. Finish it first."
        hours = (pilot.total_minutes / 60.0) if pilot else 0.0
        if hours < course.min_total_h:
            return f"Needs {course.min_total_h:g} flight hours (you have {hours:.0f})."
        missing = [RATING_LABEL[r] for r in course.requires if not self.has(r)]
        if missing:
            return "Needs the " + " and ".join(m.lower() for m in missing) + " first."
        return None

    def start_course(self, course_id: str, now: datetime | None = None) -> Course:
        now = now or _now()
        course = get_course(course_id)
        if course is None:
            raise CredentialError("Unknown course")
        problem = self.course_problem(course)
        if problem:
            raise CredentialError(problem)
        self._pay(self.course_fee(course), "training", f"Training: {course.name}")
        self.db.x("INSERT INTO training (course_id, started_at, completes_at) VALUES (?,?,?)",
                  (course.id, _iso(now), _iso(now + timedelta(days=self.course_days(course)))))
        return course

    def process(self, now: datetime | None = None) -> list[Course]:
        """Finish courses whose time is up and grant their rating. Returns the courses completed."""
        now = now or _now()
        done: list[Course] = []
        for row in self.db.q("SELECT * FROM training WHERE status = 'active'"):
            if _parse(row["completes_at"]) <= now and (course := get_course(row["course_id"])):
                self.grant(course.id, now)
                self.db.x("UPDATE training SET status = 'done' WHERE id = ?", (row["id"],))
                done.append(course)
        return done

    # ------------------------------------------------------------ warnings
    def expiring(self, now: datetime | None = None) -> list[dict]:
        """Certificates that lapse within WARN_DAYS (or already have) and have not been warned about yet."""
        now = now or _now()
        out = []
        for s in self.status(now):
            if s["days_left"] is not None and s["days_left"] <= WARN_DAYS:
                state = "expired" if not s["valid"] else "soon"
                if self.db.get_meta(f"alert_{s['kind']}") != state:
                    out.append({**s, "state": state})
        return out

    def mark_warned(self, kind: str, state: str) -> None:
        self.db.set_meta(f"alert_{kind}", state)
