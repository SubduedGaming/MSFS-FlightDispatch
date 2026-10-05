"""Procedural job generation (the "job market")."""
from __future__ import annotations

import logging
import random

from ..core import geo
from ..core.config import Settings
from ..data.aircraft import AIRLINER, CATALOG, PISTON, TURBOPROP, TWIN, AircraftType, get_type
from ..db.database import Database, iso_in
from ..db.models import Airport
from ..sim.installed import installed_types
from .pricing import (KIND_LABEL, TIER, TIER_NAME, deadline_minutes, difficulty_multiplier, job_payout)

log = logging.getLogger(__name__)

CLIENTS = {
    "passenger": ["Meridian Travel", "Bluebird Tours", "Harbour & Vale Holidays", "Northwind Commuter Club",
                  "Summit Outdoors", "Atlas Business Group", "Kestrel Conferences", "Lakeside Weddings Ltd"],
    "cargo": ["Corvid Logistics", "Orchard Fresh Produce", "Tallis Engineering Supply", "BlueRiver Pharma",
              "Ironbridge Parts Co.", "Coastal Seafood Exchange", "Pinnacle Electronics", "Marlow Farm Co-op"],
    "charter": ["Sterling Executive Travel", "Aurora Film Productions", "Kensington Capital", "Halcyon Entertainment"],
    "medevac": ["County Air Ambulance", "Regional Health Trust", "St. Brendan's Hospital", "Red Cross Air Service"],
    "mail": ["Royal Post Services", "Overland Express Mail", "Postal Union Air Link"],
}
CARGO_ITEMS = ["fresh produce", "machine parts", "medical supplies", "newspapers", "electronics",
               "frozen seafood", "construction tools", "auto parts", "textiles", "scientific equipment"]
MEDEVAC_REASONS = ["a patient needing urgent specialist care", "a donor organ transfer",
                   "an injured hiker", "a premature newborn transfer"]

# Which categories each job kind suits, and how many pax / lb typical loads are.
KIND_PROFILE = {
    "passenger": {"weights": 5.0},
    "cargo": {"weights": 4.0},
    "charter": {"weights": 1.2},
    "medevac": {"weights": 1.0},
    "mail": {"weights": 1.5},
}


