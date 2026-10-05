"""Running costs of being a pilot: the money has to go somewhere.

Every 30 real days the pilot pays *living costs* (rent, insurance, food), which grow with rank, plus *hangar and
insurance* for each aircraft they own. Flying for a company means the company pays for fuel and the aircraft's running
costs, but never for the pilot's own life, licences, training or aircraft.

If the pilot is away for a long time at most two months of bills are charged when they return, and the rest are
forgiven, so a holiday never bankrupts a career.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .core.config import Settings
from .data.aircraft import get_type
from .db.database import Database

log = logging.getLogger(__name__)

BILL_DAYS = 30
MAX_CATCH_UP = 2
LIVING_COSTS = {"Student Pilot": 900, "Private Pilot": 1200, "Commercial Pilot": 1700, "First Officer": 2300,
                "Captain": 3100, "Chief Pilot": 4200}
AIRCRAFT_MONTHLY_RATE = 0.006          # hangar space and insurance, as a share of the aircraft's price per month


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Economy:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings

    def scale(self) -> float:
        return {"relaxed": 0.5, "realistic": 1.4}.get(self.settings.game.difficulty, 1.0)

    def monthly_items(self) -> list[tuple[str, float, str]]:
        """(description, amount, transaction category) for one month's bills."""
        pilot = self.db.pilot()
        if not pilot:
            return []
        k = self.scale()
        items = [(f"Living costs ({pilot.rank})", round(LIVING_COSTS.get(pilot.rank, 1200) * k, -1), "living")]
        for a in self.db.hangar():
            t = get_type(a.type_id)
            if t:
                fee = round(a.purchase_price * AIRCRAFT_MONTHLY_RATE * k, -1) or round(t.price * AIRCRAFT_MONTHLY_RATE * k, -1)
                items.append((f"Hangar and insurance, {a.registration}", fee, "hangar"))
        return items

    def monthly_total(self) -> float:
        return sum(a for _, a, _ in self.monthly_items())

    def schedule(self, now: datetime | None = None) -> None:
        """Start the clock for a new career: the first bills are due in a month."""
        self.db.set_meta("bills_due_at", _iso((now or _now()) + timedelta(days=BILL_DAYS)))

    def next_due(self) -> datetime | None:
        raw = self.db.get_meta("bills_due_at", "")
        return _parse(raw) if raw else None

    def process(self, now: datetime | None = None) -> list[tuple[str, float]]:
        """Charge any bills that are due. Returns what was charged (empty when nothing was due)."""
        now = now or _now()
        if not self.db.pilot():
            return []
        due = self.next_due()
        if due is None:
            self.schedule(now)
            return []
        if due > now:
            return []
        cycles = min(MAX_CATCH_UP, int((now - due).days // BILL_DAYS) + 1)
        charged: list[tuple[str, float]] = []
        for _ in range(cycles):
            for desc, amount, category in self.monthly_items():
                if amount > 0:
                    self.db.add_transaction(-amount, category, desc)
                    charged.append((desc, amount))
        nxt = due + timedelta(days=BILL_DAYS * cycles)
        while nxt <= now:                                  # months we are not charging for are forgiven
            nxt += timedelta(days=BILL_DAYS)
        self.db.set_meta("bills_due_at", _iso(nxt))
        return charged
