"""Payout and eligibility rules. Pure functions, easy to test and tune."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.aircraft import AIRLINER, JET, PISTON, TURBOPROP, TWIN, AircraftType

TIER = {PISTON: 0, TWIN: 1, TURBOPROP: 2, JET: 3, AIRLINER: 4}
TIER_NAME = {v: k for k, v in TIER.items()}

# kind -> (rate per nm per pax, rate per nm per 100 lb cargo, flat fee, multiplier)
KIND_RATES = {
    "passenger": (0.9, 0.0, 120.0, 1.0),
    "cargo": (0.0, 0.55, 150.0, 1.0),
    "charter": (1.4, 0.0, 400.0, 1.35),
    "medevac": (2.0, 0.0, 600.0, 1.8),
    "mail": (0.0, 0.5, 100.0, 1.1),
}
KIND_LABEL = {"passenger": "Passenger", "cargo": "Cargo", "charter": "VIP Charter",
              "medevac": "Medevac", "mail": "Mail"}


def job_payout(kind: str, distance_nm: float, pax: int, cargo_lb: int, difficulty_mult: float = 1.0) -> float:
    r_pax, r_cargo, flat, mult = KIND_RATES[kind]
    amount = flat + distance_nm * (r_pax * pax + r_cargo * cargo_lb / 100.0)
    return round(amount * mult * difficulty_mult, -1 if amount > 500 else 0)


def difficulty_multiplier(difficulty: str) -> float:
    return {"relaxed": 1.25, "normal": 1.0, "realistic": 0.8}.get(difficulty, 1.0)


def estimate_block_minutes(distance_nm: float, cruise_kts: float) -> float:
    """Taxi/climb/descent allowance on top of cruise time."""
    return distance_nm / max(60.0, cruise_kts * 0.9) * 60.0 + 14.0


def deadline_minutes(distance_nm: float, cruise_kts: float, kind: str) -> int:
    if kind == "medevac":
        slack = 1.25
    elif kind in ("charter", "mail"):
        slack = 1.6
    else:
        slack = 2.2
    return int(estimate_block_minutes(distance_nm, cruise_kts) * slack + 15)


@dataclass
class Eligibility:
    ok: bool
    reasons: list[str]


def check_eligibility(job, aircraft_type: AircraftType, origin_runway_ft: int, dest_runway_ft: int,
                      current_location: str | None, aircraft_location: str, fuel_ok: bool = True) -> Eligibility:
    """Can this aircraft fly this job? Returns reasons it can't."""
    reasons: list[str] = []
    if TIER[aircraft_type.category] < TIER[job.min_category]:
        reasons.append(f"Needs at least a {job.min_category} aircraft")
    if job.pax > aircraft_type.pax:
        reasons.append(f"Only seats {aircraft_type.pax} (needs {job.pax})")
    if job.cargo_lb > aircraft_type.cargo_lb:
        reasons.append(f"Cargo limit {aircraft_type.cargo_lb} lb (needs {job.cargo_lb})")
    if job.distance_nm * 1.15 > aircraft_type.range_nm:
        reasons.append(f"Range {aircraft_type.range_nm} nm is too short")
    if origin_runway_ft and origin_runway_ft < aircraft_type.min_runway_ft:
        reasons.append(f"Departure runway too short ({origin_runway_ft} ft)")
    if dest_runway_ft and dest_runway_ft < aircraft_type.min_runway_ft:
        reasons.append(f"Destination runway too short ({dest_runway_ft} ft)")
    if aircraft_location.upper() != job.origin.upper():
        reasons.append(f"Aircraft is at {aircraft_location}, not {job.origin} (ferry it first)")
    if not fuel_ok:
        reasons.append("Not enough fuel for the trip")
    return Eligibility(not reasons, reasons)
