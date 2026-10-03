"""Aircraft catalog: the planes you can buy/fly and how they behave in the economy.

``match`` holds lowercase keywords used to recognise the aircraft from the
sim's TITLE variable (works for default MSFS 2020/2024 liveries and most addons).
"""
from __future__ import annotations

from dataclasses import dataclass

# Categories also decide which jobs a plane may take.
PISTON, TWIN, TURBOPROP, JET, AIRLINER = "piston", "twin", "turboprop", "jet", "airliner"


@dataclass(frozen=True)
class AircraftType:
    id: str
    name: str
    maker: str
    category: str
    cruise_kts: int
    vne_kts: int            # never-exceed / max operating speed (overspeed trigger)
    fuel_gph: float         # average burn, gallons per hour
    fuel_cap_gal: float
    pax: int
    cargo_lb: int
    range_nm: int
    price: int
    min_runway_ft: int
    hourly_cost: float      # operating cost per flight hour (fuel excluded)
    maint_hours: int        # inspection interval
    match: tuple[str, ...]
    fuel_lb_per_gal: float = 6.0   # avgas 6.0, jet-A 6.7

    @property
    def vref_kts(self) -> int:
        """Typical landing-reference / final-approach speed."""
        return VREF.get(self.id, int(self.cruise_kts * 0.55))

    @property
    def pattern_agl_ft(self) -> int:
        return {PISTON: 1000, TWIN: 1000, TURBOPROP: 1500, JET: 1500, AIRLINER: 1500}[self.category]

    @property
    def fuel_price_per_gal(self) -> float:
        return 5.2 if self.category in (PISTON, TWIN) else 6.1


CATALOG: list[AircraftType] = [
    AircraftType("c152", "Cessna 152", "Cessna", PISTON, 95, 149, 6.0, 24.5, 1, 60, 415, 38000, 1500, 55, 100,
                 ("cessna 152", "c152")),
    AircraftType("c172", "Cessna 172 Skyhawk", "Cessna", PISTON, 122, 163, 9.0, 53, 3, 120, 640, 95000, 1600, 80, 100,
                 ("cessna 172", "c172", "skyhawk")),
    AircraftType("da40", "Diamond DA40 NG", "Diamond", PISTON, 130, 178, 8.0, 40, 3, 120, 640, 175000, 1700, 95, 100,
                 ("da40", "diamond da40"), 6.7),
    AircraftType("sr22", "Cirrus SR22", "Cirrus", PISTON, 175, 200, 17.0, 92, 3, 150, 1000, 395000, 2000, 150, 100,
                 ("sr22", "cirrus sr22")),
    AircraftType("g36", "Beechcraft Bonanza G36", "Beechcraft", PISTON, 165, 202, 15.0, 74, 4, 180, 800, 600000, 2100, 170, 100,
                 ("bonanza", "g36")),
    AircraftType("dr40", "Robin DR400", "Robin", PISTON, 120, 166, 8.5, 42, 3, 100, 600, 105000, 1500, 75, 100,
                 ("dr400", "robin")),
    AircraftType("baron", "Beechcraft Baron G58", "Beechcraft", TWIN, 195, 223, 28.0, 142, 5, 300, 1480, 1150000, 2400, 330, 100,
                 ("baron", "g58")),
    AircraftType("da62", "Diamond DA62", "Diamond", TWIN, 185, 192, 18.0, 90, 6, 250, 1200, 900000, 2100, 260, 100,
                 ("da62",), 6.7),
    AircraftType("c208", "Cessna 208B Grand Caravan EX", "Cessna", TURBOPROP, 185, 175, 55.0, 335, 9, 3000, 900, 2300000,
                 1900, 520, 200, ("caravan", "c208", "208b"), 6.7),
    AircraftType("pc12", "Pilatus PC-12 NGX", "Pilatus", TURBOPROP, 280, 270, 62.0, 402, 9, 2200, 1800, 5200000,
                 2500, 760, 200, ("pc-12", "pc12"), 6.7),
    AircraftType("tbm9", "Daher TBM 930", "Daher", TURBOPROP, 300, 266, 55.0, 292, 5, 800, 1730, 4900000, 2300, 700, 200,
                 ("tbm 930", "tbm930", "tbm 940", "tbm9"), 6.7),
    AircraftType("king", "Beechcraft King Air 350i", "Beechcraft", TURBOPROP, 312, 263, 100.0, 539, 11, 2500, 1900,
                 8500000, 3300, 1100, 200, ("king air", "b350", "kingair"), 6.7),
    AircraftType("cj4", "Cessna Citation CJ4", "Cessna", JET, 425, 305, 200.0, 1000, 8, 1500, 2000, 9900000, 3300, 1500,
                 300, ("cj4", "citation"), 6.7),
    AircraftType("a320", "Airbus A320neo", "Airbus", AIRLINER, 450, 350, 700.0, 6400, 150, 8000, 3300, 110000000, 6000,
                 4500, 400, ("a320", "a320neo"), 6.7),
    AircraftType("b738", "Boeing 737-800", "Boeing", AIRLINER, 450, 340, 850.0, 6875, 162, 9000, 2900, 106000000, 6500,
                 4800, 400, ("737", "b738", "boeing 737"), 6.7),
    AircraftType("b748", "Boeing 747-8 Intercontinental", "Boeing", AIRLINER, 490, 365, 3300.0, 63000, 410, 60000, 8000,
                 420000000, 9000, 14000, 500, ("747", "b748"), 6.7),
    AircraftType("b78x", "Boeing 787-10 Dreamliner", "Boeing", AIRLINER, 490, 350, 1450.0, 33400, 330, 40000, 7000,
                 338000000, 8000, 9000, 500, ("787", "b78x", "dreamliner"), 6.7),
]

VREF = {"c152": 55, "c172": 65, "da40": 70, "sr22": 80, "g36": 75, "dr40": 62, "baron": 85, "da62": 80,
        "c208": 80, "pc12": 85, "tbm9": 85, "king": 105, "cj4": 105, "a320": 135, "b738": 140, "b748": 155,
        "b78x": 140}

_BY_ID = {a.id: a for a in CATALOG}

STARTER_IDS = ("c152", "c172", "dr40")


def get_type(type_id: str) -> AircraftType | None:
    return _BY_ID.get(type_id)


def match_title(title: str) -> AircraftType | None:
    """Best-effort match of an MSFS aircraft title to a catalog type."""
    t = (title or "").lower()
    if not t:
        return None
    best: tuple[int, AircraftType] | None = None
    for a in CATALOG:
        for kw in a.match:
            if kw in t and (best is None or len(kw) > best[0]):
                best = (len(kw), a)
    return best[1] if best else None