def _pick_load(rng: random.Random, kind: str, atype: AircraftType) -> tuple[int, int]:
    if kind in ("passenger", "charter"):
        hi = max(1, atype.pax)
        pax = rng.randint(max(1, hi // 3), hi)
        if kind == "charter":
            pax = min(pax, 8)
        return pax, 0
    if kind == "medevac":
        return rng.randint(1, min(3, max(1, atype.pax))), 0
    cap = atype.cargo_lb
    return 0, int(rng.randint(int(cap * 0.3), int(cap * 0.95)) // 10 * 10)


class JobGenerator:
    def __init__(self, db: Database, settings: Settings, rng: random.Random | None = None):
        self.db = db
        self.settings = settings
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------
    def refresh(self, target: int | None = None) -> int:
        """Expire old offers and top the market up. Returns number created."""
        self.db.expire_jobs()
        target = target or self.settings.game.job_count
        open_jobs = self.db.jobs("offered")
        need = max(0, target - len(open_jobs))
        created = 0
        for _ in range(need * 3):          # a few attempts per slot
            if created >= need:
                break
            if self._generate_one():
                created += 1
        return created

    def _generate_one(self) -> bool:
        pilot = self.db.pilot()
        fleet = self.db.hangar()
        rng = self.rng
        airports = self.db.airports()
        if len(airports) < 2:
            return False
        # Source plane: biased towards owned aircraft so jobs are actually flyable.
        plane: AircraftType | None = None
        origin: Airport | None = None
        # Only aircraft that are installed in the player's sim can be flown, so only they get jobs.
        installed = installed_types(self.settings, self.db)
        if installed is not None:
            fleet = [a for a in fleet if a.type_id in installed]
        if fleet and rng.random() < 0.8:
            owned = rng.choice(fleet)
            plane = get_type(owned.type_id)
            origin = self.db.airport(owned.location_icao)
        if plane is None:
            pool = CATALOG[:11] if pilot is None or pilot.reputation < 70 else CATALOG
            if installed is not None:
                pool = [t for t in CATALOG if t.id in installed]
            if not pool:
                return False
            plane = rng.choice(pool)
        if origin is None:
            home = self.db.airport(pilot.home_icao) if pilot else None
            origin = home or rng.choice(airports)
            if rng.random() < 0.35:
                origin = rng.choice(airports)

        kind = self._pick_kind(plane)
        max_range = min(plane.range_nm / 1.2, self.settings.game.max_job_distance_nm)
        min_dist = 25.0 if plane.category in (PISTON, TWIN) else 80.0 if plane.category == TURBOPROP else 150.0
        candidates = []
        for a in airports:
            if a.icao == origin.icao or a.runway_ft and a.runway_ft < plane.min_runway_ft:
                continue
            d = geo.distance_nm(origin.lat, origin.lon, a.lat, a.lon)
            if min_dist <= d <= max_range:
                candidates.append((a, d))
        if not candidates:
            return False
        # Prefer larger airports for bigger planes, small fields for light aircraft.
        weights = [self._airport_weight(a.size, plane.category) for a, _ in candidates]
        dest, dist = rng.choices(candidates, weights=weights, k=1)[0]
        pax, cargo = _pick_load(rng, kind, plane)
        payout = job_payout(kind, dist, pax, cargo, difficulty_multiplier(self.settings.game.difficulty))
        # Rep requirement scales with job value for variety.
        min_rep = 0.0 if payout < 2000 else 30.0 if payout < 10000 else 55.0 if payout < 50000 else 75.0
        min_cat = _min_category_for(plane, kind)
        client = rng.choice(CLIENTS[kind])
        title = self._title(kind, origin, dest, pax, cargo)
        self.db.add_job(
            kind=kind, title=title, origin=origin.icao, dest=dest.icao, distance_nm=round(dist, 1),
            pax=pax, cargo_lb=cargo, client=client, briefing=self._briefing(kind, client, origin, dest, pax, cargo),
            payout=payout, min_category=min_cat, min_runway_ft=0, min_reputation=min_rep,
            deadline_minutes=deadline_minutes(dist, plane.cruise_kts, kind),
            expires_at=iso_in(rng.randint(90, 600)))
        return True

    # ------------------------------------------------------------------
    def _pick_kind(self, plane: AircraftType) -> str:
        kinds, weights = [], []
        for kind, prof in KIND_PROFILE.items():
            if kind == "charter" and TIER[plane.category] < 1:
                continue
            if kind == "medevac" and plane.category == AIRLINER:
                continue
            if plane.pax == 0 and kind in ("passenger", "charter", "medevac"):
                continue                               # a freighter has no seats
            if kind == "mail" and plane.cargo_lb < 150:
                continue
            if kind == "cargo" and plane.cargo_lb < 100:
                continue
            kinds.append(kind)
            weights.append(prof["weights"])
        return self.rng.choices(kinds, weights=weights, k=1)[0]

    @staticmethod
    def _airport_weight(size: str, category: str) -> float:
        if category in (PISTON, TWIN):
            return {"S": 3.0, "M": 2.0, "L": 0.8}[size]
        if category == TURBOPROP:
            return {"S": 1.5, "M": 2.5, "L": 1.5}[size]
        return {"S": 0.2, "M": 1.5, "L": 3.0}[size]

    def _title(self, kind: str, o: Airport, d: Airport, pax: int, cargo: int) -> str:
        if kind in ("passenger", "charter"):
            what = f"{pax} passenger{'s' if pax != 1 else ''}"
        elif kind == "medevac":
            what = "urgent medical transfer"
        else:
            what = f"{cargo:,} lb cargo"
        return f"{KIND_LABEL[kind]}: {o.city or o.name} to {d.city or d.name} ({what})"

    def _briefing(self, kind: str, client: str, o: Airport, d: Airport, pax: int, cargo: int) -> str:
        rng = self.rng
        if kind == "passenger":
            return rng.choice([
                f"{client} has {pax} guests booked from {o.name} to {d.name}. Keep the ride smooth.",
                f"Group of {pax} travelling for {client}. They'd like to be at {d.name} on schedule.",
            ])
        if kind == "charter":
            return f"{client} wants discretion and a flawless flight: {pax} VIP passenger(s), {o.name} to {d.name}."
        if kind == "medevac":
            return (f"{client} requests {rng.choice(MEDEVAC_REASONS)}. Time matters: {o.name} to {d.name}. "
                    "Fly it by the book, and fly it quickly.")
        item = rng.choice(CARGO_ITEMS)
        if kind == "mail":
            return f"{client}: {cargo:,} lb of sorted mail for {d.name}. Deliver before the morning rush."
        return f"{client} needs {cargo:,} lb of {item} moved from {o.name} to {d.name}."


def _min_category_for(plane: AircraftType, kind: str) -> str:
    tier = TIER[plane.category]
    if kind in ("charter",):
        tier = max(tier, 1)
    return TIER_NAME[max(0, tier - 1 if tier >= 2 else tier)]
