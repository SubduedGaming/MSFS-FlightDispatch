"""Companies do not hire around the clock: they open a vacancy now and then, for a few days, and someone else may get it.

``refresh`` is called regularly (on start-up and every few minutes) and rolls the dice once per elapsed real day, so
the pace is the same whether you play every day or come back after a week. Smaller operators recruit more often than
airlines. A rejected application locks you out of that company for two weeks.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from ..core.config import Settings
from ..data.employers import EMPLOYERS, get_employer
from ..db.database import Database

log = logging.getLogger(__name__)

# chance per day that a company with no vacancy opens one, by tier (1 = bush operator ... 9 = long-haul airline)
OPEN_CHANCE = {1: 0.30, 2: 0.22, 3: 0.16, 4: 0.12, 5: 0.10, 6: 0.08, 7: 0.07, 8: 0.06, 9: 0.05}
FILL_CHANCE = 0.08                 # chance per day that another candidate takes an open vacancy
MIN_OPEN_DAYS, MAX_OPEN_DAYS = 3, 8
COOLDOWN_DAYS = 14
MAX_CATCH_UP_DAYS = 10
SEED = (("bluebird", 10), ("harbour", 8))     # open for a new pilot straight away so the career can begin


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Hiring:
    def __init__(self, db: Database, settings: Settings, rng: random.Random | None = None):
        self.db, self.settings = db, settings
        self.rng = rng or random.Random()

    # ------------------------------------------------------------ queries
    def open_vacancy(self, employer_id: str, now: datetime | None = None):
        now = now or _now()
        row = self.db.q1("SELECT * FROM vacancies WHERE employer_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                         (employer_id,))
        return row if row and _parse(row["closes_at"]) > now else None

    def cooldown_until(self, employer_id: str, now: datetime | None = None) -> datetime | None:
        raw = self.db.get_meta(f"hire_cooldown:{employer_id}", "")
        until = _parse(raw) if raw else None
        return until if until and until > (now or _now()) else None

    def state(self, employer_id: str, now: datetime | None = None) -> dict:
        """What the job board shows: {'state': open|cooldown|closed, 'until': datetime|None}."""
        now = now or _now()
        v = self.open_vacancy(employer_id, now)
        cd = self.cooldown_until(employer_id, now)
        if cd:
            return {"state": "cooldown", "until": cd}
        if v:
            return {"state": "open", "until": _parse(v["closes_at"])}
        return {"state": "closed", "until": None}

    # ------------------------------------------------------------ changes
    def open(self, employer_id: str, days: float, now: datetime | None = None) -> None:
        now = now or _now()
        self.db.x("INSERT INTO vacancies (employer_id, opened_at, closes_at, slots, status) VALUES (?,?,?,1,'open')",
                  (employer_id, _iso(now), _iso(now + timedelta(days=days))))

    def fill(self, employer_id: str) -> None:
        self.db.x("UPDATE vacancies SET status = 'filled' WHERE employer_id = ? AND status = 'open'", (employer_id,))

    def reject(self, employer_id: str, now: datetime | None = None) -> datetime:
        until = (now or _now()) + timedelta(days=COOLDOWN_DAYS)
        self.db.set_meta(f"hire_cooldown:{employer_id}", _iso(until))
        return until

    def seed(self, now: datetime | None = None) -> None:
        """A brand-new career: the entry-level companies are recruiting."""
        now = now or _now()
        for eid, days in SEED:
            self.open(eid, days, now)
            self.db.set_meta(f"hire_roll:{eid}", _iso(now))

    def refresh(self, now: datetime | None = None) -> list[str]:
        """Close old vacancies, let others fill some, and open new ones. Returns the companies that just opened one."""
        now = now or _now()
        if not self.db.pilot():
            return []
        self.db.x("UPDATE vacancies SET status = 'closed' WHERE status = 'open' AND closes_at <= ?", (_iso(now),))
        opened: list[str] = []
        for e in EMPLOYERS:
            last_raw = self.db.get_meta(f"hire_roll:{e.id}", "")
            last = _parse(last_raw) if last_raw else now - timedelta(days=1)
            days = min(MAX_CATCH_UP_DAYS, max(0, int((now - last).total_seconds() // 86400)))
            if days == 0:
                continue
            for _ in range(days):
                if self.open_vacancy(e.id, now):
                    if self.rng.random() < FILL_CHANCE:
                        self.fill(e.id)                     # somebody else got the job
                elif self.rng.random() < OPEN_CHANCE.get(e.tier, 0.05):
                    self.open(e.id, self.rng.randint(MIN_OPEN_DAYS, MAX_OPEN_DAYS), now)
                    opened.append(e.id)
            self.db.set_meta(f"hire_roll:{e.id}", _iso(now))
        return opened

    def describe(self, employer_id: str, now: datetime | None = None) -> str:
        """A line for the job board."""
        e = get_employer(employer_id)
        s = self.state(employer_id, now)
        if s["state"] == "open":
            return f"Recruiting now, applications close {s['until']:%d %b}"
        if s["state"] == "cooldown":
            return f"You can apply again after {s['until']:%d %b}"
        return f"Not recruiting right now{'' if e is None else ': check back soon'}"
