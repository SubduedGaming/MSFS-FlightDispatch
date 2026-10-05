"""Fact sheets and canned text used for briefings/debriefs.

Facts are computed in code (never by the LLM) so numbers are always right.
The LLM only rephrases them in the dispatcher's voice; if it is offline we
fall back to these templates.
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


def template_briefing(facts: dict, persona_name: str) -> str:
    lines = [
        f"Job {facts['job_id']}: {facts['from']} to {facts['to']}, {facts['distance_nm']} nm, "
        f"track {facts.get('initial_track_deg', '?')} degrees.",
        f"Payload: {facts['payload']} for {facts['client']}. Pays {facts['payout']:,}.",
    ]
    if "est_fuel_gal" in facts:
        lines.append(f"You're in {facts['aircraft']}. Estimated block time {facts['est_block_time']}; plan at "
                     f"least {facts['est_fuel_gal']} gallons, you have {facts['fuel_on_board_gal']}.")
    elif "aircraft" in facts:
        lines.append(f"You're flying {facts['aircraft']}, estimated block time {facts['est_block_time']}, "
                     f"approach speed around {facts.get('approach_speed_kt', '?')} knots.")
    if facts.get("deadline") != "none":
        lines.append(f"The client needs you there within {facts['deadline']}.")
    lines.append("Start engines when you're ready and I'll start the clock. Safe flight. - " + persona_name)
    return " ".join(lines)


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


def template_debrief(facts: dict, persona_name: str) -> str:
    if facts["outcome"] == "crashed":
        return "We lost contact and the aircraft is a write-off. Let's talk safety before you fly again."
    if facts["outcome"] == "diverted":
        return f"You ended up at {facts['arrival'] or 'somewhere unplanned'}. The client's not paying for that."
    if facts["outcome"] == "aborted":
        return "Flight abandoned. We'll log it and move on."
    net = facts["payout"] - facts["operating_costs"]
    parts = [f"Welcome in, Captain. Grade {facts['grade']} ({facts['score']:.0f}/100)."]
    if facts["landing_fpm"] is not None:
        parts.append(f"Touchdown at {facts['landing_fpm']} fpm.")
    if facts["payout"]:
        parts.append(f"Payout {facts['payout']:,} less {facts['operating_costs']:,} running costs, net {net:,}.")
    if facts["penalties"]:
        parts.append("Points lost: " + "; ".join(facts["penalties"]) + ".")
    parts.append(f"- {persona_name}")
    return " ".join(parts)


EVENT_TEMPLATES = {
    "start": "Engines running, I've started the clock. Call when you're rolling.",
    "takeoff": "Wheels up, {detail}. Good luck out there.",
    "landing": "{detail}.",
    "overspeed": "Watch your airspeed, Captain. That's {detail}",
    "low_fuel": "{detail}. Think about your options.",
    "fuel_exhausted": "Fuel is gone. Find somewhere to put it down.",
    "bounce": "That was a bounce. Settle it down.",
    "slew": "I'm seeing a position jump. Anything I should know about?",
    "wrong_aircraft": "{detail}. The contract was written for a different aircraft.",
    "wrong_origin": "{detail}.",
    "arrived": "Parked and secure. Nice work. I'll have your debrief in a moment.",
    "crash": "Mayday received... we've lost you. Report in if you can.",
}
