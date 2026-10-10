"""Fact sheets used for briefings and debriefs.

Facts are computed in code (never by the LLM) so numbers are always right.
The LLM puts them in the dispatcher's voice; if it cannot, the pilot is told (there is no stock text).
"""
from __future__ import annotations

from ..core.geo import bearing_deg, fmt_duration
from ..data.aircraft import get_type
from ..data.employers import flight_label
from ..db.database import Database
from ..db.models import HangarAircraft, Job
from ..jobs.pricing import estimate_block_minutes


def job_facts(db: Database, job: Job, aircraft: HangarAircraft | None = None) -> dict:
    o, d = db.airport(job.origin), db.airport(job.dest)
    t = get_type(aircraft.type_id) if aircraft else None
    facts = {
        "job_id": job.id, "type": job.kind, "client": job.client,
        "from": f"{o.name} ({o.icao})" if o else job.origin,
        "to": f"{d.name} ({d.icao})" if d else job.dest,
        "distance_nm": round(job.distance_nm),
        "payload": f"{job.pax} passengers" if job.pax else f"{job.cargo_lb} lb cargo",
        "payout": round(job.payout),
        "deadline": fmt_duration(job.deadline_minutes) if job.deadline_minutes else "none",
    }
    if o and d:
        facts["initial_track_deg"] = round(bearing_deg(o.lat, o.lon, d.lat, d.lon))
        facts["dest_elevation_ft"] = round(d.elevation_ft)
        facts["dest_longest_runway_ft"] = d.runway_ft
    if t:
        facts["aircraft"] = f"{t.name} {aircraft.registration}"  # type: ignore[union-attr]
        facts["est_block_time"] = fmt_duration(estimate_block_minutes(job.distance_nm, t.cruise_kts))
        facts["est_fuel_gal"] = round(job.distance_nm / t.cruise_kts * t.fuel_gph * 1.1 + t.fuel_gph * 0.75)
        facts["fuel_on_board_gal"] = round(aircraft.fuel_gal)  # type: ignore[union-attr]
    elif job.provided_type and get_type(job.provided_type):
        t = get_type(job.provided_type)
        facts["aircraft"] = f"{t.name} (company aircraft, already fuelled)"
        facts["est_block_time"] = fmt_duration(estimate_block_minutes(job.distance_nm, t.cruise_kts))
        facts["approach_speed_kt"] = t.vref_kts
    return facts


def offer_summary(db: Database, job: Job) -> str:
    """One readable line describing a flight offer (used on offer cards and for the AI)."""
    t = get_type(job.provided_type) if job.provided_type else None
    load = f"{job.pax} passenger{'s' if job.pax != 1 else ''}" if job.pax else f"{job.cargo_lb:,} lb of cargo"
    block = fmt_duration(estimate_block_minutes(job.distance_nm, t.cruise_kts)) if t else "?"
    plane = f" in the {t.name}" if t else ""
    flight = flight_label(job)
    return (f"{flight + ': ' if flight else ''}{job.origin} to {job.dest}, {job.distance_nm:.0f} nm, about {block} block time. "
            f"{load.capitalize()}. Pays {job.payout:,.0f}{plane}.")


def settlement_facts(s) -> dict:
    m, sc = s.metrics, s.score
    return {
        "outcome": m.outcome, "score": sc.score, "grade": sc.grade,
        "block_time": fmt_duration(m.block_min), "distance_nm": round(m.distance_nm),
        "landing_fpm": None if m.landing_fpm is None else round(abs(m.landing_fpm)),
        "max_g": round(m.max_g, 2), "overspeed_s": round(m.overspeed_s),
        "payout": round(s.payout), "operating_costs": round(s.costs),
        "penalties": sc.penalties, "notes": s.notes, "arrival": s.arrival,
        "job": s.job.title if s.job else "free flight (no contract)",
    }
