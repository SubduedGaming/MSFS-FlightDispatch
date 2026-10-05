"""Find out which catalog aircraft are installed in the player's MSFS (2020 or 2024).

MSFS keeps every add-on in a *packages folder* containing ``Community/`` and ``Official/``. Each package may hold
``SimObjects/Airplanes/<aircraft folder>``. We match package and folder names (for example
``asobo-aircraft-c172sp-as1000`` or ``Asobo_B747_8i``) and ``title=`` lines in ``aircraft.cfg`` against the
catalog's keywords. Detection is best effort: it returns ``None`` when no install is found, and the app then
simply stops restricting jobs.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Iterable

from ..core.config import Settings
from ..data.aircraft import CATALOG, match_title

log = logging.getLogger(__name__)

# Extra keywords seen in package/folder names that the title-based catalog keywords do not cover.
_NAME_HINTS = {
    "c152": ("c152",), "c172": ("c172",), "da40": ("da40",), "sr22": ("sr22",), "g36": ("g36", "bonanza"),
    "dr40": ("dr400", "dr40"), "baron": ("baron", "g58"), "da62": ("da62",), "c208": ("c208", "caravan"),
    "pc12": ("pc12", "pc-12"), "tbm9": ("tbm9", "tbm-9", "tbm930", "tbm-930"),
    "king": ("kingair", "king-air", "king_air", "b350"), "cj4": ("cj4", "citation"),
    "a320": ("a320",), "b738": ("b737", "737"), "b748": ("b747", "747"), "b78x": ("b787", "787"),
}


# AI-traffic model libraries (FSLTL, VATSIM/IVAO model matching...) are not aircraft the player can fly, but contain
# thousands of airliner models that would make every catalog airliner look installed.
_TRAFFIC_RX = re.compile(r"traffic|fsltl|ai[-_]?(aircraft|model|traffic)|vpilot|xmatch|world[-_]?of[-_]?ai", re.I)


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _junction_targets(path: Path) -> list[Path]:
    """Windows: the same location addressed through the real target of the nearest junction above it.

    The Store/Xbox install keeps its packages behind a junction in %LOCALAPPDATA%\\Packages (LocalCache points to
    E:\\WpSystem\\...). Some processes (for example elevated ones) get "untrusted mount point" when walking
    through it, but reading where it points needs no walking, and the target folder itself is plain."""
    if sys.platform != "win32":
        return []
    for ancestor in (path, *path.parents):
        try:
            target = os.readlink(ancestor)
        except OSError:
            continue                                  # not a link
        target = target[4:] if target.startswith("\\\\?\\") else target
        return [Path(target) / path.relative_to(ancestor)]
    return []


def _reachable(path: Path, test) -> Path | None:
    """`path` itself if `test` passes, else the same place via its junction target."""
    if test(path):
        return path
    for alt in _junction_targets(path):
        if test(alt):
            return alt
    return None


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def userconfig_candidates() -> list[Path]:
    """Locations of MSFS's UserCfg.opt (Store and Steam editions of both sims)."""
    out: list[Path] = []
    local, roaming = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    if local:
        for pkg in ("Microsoft.FlightSimulator_8wekyb3d8bbwe", "Microsoft.Limitless_8wekyb3d8bbwe"):
            out.append(Path(local) / "Packages" / pkg / "LocalCache" / "UserCfg.opt")
    if roaming:
        for name in ("Microsoft Flight Simulator", "Microsoft Flight Simulator 2024"):
            out.append(Path(roaming) / name / "UserCfg.opt")
    return out


def packages_path_from_usercfg(cfg: Path) -> Path | None:
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r'InstalledPackagesPath\s+"([^"]+)"', text)
    return Path(m.group(1)) if m else None


def _types_from_name(name: str) -> set[str]:
    low = name.lower()
    found = {tid for tid, hints in _NAME_HINTS.items() if any(h in low for h in hints)}
    t = match_title(name)
    if t:
        found.add(t.id)
    return found


