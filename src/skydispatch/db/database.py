"""SQLite persistence for the whole career. One instance per app, thread-safe."""
from __future__ import annotations

import csv
import io
import logging
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core import geo
from ..data.airports import AIRPORTS
from .models import Airport, Flight, HangarAircraft, Job, Pilot
from .schema import MIGRATIONS

log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso_in(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        self._seed_airports()

    # ------------------------------------------------------------------ infra
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(args)).fetchall()

    def q1(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(args)).fetchone()

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, tuple(args)).lastrowid or 0

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA user_version")
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                log.info("Applying DB migration %d", i)
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {i}")
                self._conn.commit()

    def backup(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()
        return dest

    @staticmethod
    def restore(src: Path, dest: Path) -> None:
        """Replace the career file (call before opening a Database on `dest`)."""
        probe = sqlite3.connect(str(src))
        try:
            ok = probe.execute("PRAGMA integrity_check").fetchone()[0]
            probe.execute("SELECT 1 FROM pilot LIMIT 1")
        finally:
            probe.close()
        if ok != "ok":
            raise ValueError("Backup file failed integrity check")
        for suffix in ("-wal", "-shm"):
            Path(str(dest) + suffix).unlink(missing_ok=True)
        shutil.copyfile(src, dest)

    # ---------------------------------------------------------------- airports
    def _seed_airports(self) -> None:
        if self.q1("SELECT 1 FROM airports LIMIT 1"):
            return
        with self.tx() as c:
            c.executemany("INSERT OR IGNORE INTO airports VALUES (?,?,?,?,?,?,?,?,?)", AIRPORTS)

    def import_airports_csv(self, airports_csv: str, runways_csv: str | None = None) -> int:
        """Import OurAirports `airports.csv` (+ optional `runways.csv`). Returns rows added."""
        longest: dict[str, int] = {}
        if runways_csv:
            for r in csv.DictReader(io.StringIO(runways_csv)):
                try:
                    length = int(float(r.get("length_ft") or 0))
                except ValueError:
                    continue
                ident = r.get("airport_ident", "")
                if r.get("closed") == "1":
                    continue
                longest[ident] = max(longest.get(ident, 0), length)
        size_map = {"large_airport": "L", "medium_airport": "M", "small_airport": "S"}
        rows = []
        for r in csv.DictReader(io.StringIO(airports_csv)):
            size = size_map.get(r.get("type", ""))
            ident = r.get("ident", "")
            if not size or len(ident) != 4 or not ident.isalpha():
                continue
            try:
                lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
                elev = float(r.get("elevation_ft") or 0)
            except (KeyError, ValueError):
                continue
            rows.append((ident.upper(), r.get("name", ident), r.get("municipality", ""),
                         r.get("iso_country", ""), lat, lon, elev, longest.get(ident, 3000), size))
        with self.tx() as c:
            before = c.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
            c.executemany(
                "INSERT INTO airports VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(icao) DO UPDATE SET name=excluded.name, runway_ft=MAX(runway_ft, excluded.runway_ft)",
                rows)
            after = c.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
        return after - before

    def airport(self, icao: str) -> Airport | None:
        return Airport.from_row(self.q1("SELECT * FROM airports WHERE icao = ?", (icao.upper(),)))

    def airports(self, size: str | None = None) -> list[Airport]:
        if size:
            rows = self.q("SELECT * FROM airports WHERE size = ? ORDER BY icao", (size,))
        else:
            rows = self.q("SELECT * FROM airports ORDER BY icao")
        return [Airport.from_row(r) for r in rows]

    def search_airports(self, text: str, limit: int = 25) -> list[Airport]:
        like = f"%{text.strip()}%"
        rows = self.q("SELECT * FROM airports WHERE icao LIKE ? OR name LIKE ? OR city LIKE ? "
                      "ORDER BY size = 'L' DESC, icao LIMIT ?", (like, like, like, limit))
        return [Airport.from_row(r) for r in rows]

    def nearest_airport(self, lat: float, lon: float) -> tuple[Airport, float] | None:
        best = None
        for a in self.airports():
            d = geo.distance_nm(lat, lon, a.lat, a.lon)
            if best is None or d < best[1]:
                best = (a, d)
        return best

    def airport_distance(self, a: str, b: str) -> float | None:
        pa, pb = self.airport(a), self.airport(b)
        if not pa or not pb:
            return None
        return geo.distance_nm(pa.lat, pa.lon, pb.lat, pb.lon)

    # ------------------------------------------------------------------- pilot
    def pilot(self) -> Pilot | None:
        return Pilot.from_row(self.q1("SELECT * FROM pilot WHERE id = 1"))

    def create_pilot(self, name: str, callsign: str, home_icao: str, balance: float) -> Pilot:
        self.x("INSERT OR REPLACE INTO pilot (id,name,callsign,home_icao,balance,created_at,location_icao) "
               "VALUES (1,?,?,?,?,?,?)", (name, callsign, home_icao.upper(), balance, now_iso(), home_icao.upper()))
        self.add_transaction(0, "career", "Career started", _balance=balance)
        return self.pilot()  # type: ignore[return-value]

    def update_pilot(self, **fields: Any) -> None:
        allowed = {"name", "callsign", "home_icao", "reputation", "xp", "total_minutes", "skill", "location_icao"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Cannot update {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.x(f"UPDATE pilot SET {sets} WHERE id = 1", fields.values())

    def add_transaction(self, amount: float, category: str, description: str, _balance: float | None = None) -> float:
        """Apply a balance change and log it. Returns the new balance."""
        with self.tx() as c:
            if _balance is None:
                row = c.execute("SELECT balance FROM pilot WHERE id = 1").fetchone()
                balance = (row["balance"] if row else 0) + amount
                c.execute("UPDATE pilot SET balance = ? WHERE id = 1", (balance,))
            else:
                balance = _balance
            c.execute("INSERT INTO transactions (ts, amount, category, description, balance_after) VALUES (?,?,?,?,?)",
                      (now_iso(), amount, category, description, balance))
        return balance

    def transactions(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ hangar
    def hangar(self, include_sold: bool = False) -> list[HangarAircraft]:
        sql = "SELECT * FROM hangar" + ("" if include_sold else " WHERE sold = 0") + " ORDER BY id"
        return [HangarAircraft.from_row(r) for r in self.q(sql)]

    def aircraft(self, aircraft_id: int) -> HangarAircraft | None:
        return HangarAircraft.from_row(self.q1("SELECT * FROM hangar WHERE id = ?", (aircraft_id,)))

    def add_aircraft(self, type_id: str, registration: str, location: str, fuel_gal: float,
                     price: float, nickname: str = "", condition: float = 100.0) -> int:
        return self.x(
            "INSERT INTO hangar (type_id,registration,nickname,location_icao,fuel_gal,purchase_price,"
            "purchased_at,condition) VALUES (?,?,?,?,?,?,?,?)",
            (type_id, registration, nickname, location.upper(), fuel_gal, price, now_iso(), condition))

    def update_aircraft(self, aircraft_id: int, **fields: Any) -> None:
        allowed = {"nickname", "location_icao", "hours_total", "hours_since_inspection",
                   "condition", "fuel_gal", "sold"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Cannot update {bad}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.x(f"UPDATE hangar SET {sets} WHERE id = ?", (*fields.values(), aircraft_id))

    def registration_exists(self, reg: str) -> bool:
        return self.q1("SELECT 1 FROM hangar WHERE registration = ?", (reg,)) is not None

    # -------------------------------------------------------------------- jobs
    def add_job(self, **f: Any) -> int:
        cols = ("kind,title,origin,dest,distance_nm,pax,cargo_lb,client,briefing,payout,min_category,"
                "min_runway_ft,min_reputation,deadline_minutes,status,created_at,expires_at,employer_id,provided_type")
        vals = (f["kind"], f["title"], f["origin"], f["dest"], f["distance_nm"], f.get("pax", 0),
                f.get("cargo_lb", 0), f.get("client", ""), f.get("briefing", ""), f["payout"],
                f.get("min_category", "piston"), f.get("min_runway_ft", 0), f.get("min_reputation", 0),
                f.get("deadline_minutes", 0), "offered", now_iso(), f["expires_at"], f.get("employer_id"),
                f.get("provided_type", ""))
        return self.x(f"INSERT INTO jobs ({cols}) VALUES ({','.join('?' * 19)})", vals)

    def job(self, job_id: int) -> Job | None:
        return Job.from_row(self.q1("SELECT * FROM jobs WHERE id = ?", (job_id,)))

    def jobs(self, status: str | tuple[str, ...] = "offered", limit: int = 200, scope: str = "freelance") -> list[Job]:
        """scope: 'freelance' (open-market jobs), 'all', or an employer id (that company's flights)."""
        statuses = (status,) if isinstance(status, str) else status
        marks = ",".join("?" * len(statuses))
        where, args = f"status IN ({marks})", list(statuses)
        if scope == "freelance":
            where += " AND employer_id IS NULL"
        elif scope != "all":
            where += " AND employer_id = ?"
            args.append(scope)
        rows = self.q(f"SELECT * FROM jobs WHERE {where} ORDER BY id DESC LIMIT ?", (*args, limit))
        return [Job.from_row(r) for r in rows]

    def set_job(self, job_id: int, **fields: Any) -> None:
        allowed = {"status", "aircraft_id", "accepted_at", "briefing"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Cannot update {bad}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.x(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))

    def active_job(self) -> Job | None:
        return Job.from_row(self.q1("SELECT * FROM jobs WHERE status IN ('accepted','active') ORDER BY id LIMIT 1"))

    def expire_jobs(self) -> int:
        return self.x("UPDATE jobs SET status = 'expired' WHERE status = 'offered' AND expires_at < ?", (now_iso(),))

    # ----------------------------------------------------------------- flights
    def create_flight(self, job_id: int | None, aircraft_id: int | None, sim_title: str, dep: str,
                      type_id: str = "", employer_id: str | None = None) -> int:
        return self.x("INSERT INTO flights (job_id,aircraft_id,sim_title,dep,started_at,type_id,employer_id) "
                      "VALUES (?,?,?,?,?,?,?)", (job_id, aircraft_id, sim_title, dep, now_iso(), type_id, employer_id))

    def flight_history(self) -> list[sqlite3.Row]:
        """Every finished flight with air time (used for qualifications)."""
        return self.q("SELECT started_at, air_min, type_id, outcome, employer_id FROM flights "
                      "WHERE outcome != 'in_progress' AND air_min > 0 ORDER BY started_at")

    def finish_flight(self, flight_id: int, **fields: Any) -> None:
        allowed = {"arr", "block_min", "air_min", "distance_nm", "fuel_used_gal", "landing_fpm", "max_g",
                   "max_ias", "max_alt_ft", "overspeed_s", "score", "outcome", "payout", "costs",
                   "summary", "debrief", "ended_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Cannot update {bad}")
        fields.setdefault("ended_at", now_iso())
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.x(f"UPDATE flights SET {sets} WHERE id = ?", (*fields.values(), flight_id))

    def flight(self, flight_id: int) -> Flight | None:
        return Flight.from_row(self.q1("SELECT * FROM flights WHERE id = ?", (flight_id,)))

    def flights(self, limit: int = 500) -> list[Flight]:
        return [Flight.from_row(r) for r in self.q("SELECT * FROM flights ORDER BY id DESC LIMIT ?", (limit,))]

    def add_telemetry(self, flight_id: int, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.tx() as c:
            c.executemany("INSERT INTO telemetry (flight_id,t,lat,lon,alt_ft,ias,gs,hdg,vs,fuel_gal,on_ground) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)", [(flight_id, *r) for r in rows])

    def telemetry(self, flight_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM telemetry WHERE flight_id = ? ORDER BY t", (flight_id,))

    def add_event(self, flight_id: int, t: float, kind: str, detail: str = "") -> None:
        self.x("INSERT INTO flight_events (flight_id,t,kind,detail) VALUES (?,?,?,?)", (flight_id, t, kind, detail))

    def events(self, flight_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM flight_events WHERE flight_id = ? ORDER BY t", (flight_id,))

    def record_aircraft_flown(self, title: str, type_id: str | None, minutes: float) -> None:
        if not title:
            return
        ts = now_iso()
        self.x("INSERT INTO aircraft_flown (sim_title,type_id,first_flown,last_flown,flights,minutes) "
               "VALUES (?,?,?,?,1,?) ON CONFLICT(sim_title) DO UPDATE SET last_flown=excluded.last_flown, "
               "flights=flights+1, minutes=minutes+excluded.minutes, type_id=COALESCE(excluded.type_id,type_id)",
               (title, type_id, ts, ts, minutes))

    def aircraft_flown(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM aircraft_flown ORDER BY minutes DESC")

    # ---------------------------------------------------------------- messages
    def add_message(self, role: str, content: str, thread: str = "general", kind: str = "text",
                    payload: str = "") -> int:
        return self.x("INSERT INTO messages (ts, role, content, thread, kind, payload) VALUES (?,?,?,?,?,?)",
                      (now_iso(), role, content, thread, kind, payload))

    def messages(self, limit: int = 100, thread: str = "general") -> list[sqlite3.Row]:
        rows = self.q("SELECT * FROM messages WHERE thread = ? ORDER BY id DESC LIMIT ?", (thread, limit))
        return list(reversed(rows))

    def last_message(self, thread: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM messages WHERE thread = ? ORDER BY id DESC LIMIT 1", (thread,))

    def clear_messages(self, thread: str | None = None) -> None:
        if thread is None:
            self.x("DELETE FROM messages")
        else:
            self.x("DELETE FROM messages WHERE thread = ?", (thread,))

    # --------------------------------------------------------------- employment
    def employments(self, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM employment" + (" WHERE status = 'active'" if active_only else "") + " ORDER BY hired_at"
        return self.q(sql)

    def employment(self, employer_id: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM employment WHERE employer_id = ?", (employer_id,))

    def is_employed_by(self, employer_id: str) -> bool:
        row = self.employment(employer_id)
        return bool(row and row["status"] == "active")

    def hire(self, employer_id: str) -> None:
        self.x("INSERT INTO employment (employer_id, hired_at, status) VALUES (?,?, 'active') "
               "ON CONFLICT(employer_id) DO UPDATE SET status = 'active', hired_at = excluded.hired_at",
               (employer_id, now_iso()))

    def resign(self, employer_id: str) -> None:
        self.x("UPDATE employment SET status = 'resigned' WHERE employer_id = ?", (employer_id,))

    def add_employment_stats(self, employer_id: str, minutes: float, earned: float) -> None:
        self.x("UPDATE employment SET flights = flights + 1, minutes = minutes + ?, earned = earned + ? "
               "WHERE employer_id = ?", (minutes, earned, employer_id))

    def add_application(self, employer_id: str, status: str, message: str, snapshot: str) -> int:
        return self.x("INSERT INTO applications (employer_id, applied_at, status, message, snapshot) VALUES (?,?,?,?,?)",
                      (employer_id, now_iso(), status, message, snapshot))

    def applications(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM applications ORDER BY id DESC LIMIT ?", (limit,))

    # -------------------------------------------------------------------- meta
    def get_meta(self, key: str, default: str = "") -> str:
        row = self.q1("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.x("INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))

    # ------------------------------------------------------------------- reset
    def reset_career(self) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM meta WHERE key LIKE 'enhanced_%' OR key LIKE 'avail%' OR key LIKE 'hire_%' "
                      "OR key LIKE 'bills_%' OR key LIKE 'alert_%'")
            for table in ("telemetry", "flight_events", "flights", "jobs", "hangar", "transactions",
                          "aircraft_flown", "messages", "employment", "applications", "vacancies", "certificates",
                          "training", "pilot"):
                c.execute(f"DELETE FROM {table}")
