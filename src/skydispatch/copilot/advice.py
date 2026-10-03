"""Pure, deterministic flight-deck advice: checklists, fuel, descent and approach planning.

These work with no AI at all, so the copilot is useful offline and the language model only has to phrase things.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.aircraft import AircraftType
from ..db.models import Airport

RESERVE_MIN = 45.0       # fuel reserve a sensible pilot keeps (VFR day is 30, IFR 45)


def tod_distance_nm(alt_msl_ft: float, target_msl_ft: float) -> float:
    """3-to-1 rule: three nautical miles per thousand feet to lose."""
    return max(0.0, (alt_msl_ft - target_msl_ft) / 1000.0 * 3.0)


def _miles(n: float) -> str:
    r = round(n)
    return f"{r} mile{'' if r == 1 else 's'}"


def descent_rate_fpm(alt_to_lose_ft: float, distance_nm: float, gs_kts: float) -> float:
    if distance_nm <= 0.5 or gs_kts < 30:
        return 0.0
    minutes = distance_nm / gs_kts * 60.0
    return alt_to_lose_ft / max(0.5, minutes)


@dataclass
class FuelReport:
    endurance_min: float
    needed_min: float
    margin_min: float
    ok: bool
    text: str


def fuel_report(fuel_gal: float, burn_gph: float, remaining_nm: float | None, gs_kts: float,
                reserve_min: float = RESERVE_MIN) -> FuelReport:
    burn = max(0.1, burn_gph)
    endurance = fuel_gal / burn * 60.0
    if remaining_nm is None or gs_kts < 30:
        return FuelReport(endurance, 0.0, endurance - reserve_min, endurance > reserve_min,
                          f"{fuel_gal:.0f} gallons on board, about {endurance / 60:.1f} hours at the current burn.")
    needed = remaining_nm / gs_kts * 60.0
    margin = endurance - needed
    if margin >= reserve_min:
        verdict = f"That leaves about {margin:.0f} minutes of fuel over the reserve."
    elif margin >= reserve_min * 0.5:
        verdict = f"That's below our {reserve_min:.0f}-minute reserve, so consider a closer alternate."
    else:
        verdict = "That is too tight. We need to divert or reduce power right now."
    return FuelReport(endurance, needed, margin, margin >= reserve_min,
                      f"{fuel_gal:.0f} gallons, {endurance:.0f} minutes of endurance, and "
                      f"{needed:.0f} minutes to go. {verdict}")


def approach_brief(dest: Airport | None, atype: AircraftType | None, wind: str = "") -> str:
    if dest is None:
        return "No destination loaded, so I can't brief the approach."
    parts = [f"{dest.icao} {dest.name}: field elevation {dest.elevation_ft:,.0f} feet, longest runway "
             f"{dest.runway_ft:,} feet."]
    if atype:
        pattern = dest.elevation_ft + atype.pattern_agl_ft
        parts.append(f"Approach speed about {atype.vref_kts} knots, circuit or final-approach altitude "
                     f"{pattern:,.0f} feet.")
        if dest.runway_ft and dest.runway_ft < atype.min_runway_ft * 1.3:
            parts.append(f"Runway is short for us: we need about {atype.min_runway_ft:,} feet, so fly a precise, "
                         "stable approach and don't float.")
    if wind:
        parts.append(wind)
    parts.append("Stable by five hundred feet: speed, sink rate and configuration. Go around if we're not.")
    return " ".join(parts)


def descent_plan(alt_msl_ft: float, dest_elev_ft: float, remaining_nm: float | None, gs_kts: float,
                 atype: AircraftType | None = None) -> str:
    target = dest_elev_ft + (atype.pattern_agl_ft if atype else 1500)
    lose = alt_msl_ft - target
    if lose <= 200:
        return "We're already at or near circuit altitude. No descent needed."
    tod = tod_distance_nm(alt_msl_ft, target)
    if remaining_nm is None:
        return f"We need to lose {lose:,.0f} feet: start down about {_miles(tod)} out."
    if remaining_nm <= tod:
        rate = descent_rate_fpm(lose, remaining_nm, gs_kts)
        return (f"We're inside top of descent: {_miles(remaining_nm)} to go with {lose:,.0f} feet to lose, "
                f"so descend now at about {rate:,.0f} feet per minute.")
    rate = descent_rate_fpm(lose, tod, gs_kts)
    return (f"Top of descent in about {_miles(remaining_nm - tod)}. We lose {lose:,.0f} feet over {_miles(tod)}, "
            f"roughly {rate:,.0f} feet per minute at this speed.")


_CHECK = {
    "before_start": {
        "all": ["Parking brake set", "Fuel quantity and selector checked", "Avionics and flight controls checked"],
        "piston": ["Mixture rich, carb heat as required", "Master on, beacon on, prime and start"],
        "turbine": ["Battery on, fuel boost pumps on", "Start the engine(s) per the aircraft procedure"],
    },
    "taxi": {"all": ["Brakes checked", "Flight instruments and heading indicator set", "Flaps and trim set for takeoff",
                     "Lights on, transponder to ON"]},
    "takeoff": {"all": ["Runway and heading confirmed", "Full power, engine instruments green",
                        "Airspeed alive, rotate at the right speed"]},
    "climb": {"all": ["Positive rate: gear up if retractable", "Flaps up, climb power set", "Lights and altimeter checked"]},
    "cruise": {"all": ["Cruise power set and leaned if piston", "Fuel, engine and electrical systems checked",
                       "Navigation and fuel plan compared with the flight plan"]},
    "descent": {"all": ["Landing airport weather and runway reviewed", "Altimeter set, approach briefed",
                        "Fuel and engine settings checked", "Seat belts on"]},
    "landing": {"all": ["Gear down and three green", "Flaps set for landing", "Speed on reference, stabilised by 500 feet",
                        "Landing lights on"]},
    "after_landing": {"all": ["Flaps up", "Lights and transponder off the runway", "Brakes and taxi to parking"]},
    "shutdown": {"all": ["Parking brake set", "Engines shut down per procedure", "Avionics and master off", "Secure the aircraft"]},
}
_PHASE_TO_LIST = {"parked": "before_start", "taxi_out": "taxi", "takeoff": "takeoff", "climb": "climb",
                  "cruise": "cruise", "descent": "descent", "landed": "after_landing", "taxi_in": "after_landing",
                  "arrived": "shutdown"}


def checklist_for_phase(phase: str, atype: AircraftType | None, alt_agl_ft: float = 9999, on_ground: bool = True) -> tuple[str, list[str]]:
    key = _PHASE_TO_LIST.get(phase, "cruise")
    if key == "descent" and not on_ground and alt_agl_ft < 3000:
        key = "landing"
    group = _CHECK[key]
    turbine = bool(atype and atype.category in ("turboprop", "jet", "airliner"))
    items = list(group.get("all", []))
    if key == "before_start":
        items += group["turbine" if turbine else "piston"]
    return key.replace("_", " "), items


def status_text(state, live, atype: AircraftType | None, dest: Airport | None) -> str:
    if state is None:
        return "I'm not getting any data from the aircraft yet."
    bits = [f"{state.alt_msl:,.0f} feet", f"{state.ias:.0f} knots indicated", f"heading {state.heading:03.0f}"]
    if abs(state.vs) > 150:
        bits.append(f"{state.vs:+,.0f} feet per minute")
    text = ", ".join(bits) + "."
    if live and dest and live.remaining_nm is not None:
        text += f" {live.remaining_nm:.0f} miles to {dest.icao}"
        if live.eta_min:
            text += f", about {live.eta_min:.0f} minutes out"
        text += "."
    if atype and state.fuel_gal:
        burn = state.fuel_flow_gph or atype.fuel_gph
        text += " " + fuel_report(state.fuel_gal, burn, live.remaining_nm if live else None, state.gs).text
    return text