def _titles(cfg_file: Path, limit: int = 60_000) -> Iterable[str]:
    try:
        with open(cfg_file, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read(limit)
    except OSError:
        return []
    return re.findall(r"^\s*title\s*=\s*\"?([^\";\r\n]+)", text, flags=re.I | re.M)


def scan_packages(root: Path) -> set[str]:
    """Catalog ids of aircraft found in one packages folder."""
    found: set[str] = set()
    bases = _package_bases(root)
    if not bases:
        bases = [root]                                  # the user pointed straight at a folder of packages
    seen: set[Path] = set()
    for base in bases:
        if not base.is_dir():
            continue
        try:
            packages = [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            continue
        for pkg in packages:
            if pkg in seen or _TRAFFIC_RX.search(pkg.name):
                continue
            seen.add(pkg)
            airplanes = pkg / "SimObjects" / "Airplanes"
            if not airplanes.is_dir():
                continue
            found |= _types_from_name(pkg.name)
            try:
                for plane in airplanes.iterdir():
                    if not plane.is_dir():
                        continue
                    found |= _types_from_name(plane.name)
                    for title in _titles(plane / "aircraft.cfg"):
                        found |= _types_from_name(title)
            except OSError:
                continue
    return found


_CONTAINERS = {"community", "official", "onestore", "steam"}


def _is_container(name: str) -> bool:
    """Community / Official, including the MSFS 2024 spellings (Community2024, Official2020, Official2024)."""
    n = name.lower()
    return n.startswith(("community", "official"))


def _package_bases(root: Path) -> list[Path]:
    """Folders directly holding add-on packages: every Community*/Official* folder, plus their OneStore/Steam parts."""
    bases: list[Path] = []
    try:
        children = sorted(c for c in root.iterdir() if c.is_dir() and _is_container(c.name))
    except OSError:
        return []
    for c in children:
        if c.name.lower().startswith("official"):
            for sub in ("OneStore", "Steam"):
                if (c / sub).is_dir():
                    bases.append(c / sub)
        bases.append(c)
    return bases


def default_roots() -> list[Path]:
    """Well-known packages folders of MSFS 2020 and 2024 (Store and Steam), whether or not UserCfg.opt names them."""
    out: list[Path] = []
    local, roaming = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    if local:
        for pkg in ("Microsoft.FlightSimulator_8wekyb3d8bbwe", "Microsoft.Limitless_8wekyb3d8bbwe"):
            out.append(Path(local) / "Packages" / pkg / "LocalCache" / "Packages")
    if roaming:
        for name in ("Microsoft Flight Simulator", "Microsoft Flight Simulator 2024"):
            out.append(Path(roaming) / name / "Packages")
    return out


def normalise_root(path: Path) -> list[Path]:
    """Accept whatever the user (or UserCfg.opt) points at and return the folders worth scanning.

    That may be the folder holding Community/Official, its parent (Packages lives inside), or the Community or
    Official folder itself."""
    out: list[Path] = []
    if _is_container(path.name) or path.name.lower() in _CONTAINERS:
        out.append(path.parent)
        if path.name.lower() in ("onestore", "steam"):
            out.append(path.parent.parent)
    out += [path, path / "Packages"]
    seen: list[Path] = []
    for p in out:
        if _is_dir(p) and p not in seen:
            seen.append(p)
    return seen


def _raw_candidates(custom_path: str = "") -> list[Path]:
    raw: list[Path] = []
    if custom_path:
        raw.append(Path(custom_path.strip().strip('"')))
    if os.environ.get("MSFS_PACKAGES_PATH"):
        raw.append(Path(os.environ["MSFS_PACKAGES_PATH"]))
    if sys.platform == "win32":
        for cfg in userconfig_candidates():
            readable = _reachable(cfg, _exists)
            r = packages_path_from_usercfg(readable) if readable else None
            if r:
                raw.append(r)
        raw += default_roots()
    return raw


def candidate_roots(custom_path: str = "") -> list[Path]:
    """Every folder we would scan, in priority order, without duplicates."""
    roots: list[Path] = []
    for r in _raw_candidates(custom_path):
        for variant in (r, *(_junction_targets(r) if not _is_dir(r) else [])):
            for n in normalise_root(variant):
                if n not in roots:
                    roots.append(n)
    return roots


def _has_packages(root: Path) -> bool:
    try:
        return any(c.is_dir() and _is_container(c.name) for c in root.iterdir())
    except OSError:
        return False


def detect_installed(custom_path: str = "") -> set[str] | None:
    """Scan this computer for MSFS aircraft. None = no MSFS install found (nothing to restrict by)."""
    roots = [r for r in candidate_roots(custom_path) if _has_packages(r) or r == Path(custom_path or "?")]
    if not roots:
        log.info("No MSFS packages folder found. Looked in: %s", searched_locations(custom_path))
        return None
    found: set[str] = set()
    for r in roots:
        found |= scan_packages(r)
    log.info("Scanned %s: detected %d installed catalog aircraft: %s", [str(r) for r in roots], len(found),
             sorted(found))
    return found


def searched_locations(custom_path: str = "") -> str:
    """Human-readable list of where detection looked and what stopped it, for messages when nothing is found."""
    notes: list[str] = []
    seen: set[Path] = set()
    for r in _raw_candidates(custom_path):
        if r in seen:
            continue
        seen.add(r)
        via = next((t for t in _junction_targets(r) if _is_dir(t)), None) if not _is_dir(r) else None
        if _is_dir(r) or via:
            where = via or r
            notes.append(f"{r} (found)" if _has_packages(where) else f"{r} (a folder, but no Community or Official inside)")
        else:
            try:
                r.stat()
                notes.append(f"{r} (not a folder)")
            except OSError as exc:
                notes.append(f"{r} ({exc.strerror or 'not found'})")
    return "; ".join(notes[:6]) or "(no MSFS folders found on this computer)"


# ---------------------------------------------------------------- settings helpers
def parse_ids(csv: str) -> set[str]:
    valid = {t.id for t in CATALOG}
    return {p.strip() for p in csv.split(",") if p.strip() in valid}


def format_ids(ids: Iterable[str]) -> str:
    order = [t.id for t in CATALOG]
    return ",".join(sorted(set(ids), key=lambda i: order.index(i) if i in order else 99))


def installed_types(settings: Settings, db=None) -> set[str] | None:
    """Aircraft types usable for jobs, or None when we don't know / restriction is off.

    Types you have flown in the sim (recorded in the logbook) always count as installed.
    """
    if not settings.sim.restrict_to_installed:
        return None
    known = parse_ids(settings.sim.installed_aircraft)
    if not known:
        return None
    if db is not None:
        try:
            known |= {r["type_id"] for r in db.aircraft_flown() if r["type_id"]}
        except Exception:
            pass
    return known


def is_installed(type_id: str, settings: Settings, db=None) -> bool:
    ids = installed_types(settings, db)
    return ids is None or type_id in ids
