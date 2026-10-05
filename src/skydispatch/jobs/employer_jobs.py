"""Flights an employer's dispatcher creates for you, sized to the time you have free."""
from __future__ import annotations

import logging
import random
import re

from ..core import geo
from ..core.config import Settings
from ..data.aircraft import AircraftType
from ..data.employers import Employer
from ..db.database import Database, iso_in
from ..db.models import Airport, Job
from ..sim.installed import installed_types
from .generator import JobGenerator, _pick_load
from .pricing import difficulty_multiplier, job_payout, deadline_minutes, estimate_block_minutes

log = logging.getLogger(__name__)

GROUND_ALLOWANCE_MIN = 14.0     # matches pricing.estimate_block_minutes


def distance_for_block(block_min: float, cruise_kts: float) -> float:
    """Inverse of estimate_block_minutes."""
    return max(0.0, (block_min - GROUND_ALLOWANCE_MIN) / 60.0 * max(60.0, cruise_kts * 0.9))


BASE_HOURLY_PAY = 160.0        # what a tier-1 bush operator pays per block hour, before its own pay factor


def pilot_pay(employer: Employer, block_min: float, difficulty_mult: float = 1.0) -> float:
    """What the company pays the pilot for a flight: an hourly rate that rises with the company's tier, not the
    passengers' or cargo's revenue (which would make an airline flight pay tens of thousands)."""
    rate = BASE_HOURLY_PAY * (1 + (employer.tier - 1) * 0.45) * employer.pay_factor * difficulty_mult
    return rate * block_min / 60.0


class NoFlightsAvailable(Exception):
    """Explains (in plain English) why the dispatcher cannot offer anything."""


def usable_fleet(employer: Employer, settings: Settings, db: Database) -> list[AircraftType]:
    installed = installed_types(settings, db)
    return [t for t in employer.fleet_types() if installed is None or t.id in installed]


def parse_duration(text: str) -> int | None:
    """'2 hours', '90 mins', 'an hour and a half', '1h30', 'half an hour', '2.5 hrs' -> minutes."""
    t = text.lower().strip()
    words = {"a couple": 2, "couple": 2, "an": 1, "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8}
    t = re.sub(r"\b(one|two|three|four|five|six|seven|eight)\b", lambda m: str(words[m.group(1)]), t)
    if re.search(r"half an? hour|half hour|30 ?min", t) and not re.search(r"\d\s*(h|hour|hr)", t):
        return 30
    if re.search(r"hour and a half|hours and a half", t):
        m = re.search(r"(\d+(?:\.\d+)?)\s*hours? and a half", t)
        return int(float(m.group(1)) * 60 + 30) if m else 90
    m = re.search(r"(\d+)\s*(?:h|hr|hrs|hour|hours)\s*(?:and\s*)?(\d+)\s*(?:m|min|mins|minutes)?\b", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*h\s*(\d+)\b", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    if m:
        return int(round(float(m.group(1)) * 60))
    m = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)\b", t)
    if m:
        return int(m.group(1))
    if re.search(r"\ban hour\b|\bone hour\b", t) or re.fullmatch(r"\s*(an )?hour\s*", t):
        return 60
    m = re.fullmatch(r"\s*(\d{1,3})\s*", t)          # a bare number: treat small ones as hours, big as minutes
    if m:
        n = int(m.group(1))
        return n * 60 if n <= 8 else n
    return None


class EmployerDispatch:
    def __init__(self, db: Database, settings: Settings, rng: random.Random | None = None):
        self.db, self.settings = db, settings
        self.rng = rng or random.Random()
        self.gen = JobGenerator(db, settings, self.rng)

    def pilot_origin(self) -> Airport:
        pilot = self.db.pilot()
        ap = self.db.airport(pilot.location_icao or pilot.home_icao) if pilot else None
        return ap or self.db.airport("EGLL") or self.db.airports()[0]

    def offer_flights(self, employer: Employer, minutes: int, count: int = 3) -> list[Job]:
        fleet = usable_fleet(employer, self.settings, self.db)
        if not fleet:
            names = ", ".join(t.name for t in employer.fleet_types())
            raise NoFlightsAvailable(f"{employer.name} flies {names}, and none of those are installed in your sim.")
        minutes = int(max(15, min(minutes, 24 * 60)))
        origin = self.pilot_origin()
        airports = [a for a in self.db.airports() if a.icao != origin.icao]
        diff = difficulty_multiplier(self.settings.game.difficulty)
        made: list[Job] = []
        used_dest: set[str] = set()
        for _ in range(count):
            job = self._one(employer, fleet, origin, airports, minutes, used_dest, diff)
            if job:
                made.append(job)
                used_dest.add(job.dest)
        if not made:
            raise NoFlightsAvailable(
                f"I can't find a sensible flight for {minutes} minutes from {origin.icao} in {fleet[0].name}. "
                "Give me a bit longer or move to a bigger airport.")
        return made

    def _one(self, employer, fleet, origin, airports, minutes, used_dest, diff) -> Job | None:
        rng = self.rng
        for lo_frac in (0.6, 0.4, 0.2):          # widen the window if nothing fits
            plane = rng.choice(fleet)
            hi_block, lo_block = minutes * 0.95, max(20.0, minutes * lo_frac)
            dmax = min(distance_for_block(hi_block, plane.cruise_kts), plane.range_nm * 0.85,
                       self.settings.game.max_job_distance_nm)
            dmin = max(12.0, distance_for_block(lo_block, plane.cruise_kts))
            if dmax < dmin:
                continue
            cands = []
            for a in airports:
                if a.icao in used_dest or (a.runway_ft and a.runway_ft < plane.min_runway_ft):
                    continue
                d = geo.distance_nm(origin.lat, origin.lon, a.lat, a.lon)
                if dmin <= d <= dmax:
                    cands.append((a, d))
            if not cands:
                continue
            regional = [(a, d) for a, d in cands if not employer.countries or a.country in employer.countries]
            pool = regional or cands
            weights = [self.gen._airport_weight(a.size, plane.category) for a, _ in pool]
            dest, dist = rng.choices(pool, weights=weights, k=1)[0]
            kinds = [k for k in employer.kinds if plane.pax > 0 or k in ("cargo", "mail")] or ["cargo"]
            kind = rng.choice(kinds)
            pax, cargo = _pick_load(rng, kind, plane)
            block = estimate_block_minutes(dist, plane.cruise_kts)
            payout = pilot_pay(employer, block, diff)
            jid = self.db.add_job(
                kind=kind, title=self.gen._title(kind, origin, dest, pax, cargo), origin=origin.icao, dest=dest.icao,
                distance_nm=round(dist, 1), pax=pax, cargo_lb=cargo, client=employer.name,
                briefing=self.gen._briefing(kind, employer.name, origin, dest, pax, cargo), payout=round(payout, -1),
                min_category=plane.category, min_reputation=0,
                deadline_minutes=max(int(block * 1.5) + 10, deadline_minutes(dist, plane.cruise_kts, kind)),
                expires_at=iso_in(240), employer_id=employer.id, provided_type=plane.id)
            return self.db.job(jid)
        return None
