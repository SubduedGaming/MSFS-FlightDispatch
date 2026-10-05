"""Companies on the job board. Each one lists what it flies, where, what it pays and what it wants from a pilot.

The companies form a career ladder: bush and courier flying leads to air taxi, medevac and regional cargo, then
turboprop charter, jets and finally airline and heavy cargo work. Company aircraft are supplied by the employer,
so you need not own them (but they must be installed in your sim).
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

from .aircraft import get_type


@dataclass(frozen=True)
class Requirements:
    min_total_h: float = 0.0
    min_recent_h: float = 0.0           # recency-weighted hours (decays while you don't fly)
    min_skill: float = 0.0
    type_req: str = ""                  # "category:turboprop" or "type:c208"
    min_type_h: float = 0.0


@dataclass(frozen=True)
class Employer:
    id: str
    name: str
    tagline: str
    blurb: str
    base: str                           # ICAO of the home base
    countries: tuple[str, ...]          # regions it serves (empty = worldwide)
    fleet: tuple[str, ...]              # catalog type ids
    kinds: tuple[str, ...]              # job kinds it offers
    pay_factor: float
    persona: str                        # dispatcher personality id
    reqs: Requirements
    tier: int

    def fleet_types(self):
        return [t for t in (get_type(i) for i in self.fleet) if t]

    @property
    def icao(self) -> str:
        """Three-letter ICAO airline designator (fictional), used for flight numbers and SimBrief."""
        return AIRLINE_CODES.get(self.id, (self.id[:3].upper(), ""))[0]

    @property
    def callsign(self) -> str:
        """The name used on the radio, e.g. 'Bluebird'."""
        return AIRLINE_CODES.get(self.id, ("", self.name.split()[0]))[1]


EMPLOYERS: list[Employer] = [
    Employer("bluebird", "Bluebird Bush Air", "Everyone starts somewhere",
             "Small family-run operator flying walkers, post and parcels between islands and highland strips. "
             "No experience needed: they train you.", "EGPH", ("GB", "IE"), ("c152", "c172"),
             ("passenger", "mail"), 0.85, "fiona", Requirements(), 1),
    Employer("harbour", "Harbour Light Courier", "Parcels before breakfast",
             "Overnight and early-morning parcel runs in single-engine aircraft. Reliable, tidy flying wanted.",
             "EGHI", ("GB", "FR", "NL", "BE", "DE", "IE"), ("c172", "dr40"), ("cargo", "mail"), 0.95, "gordon",
             Requirements(min_total_h=10, min_skill=40), 2),
    Employer("skyline", "Skyline Air Taxi", "On-demand, on time",
             "Four-seat air taxi for business travellers across Europe and North America.", "EGLL", (),
             ("da40", "sr22"), ("passenger", "charter"), 1.05, "priya",
             Requirements(min_total_h=50, min_recent_h=3, min_skill=50, type_req="category:piston", min_type_h=30), 3),
    Employer("alpine", "Alpine Scenic Flights", "Mountains, lakes, happy customers",
             "Sightseeing and shuttle flights around mountain country. Passengers like a smooth ride.", "LSZH",
             ("CH", "AT", "DE", "IT", "FR", "US"), ("c172", "da40", "g36"), ("passenger",), 1.0, "stefan",
             Requirements(min_total_h=100, min_recent_h=4, min_skill=55, type_req="category:piston", min_type_h=60), 3),
    Employer("coastline", "Coastline Air Ambulance", "When minutes matter",
             "Twin-engine air ambulance for hospitals and rescue services. Calm, accurate, quick.", "EGGD",
             ("GB", "IE", "FR", "ES", "PT", "US", "CA"), ("baron", "da62"), ("medevac",), 1.25, "grace",
             Requirements(min_total_h=200, min_recent_h=6, min_skill=60, type_req="category:piston", min_type_h=120), 4),
    Employer("northwind", "Northwind Regional Freight", "Turboprop workhorses",
             "Single-turbine freight across wild country in all weathers.", "ENGM",
             ("NO", "SE", "FI", "DK", "IS", "CA", "US"), ("c208",), ("cargo", "mail"), 1.15, "jack",
             Requirements(min_total_h=300, min_recent_h=8, min_skill=60, type_req="category:twin", min_type_h=25), 5),
    Employer("summit", "Summit Executive Aviation", "Discreet, punctual, premium",
             "Executive turboprop charter for private clients. Flawless landings expected.", "KSDL", (),
             ("pc12", "tbm9", "king"), ("charter", "passenger"), 1.35, "elena",
             Requirements(min_total_h=500, min_recent_h=10, min_skill=65, type_req="category:turboprop", min_type_h=50), 6),
    Employer("apex", "Apex Jet Charter", "Fast, far and first class",
             "Light jet charter and positioning flights across the world.", "EGLF", (), ("cj4",),
             ("charter", "passenger", "medevac"), 1.5, "dante",
             Requirements(min_total_h=900, min_recent_h=12, min_skill=70, type_req="category:turboprop", min_type_h=120), 7),
    Employer("meridian", "Meridian Airways", "Scheduled passenger services",
             "A regional-to-international airline operating narrowbody jets on scheduled routes.", "EHAM", (),
             ("a320", "b738"), ("passenger",), 1.45, "annika",
             Requirements(min_total_h=1500, min_recent_h=15, min_skill=72, type_req="category:jet", min_type_h=100), 8),
    Employer("atlas", "Atlas Global Cargo & Long-Haul", "Across oceans",
             "Widebody freight and passenger flying on the world's longest routes.", "KJFK", (),
             ("b748", "b78x"), ("passenger", "cargo"), 1.7, "ray",
             Requirements(min_total_h=3500, min_recent_h=20, min_skill=78, type_req="category:airliner", min_type_h=400), 9),
]

# (ICAO designator, radio callsign). These are made up for the game; change them here if one clashes with a real airline.
AIRLINE_CODES: dict[str, tuple[str, str]] = {
    "bluebird": ("BBA", "Bluebird"), "harbour": ("HLC", "Harbour"), "skyline": ("SKX", "Skyline"),
    "alpine": ("ASF", "Alpine"), "coastline": ("CAM", "Coastline Medevac"), "northwind": ("NWF", "Northwind"),
    "summit": ("SMX", "Summit"), "apex": ("APJ", "Apex"), "meridian": ("MRD", "Meridian"), "atlas": ("AGC", "Atlas Cargo"),
}

_BY_ID = {e.id: e for e in EMPLOYERS}


def flight_number(origin: str, dest: str) -> int:
    """A stable flight number for a route, the way scheduled airlines do it: the same route always has the same number,
    and the opposite direction gets the next one up (outbound even, inbound odd)."""
    a, b = sorted((origin.upper(), dest.upper()))
    base = 100 + 2 * (zlib.crc32(f"{a}-{b}".encode()) % 440)
    return base if origin.upper() == a else base + 1


def flight_label(job) -> str:
    """'BBA214' for a company flight; empty for a freelance contract (those fly under the pilot's own callsign)."""
    e = _BY_ID.get(getattr(job, "employer_id", None) or "")
    return f"{e.icao}{flight_number(job.origin, job.dest)}" if e else ""


def get_employer(employer_id: str) -> Employer | None:
    return _BY_ID.get(employer_id)
