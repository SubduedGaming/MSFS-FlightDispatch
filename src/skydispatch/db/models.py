"""Plain data objects returned by the database layer."""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping


class _Row:
    @classmethod
    def from_row(cls, row: Mapping[str, Any] | None):
        if row is None:
            return None
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: row[k] for k in row.keys() if k in names})


@dataclass
class Pilot(_Row):
    name: str
    callsign: str
    home_icao: str
    balance: float
    reputation: float
    xp: int
    total_minutes: float
    created_at: str
    id: int = 1
    skill: float = 40.0
    location_icao: str = ""

    @property
    def skill_level(self) -> str:
        for threshold, name in SKILL_LEVELS:
            if self.skill >= threshold:
                return name
        return SKILL_LEVELS[-1][1]

    @property
    def rank(self) -> str:
        for threshold, title in RANKS:
            if self.xp >= threshold:
                return title
        return RANKS[-1][1]

    @property
    def next_rank_xp(self) -> int | None:
        higher = [t for t, _ in RANKS if t > self.xp]
        return min(higher) if higher else None


SKILL_LEVELS = [(85, "Expert"), (70, "Skilled"), (55, "Competent"), (40, "Developing"), (0, "Novice")]
RANKS = [(25000, "Chief Pilot"), (10000, "Captain"), (4000, "First Officer"),
         (1500, "Commercial Pilot"), (500, "Private Pilot"), (0, "Student Pilot")]


@dataclass
class Airport(_Row):
    icao: str
    name: str
    city: str
    country: str
    lat: float
    lon: float
    elevation_ft: float
    runway_ft: int
    size: str


@dataclass
class HangarAircraft(_Row):
    id: int
    type_id: str
    registration: str
    nickname: str
    location_icao: str
    hours_total: float
    hours_since_inspection: float
    condition: float
    fuel_gal: float
    purchase_price: float
    purchased_at: str
    sold: int = 0


@dataclass
class Job(_Row):
    id: int
    kind: str
    title: str
    origin: str
    dest: str
    distance_nm: float
    pax: int
    cargo_lb: int
    client: str
    briefing: str
    payout: float
    min_category: str
    min_runway_ft: int
    min_reputation: float
    deadline_minutes: int
    status: str
    aircraft_id: int | None
    created_at: str
    expires_at: str
    accepted_at: str | None = None
    employer_id: str | None = None
    provided_type: str = ""


@dataclass
class Flight(_Row):
    id: int
    job_id: int | None
    aircraft_id: int | None
    sim_title: str
    dep: str
    arr: str
    started_at: str
    ended_at: str | None
    block_min: float
    air_min: float
    distance_nm: float
    fuel_used_gal: float
    landing_fpm: float | None
    max_g: float
    max_ias: float
    max_alt_ft: float
    overspeed_s: float
    score: float
    outcome: str
    payout: float
    costs: float
    summary: str
    debrief: str
    type_id: str = ""
    employer_id: str | None = None
