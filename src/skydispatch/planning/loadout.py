"""What the aircraft should carry for the active job, and how to put exactly that into the simulator.

SkyDispatch is the source of truth: a hangar aircraft carries the fuel it has in the hangar, a company aircraft carries
the SimBrief block fuel (or an estimate), and the payload is the contract's passengers and cargo. ``apply_loadout``
writes that into MSFS through a tiny get/set interface, so it can be tested without a simulator.

Safety: nothing is written unless the aircraft is on the ground with engines off, values are clamped to what the
aircraft type can carry, the pilot's own station is never touched, and the result is read back and reported.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..data.aircraft import AircraftType, match_title

log = logging.getLogger(__name__)

PAX_LB = 190.0                  # average passenger including carry-on
ESTIMATE_RESERVE_H = 0.75       # when there is no SimBrief plan: trip fuel + taxi + 45 minutes
ESTIMATE_TAXI_H = 0.1
SEAT_MAX_LB = 400.0             # sanity limit for one seat station
FUEL_TANKS = ("LEFT_MAIN", "RIGHT_MAIN", "CENTER", "CENTER2", "CENTER3", "LEFT_AUX", "RIGHT_AUX", "LEFT_TIP",
              "RIGHT_TIP", "EXTERNAL1", "EXTERNAL2")


class LoadoutError(Exception):
    pass


class SimVars(Protocol):
    def get(self, name: str): ...
    def set(self, name: str, value) -> bool: ...


@dataclass
class Loadout:
    fuel_gal: float
    pax: int
    cargo_lb: float
    fuel_source: str = "hangar"          # hangar | simbrief | estimate
    notes: list[str] = field(default_factory=list)

    @property
    def payload_lb(self) -> float:
        return self.pax * PAX_LB + self.cargo_lb


@dataclass
class LoadoutResult:
    fuel_gal: float = 0.0                # what the sim reports afterwards
    payload_lb: float = 0.0              # passenger and cargo weight now on the payload stations (pilot excluded)
    messages: list[str] = field(default_factory=list)


def estimate_fuel(job, atype: AircraftType) -> float:
    hours = job.distance_nm / max(60, atype.cruise_kts) + ESTIMATE_TAXI_H + ESTIMATE_RESERVE_H
    return min(atype.fuel_cap_gal, round(hours * atype.fuel_gph, 1))


def plan_loadout(job, atype: AircraftType, aircraft=None, ofp=None) -> Loadout:
    """The target fuel and payload for `job` flown in `atype`. `aircraft` is the hangar aircraft, if any."""
    notes: list[str] = []
    if aircraft is not None and not job.employer_id:
        fuel, source = min(aircraft.fuel_gal, atype.fuel_cap_gal), "hangar"
    elif ofp is not None and ofp.matches(job) and ofp.block_fuel_lb > 0:
        fuel, source = min(ofp.block_fuel_gal(atype), atype.fuel_cap_gal), "simbrief"
    else:
        fuel, source = estimate_fuel(job, atype), "estimate"
    if ofp is not None and ofp.matches(job) and source == "hangar" and ofp.block_fuel_gal(atype) > fuel + 1:
        notes.append(f"SimBrief wants {ofp.block_fuel_gal(atype):.0f} gal but the aircraft has {fuel:.0f} gal. "
                     "Refuel in the hangar to match the plan.")
    pax, cargo = min(job.pax, atype.pax), min(float(job.cargo_lb), float(atype.cargo_lb))
    return Loadout(round(fuel, 1), pax, cargo, source, notes)


def ready_problem(state, atype: AircraftType | None) -> str | None:
    """Why the sim aircraft must not be loaded right now, or None when it is safe."""
    if state is None:
        return "The simulator is not connected."
    if not state.on_ground or state.gs > 3:
        return "The aircraft must be parked on the ground."
    if state.engine_running:
        return "Shut the engines down before loading the aircraft."
    if atype is not None:
        sim_type = match_title(state.title or "")
        if sim_type is None or sim_type.id != atype.id:
            return (f"The aircraft in the sim ({state.title or 'unknown'}) is not the contract's aircraft "
                    f"({atype.name}), so nothing was changed.")
    return None


def station_weights(count: int, pax: int, cargo_lb: float) -> tuple[dict[int, float], list[str]]:
    """Weights for payload stations 2..count (station 1 is the pilot and is never changed).

    Small aircraft: stations are seats with the baggage compartment last. Airliners (many stations): passengers are
    spread over the cabin stations and cargo over the last two (the holds)."""
    notes: list[str] = []
    if count < 2:
        return {}, ["This aircraft has no payload stations to load."]
    out = {i: 0.0 for i in range(2, count + 1)}
    holds = [count - 1, count] if count >= 12 else [count] if count >= 4 else []
    seats = [i for i in range(2, count + 1) if i not in holds]
    if cargo_lb > 0:
        if holds:
            for h in holds:
                out[h] = round(cargo_lb / len(holds), 1)
        else:
            notes.append("This aircraft has no baggage station, so the cargo is not loaded in the sim.")
    if pax > 0 and seats:
        if count >= 12:
            for s in seats:
                out[s] = round(pax * PAX_LB / len(seats), 1)
        else:
            seated = min(pax, len(seats))
            for s in seats[:seated]:
                out[s] = PAX_LB
            if pax > len(seats):
                notes.append(f"Only {len(seats)} seats are available in the sim for {pax} passengers.")
            if seated < pax and seats:
                extra = (pax - seated) * PAX_LB
                out[seats[-1]] = min(SEAT_MAX_LB, out[seats[-1]] + extra)
    elif pax > 0:
        notes.append("This aircraft has no passenger stations in the sim.")
    for i, w in out.items():
        if i not in holds:
            out[i] = min(w, SEAT_MAX_LB if count < 12 else 5000.0)
    return out, notes


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def apply_loadout(sim: SimVars, plan: Loadout, atype: AircraftType,
                  sleep: Callable[[float], None] = time.sleep) -> LoadoutResult:
    """Write `plan` into the simulator. Raises LoadoutError when nothing could be written."""
    res = LoadoutResult()
    # ---- fuel: fill every tank to the same fraction of its capacity
    caps = {t: _f(sim.get(f"FUEL_TANK_{t}_CAPACITY")) for t in FUEL_TANKS}
    caps = {t: c for t, c in caps.items() if c > 0.01}
    if caps:
        total = sum(caps.values())
        target = max(0.0, min(plan.fuel_gal, total))
        for t, cap in caps.items():
            sim.set(f"FUEL_TANK_{t}_QUANTITY", round(cap * target / total, 2))
        if plan.fuel_gal > total + 0.5:
            res.messages.append(f"The sim aircraft holds only {total:.0f} gal, so fuel was limited to that.")
    else:
        res.messages.append("Could not read the aircraft's fuel tanks, so fuel was not changed.")
    # ---- payload
    count = int(_f(sim.get("PAYLOAD_STATION_COUNT")))
    weights, notes = station_weights(count, plan.pax, plan.cargo_lb)
    res.messages += notes
    for i, w in weights.items():
        sim.set(f"PAYLOAD_STATION_WEIGHT:{i}", w)
    if not caps and not weights:
        raise LoadoutError("The simulator did not accept any changes. Is the aircraft fully loaded in the sim?")
    # ---- read back what the sim actually holds
    sleep(0.4)
    res.fuel_gal = _f(sim.get("FUEL_TOTAL_QUANTITY"))
    res.payload_lb = sum(_f(sim.get(f"PAYLOAD_STATION_WEIGHT:{i}")) for i in weights)
    return res


@dataclass
class LoadoutStatus:
    fuel_ok: bool
    payload_ok: bool
    sim_fuel_gal: float
    plan_fuel_gal: float
    sim_payload_lb: float
    plan_payload_lb: float

    @property
    def matches(self) -> bool:
        return self.fuel_ok and self.payload_ok


def compare(plan: Loadout, state) -> LoadoutStatus | None:
    """Does the live sim aircraft carry what the plan says? None when there is no telemetry."""
    if state is None:
        return None
    fuel_ok = abs(state.fuel_gal - plan.fuel_gal) <= 0.5 + 0.03 * plan.fuel_gal
    # the sim's payload includes the pilot, so allow for crew weight on top of the planned load
    payload_ok = plan.payload_lb - 5 <= state.payload_lb <= plan.payload_lb + 450
    return LoadoutStatus(fuel_ok, payload_ok, state.fuel_gal, plan.fuel_gal, state.payload_lb, plan.payload_lb)
