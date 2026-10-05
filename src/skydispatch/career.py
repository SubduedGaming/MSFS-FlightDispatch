"""The career engine: accepts jobs, records flights, pays you, wears out planes.

This class owns the rules of the game and has no GUI code. The UI and the AI
dispatcher subscribe to its events via ``subscribe()``.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .core.config import Settings
from .data.aircraft import AircraftType, get_type, match_title
from .data.employers import Employer, get_employer
from .economy import Economy
from .jobs.hiring import Hiring
from .pilot import quals as quals_mod
from .pilot.credentials import RATING_LABEL, Credentials, rating_for_type
from .sim.installed import is_installed
from .jobs.employer_jobs import EmployerDispatch, usable_fleet
from .db.database import Database, now_iso
from .db.models import Airport, HangarAircraft, Job
from .flight.recorder import FlightRecorder, Live
from .flight.scoring import FlightMetrics, ScoreResult, score_flight
from .hangar.service import HangarService, airworthiness
from .jobs.generator import JobGenerator
from .jobs.pricing import check_eligibility, Eligibility
from .sim.base import SimState

log = logging.getLogger(__name__)

Listener = Callable[[str, dict], None]


class CareerError(Exception):
    """A rule violation that should be shown to the user."""


@dataclass
class Settlement:
    flight_id: int
    job: Job | None
    metrics: FlightMetrics
    score: ScoreResult
    payout: float
    costs: float
    notes: list[str]
    arrival: str
    employer_id: str | None = None
    skill_before: float = 0.0
    skill_after: float = 0.0


@dataclass
class ApplicationResult:
    employer: Employer
    accepted: bool
    checks: list
    message: str


class Career:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.hangar = HangarService(db)
        self.jobs = JobGenerator(db, settings)
        self.employer_dispatch = EmployerDispatch(db, settings)
        self.credentials = Credentials(db, settings)       # licence, medical, ratings, training
        self.economy = Economy(db, settings)               # monthly bills
        self.hiring = Hiring(db, settings)                 # which companies are recruiting
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()
        self.recorder: FlightRecorder | None = None
        self._flight_id: int | None = None
        self._job: Job | None = None
        self._aircraft: HangarAircraft | None = None
        self._flags: set[str] = set()
        self._sample_flush = 0
        self.last_state: SimState | None = None
        self.last_settlement: Settlement | None = None

    # ------------------------------------------------------------- events
    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _fire(self, name: str, **payload: Any) -> None:
        for fn in list(self._listeners):
            try:
                fn(name, payload)
            except Exception:
                log.exception("listener failed on %s", name)

    # ----------------------------------------------------------- new career
    def start_career(self, name: str, callsign: str, home_icao: str, starter_type: str, balance: float) -> None:
        self.db.reset_career()
        self.db.create_pilot(name, callsign, home_icao, balance)
        self.hangar.starter_fleet(starter_type, home_icao)
        self.credentials.ensure_defaults()
        self.credentials.grant_for_aircraft(starter_type)          # the starter aircraft comes with its rating
        self.economy.schedule()
        self.hiring.seed()
        self.jobs.refresh()
        self._fire("career_started")

    @property
    def has_career(self) -> bool:
        return self.db.pilot() is not None

    # --------------------------------------------------------------- market
    def refresh_market(self) -> int:
        n = self.jobs.refresh()
        self._fire("market_changed")
        return n

    def eligible_aircraft(self, job: Job) -> list[tuple[HangarAircraft, Eligibility]]:
        out = []
        o, d = self.db.airport(job.origin), self.db.airport(job.dest)
        for a in self.db.hangar():
            t = get_type(a.type_id)
            if not t:
                continue
            fuel_needed = job.distance_nm / max(60, t.cruise_kts) * t.fuel_gph * 1.2
            e = check_eligibility(job, t, o.runway_ft if o else 0, d.runway_ft if d else 0, None,
                                  a.location_icao, fuel_ok=a.fuel_gal >= fuel_needed)
            ok, why = airworthiness(a, t)
            if not ok:
                e.ok = False
                e.reasons.append(why)
            need = rating_for_type(t)
            if need and not self.credentials.has(need):
                e.ok = False
                e.reasons.append(f"Needs the {RATING_LABEL[need].lower()} (see Training)")
            if not is_installed(a.type_id, self.settings, self.db):
                e.ok = False
                e.reasons.append(f"{t.name} is not installed in your simulator")
            out.append((a, e))
        return out

    def accept_job(self, job_id: int, aircraft_id: int | None = None) -> Job:
        with self._lock:
            pilot = self.db.pilot()
            job = self.db.job(job_id)
            if not pilot or not job:
                raise CareerError("Job not found")
            grounded = self.credentials.grounded_reason()
            if grounded:
                raise CareerError(grounded)
            if job.status != "offered":
                raise CareerError("That job is no longer available")
            if self.db.active_job():
                raise CareerError("Finish or abandon your current job first")
            if self.recorder and self.recorder.started and not self.recorder.finished:
                raise CareerError("Finish your current flight before accepting a job")
            if job.employer_id:
                self._check_employer_job(job)
                self.db.set_job(job_id, status="accepted", aircraft_id=None, accepted_at=now_iso())
                for other in self.db.jobs("offered", scope=job.employer_id):
                    if other.id != job_id:
                        self.db.set_job(other.id, status="expired")
            else:
                if pilot.reputation < job.min_reputation:
                    raise CareerError(f"Requires reputation {job.min_reputation:.0f} (you have {pilot.reputation:.0f})")
                if not self.is_owner_operator():
                    raise CareerError("Freelance contracts are for pilots who own an aircraft. Buy one in the "
                                      "Hangar, or get hired by a company and let their dispatcher assign your flights.")
                aircraft = self.db.aircraft(aircraft_id) if aircraft_id else None
                if not aircraft or aircraft.sold:
                    raise CareerError("Pick an aircraft from your hangar")
                elig = {a.id: e for a, e in self.eligible_aircraft(job)}[aircraft_id]
                if not elig.ok:
                    raise CareerError("; ".join(elig.reasons))
                self.db.set_job(job_id, status="accepted", aircraft_id=aircraft_id, accepted_at=now_iso())
            # Other offers stay on the market; only one active job at a time.
            self._arm(self.db.job(job_id))  # type: ignore[arg-type]
            self._fire("job_accepted", job=self.db.job(job_id))
            return self.db.job(job_id)  # type: ignore[return-value]

    def _check_employer_job(self, job: Job) -> None:
        employer = get_employer(job.employer_id or "")
        if employer is None or not self.db.is_employed_by(employer.id):
            raise CareerError("You don't work for that company")
        t = get_type(job.provided_type)
        if t is None:
            raise CareerError("This flight has no aircraft assigned")
        if not is_installed(t.id, self.settings, self.db):
            raise CareerError(f"The {t.name} is not installed in your simulator")

    def decline_job(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job or job.status != "offered":
            raise CareerError("That job is no longer available")
        self.db.set_job(job_id, status="declined")
        self._fire("job_declined", job=job)
        if job.employer_id:
            return
        if len(self.db.jobs("offered")) < self.settings.game.job_count // 2:
            self.refresh_market()
        else:
            self._fire("market_changed")

    def abandon_job(self) -> None:
        with self._lock:
            job = self.db.active_job()
            if not job:
                return
            if self.recorder and self.recorder.started and not self.recorder.finished:
                self.recorder.abort()      # settles via _on_event
            else:
                self.db.set_job(job.id, status="failed")
                pilot = self.db.pilot()
                if pilot:                  # walking away from a flight a dispatcher rostered you on costs more
                    self.db.update_pilot(reputation=max(0.0, pilot.reputation - (3.0 if job.employer_id else 1.0)))
                self._disarm()
                self._fire("job_abandoned", job=job)
                self._fire("pilot_changed")

    # ------------------------------------------------------------- recording
    def resume(self) -> None:
        """After an app restart: close flights that were interrupted and re-arm the accepted job."""
        self.db.x("UPDATE flights SET outcome = 'aborted', ended_at = ?, summary = 'Interrupted (app closed)' "
                  "WHERE outcome = 'in_progress'", (now_iso(),))
        self.db.x("UPDATE jobs SET status = 'accepted' WHERE status = 'active'")
        job = self.db.active_job()
        if job and not self.recorder:
            self._arm(job)
        self.housekeeping()

    def _arm(self, job: Job) -> None:
        self._job = job
        self._aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        atype = get_type(job.provided_type) if job.employer_id else (
            get_type(self._aircraft.type_id) if self._aircraft else None)
        self._new_recorder(self.db.airport(job.origin), self.db.airport(job.dest), atype, job.deadline_minutes)

    def _new_recorder(self, origin: Airport | None, dest: Airport | None, atype: AircraftType | None,
                      deadline: int) -> None:
        self._flags = set()
        self._flight_id = None
        self._sample_flush = 0
        self.recorder = FlightRecorder(
            origin, dest, atype, deadline, on_event=self._on_event,
            nearest=lambda la, lo: (self.db.nearest_airport(la, lo) or (None, 0))[0])

    def _disarm(self) -> None:
        self.recorder = None
        self._job = None
        self._aircraft = None
        self._flight_id = None

    def feed(self, state: SimState) -> None:
        """Called for every telemetry sample (UI thread)."""
        with self._lock:
            self.last_state = state
            if self.recorder is None:
                if self.db.pilot() is None:
                    return
                # No job: still log free flights so every aircraft you fly is recorded.
                self._new_recorder(None, None, match_title(state.title), 0)
                self._job = None
            self.recorder.feed(state)

    def live(self) -> Live | None:
        return self.recorder.live() if self.recorder else None

    @property
    def active_job(self) -> Job | None:
        return self._job

    # ------------------------------------------------------------- recorder events
    def _on_event(self, kind: str, detail: str, data: dict) -> None:
        rec = self.recorder
        if rec is None:
            return
        if kind == "start":
            state = self.last_state
            if self._job is None and state and rec.origin is None:
                near = self.db.nearest_airport(state.lat, state.lon)
                rec.origin = near[0] if near and near[1] < 8 else None
            dep = rec.origin.icao if rec.origin else ""
            aid = self._aircraft.id if self._aircraft else None
            flown = match_title(rec.sim_title) if rec.sim_title else None
            type_id = flown.id if flown else (rec.atype.id if rec.atype else "")
            self._flight_id = self.db.create_flight(self._job.id if self._job else None, aid, rec.sim_title, dep,
                                                    type_id, self._job.employer_id if self._job else None)
            if self._job:
                self.db.set_job(self._job.id, status="active")
        if self._flight_id:
            self.db.add_event(self._flight_id, rec.t, kind, detail)
        if kind in ("wrong_aircraft", "slew"):
            self._flags.add(kind)
        if kind == "finished":
            self._settle()
            return
        self._fire("flight_event", kind=kind, detail=detail, data=data, job=self._job)

    def _settle(self) -> None:
        rec = self.recorder
        if rec is None or self._flight_id is None:
            self._disarm()
            return
        m = rec.metrics()
        s = score_flight(m, self.settings.game.difficulty)
        job, aircraft = self._job, self._aircraft
        atype = get_type(aircraft.type_id) if aircraft else rec.atype
        notes: list[str] = []
        payout = 0.0
        costs = 0.0
        if job and m.outcome == "completed":
            mult = s.payout_mult
            if "wrong_aircraft" in self._flags:
                if self.settings.game.strict_aircraft:
                    mult = 0.0
                    notes.append("Wrong aircraft flown - contract voided (strict mode)")
                else:
                    mult *= 0.8
                    notes.append("Flew a different aircraft than assigned: payout -20%")
            payout = round(job.payout * mult, 2)
        employer_id = job.employer_id if job else None
        if atype and m.air_min > 0 and not employer_id:
            costs = round(atype.hourly_cost * m.air_min / 60.0, 2)
        elif employer_id and m.air_min > 0:
            notes.append("Company aircraft: your employer covers fuel and operating costs")
        arrival = rec.arrival.icao if rec.arrival else (job.dest if job and m.outcome == "completed" else "")
        # hangar wear/location
        if aircraft and atype and m.outcome in ("completed", "diverted", "crashed", "aborted"):
            if m.outcome == "crashed":
                self.db.update_aircraft(aircraft.id, condition=0.0)
                notes.append("The aircraft was destroyed and is unflyable until repaired")
            else:
                fuel_after = max(0.0, aircraft.fuel_gal - m.fuel_used_gal)
                loc = arrival or aircraft.location_icao
                notes += self.hangar.apply_flight(
                    aircraft.id, m.air_min / 60.0, loc, fuel_after, m.landing_fpm, m.overspeed_s,
                    m.max_g, wear=self.settings.game.wear_enabled)
        # money
        if payout:
            self.db.add_transaction(payout, "job", f"Job #{job.id} {job.origin}-{job.dest}")  # type: ignore[union-attr]
        if costs:
            self.db.add_transaction(-costs, "operating", f"Operating cost ({m.air_min / 60:.1f} h)")
        # pilot
        pilot = self.db.pilot()
        skill_before = skill_after = pilot.skill if pilot else 0.0
        if pilot:
            skill_after = skill_before
            if m.outcome != "aborted" and m.air_min >= 3:       # flying badly (or crashing) lowers your skill rating
                skill_after = max(0.0, min(100.0, skill_before * 0.85 + s.score * 0.15))
            updates = dict(reputation=max(0.0, min(100.0, pilot.reputation + s.reputation_delta)),
                           xp=pilot.xp + s.xp, total_minutes=pilot.total_minutes + m.air_min, skill=skill_after)
            if arrival and m.outcome in ("completed", "diverted"):
                updates["location_icao"] = arrival
            self.db.update_pilot(**updates)
        if employer_id and self.db.employment(employer_id):
            self.db.add_employment_stats(employer_id, m.air_min, payout)
        # job status
        if job:
            self.db.set_job(job.id, status="completed" if m.outcome == "completed" else "failed")
        summary = f"{rec.origin.icao if rec.origin else '----'} to {arrival or '----'}: " \
                  f"{m.outcome}, score {s.score:.0f} ({s.grade})"
        self.db.finish_flight(
            self._flight_id, arr=arrival, block_min=m.block_min, air_min=m.air_min, distance_nm=m.distance_nm,
            fuel_used_gal=m.fuel_used_gal, landing_fpm=m.landing_fpm, max_g=m.max_g, max_ias=rec.max_ias,
            max_alt_ft=rec.max_alt, overspeed_s=m.overspeed_s, score=s.score, outcome=m.outcome,
            payout=payout, costs=costs, summary=summary)
        self.db.add_telemetry(self._flight_id, rec.samples)
        flown = match_title(rec.sim_title) if rec.sim_title else None
        flown_id = flown.id if flown else (atype.id if atype else None)
        self.db.record_aircraft_flown(rec.sim_title, flown_id, m.air_min)
        settlement = Settlement(self._flight_id, job, m, s, payout, costs, notes + s.penalties, arrival,
                                employer_id, skill_before, skill_after)
        self.last_settlement = settlement
        self._disarm()
        self._fire("flight_finished", settlement=settlement)
        self._fire("pilot_changed")
        self._fire("hangar_changed")
        self._fire("market_changed")

    # ----------------------------------------------------------- qualifications & employers
    def qualifications(self):
        return quals_mod.compute(self.db, half_life_days=self.settings.game.recency_half_life_days)

    def is_owner_operator(self) -> bool:
        """Freelance contracts are only for pilots who own an aircraft."""
        return bool(self.db.hangar())

    def housekeeping(self, now: datetime | None = None) -> None:
        """Real-time upkeep: vacancies open and close, ground school finishes, bills fall due, certificates near expiry.
        Safe to call as often as you like."""
        now = now or datetime.now(timezone.utc)
        if not self.db.pilot():
            return
        self.credentials.ensure_defaults(now)
        for eid in self.hiring.refresh(now):
            self._fire("vacancy_opened", employer_id=eid)
        for course in self.credentials.process(now):
            self._fire("training_done", course=course)
        charged = self.economy.process(now)
        if charged:
            self._fire("bills_charged", items=charged)
            self._fire("pilot_changed")
        for w in self.credentials.expiring(now):
            self.credentials.mark_warned(w["kind"], w["state"])
            self._fire("credential_expiring", **w)

    def employer_fleet_note(self, employer: Employer) -> str | None:
        """None when the pilot can fly for this company, otherwise why not (aircraft not installed)."""
        if usable_fleet(employer, self.settings, self.db):
            return None
        names = ", ".join(t.name for t in employer.fleet_types())
        return f"Needs {names} installed in your sim"

    def apply_to_employer(self, employer_id: str) -> ApplicationResult:
        employer = get_employer(employer_id)
        if employer is None:
            raise CareerError("Unknown company")
        if self.db.is_employed_by(employer_id):
            raise CareerError(f"You already work for {employer.name}")
        cooldown = self.hiring.cooldown_until(employer_id)
        if cooldown:
            raise CareerError(f"{employer.name} turned you down recently. You can apply again after {cooldown:%d %b}.")
        if not self.hiring.open_vacancy(employer_id):
            raise CareerError(f"{employer.name} is not recruiting right now. Vacancies open from time to time; "
                              "the Job Board shows who is hiring.")
        note = self.employer_fleet_note(employer)
        if note:
            raise CareerError(f"{employer.name} can't take you on yet: {note.lower()}.")
        q = self.qualifications()
        checks = quals_mod.check_requirements(employer.reqs, q, employer)
        accepted = all(c.met for c in checks)
        if accepted:
            message = (f"Congratulations! {employer.name} is pleased to offer you a position as a pilot. "
                       f"You'll fly {', '.join(t.name for t in employer.fleet_types())} with a pay rate of "
                       f"{employer.pay_factor:.0%} of the standard rate, with the company covering fuel and operating costs.")
        else:
            short = [f"{c.label}: need {c.required}, you have {c.actual}" for c in checks if not c.met]
            message = (f"Thank you for applying to {employer.name}. We can't offer you a position yet. "
                       + "; ".join(short) + ". Please apply again when you meet these requirements.")
        if not accepted:
            until = self.hiring.reject(employer_id)
            message += f" You can apply again after {until:%d %b}."
        self.db.add_application(employer_id, "accepted" if accepted else "rejected", message, q.to_json())
        if accepted:
            self.db.hire(employer_id)
            self.hiring.fill(employer_id)
        result = ApplicationResult(employer, accepted, checks, message)
        self._fire("application_result", result=result)
        if accepted:
            self._fire("hired", employer=employer)
        return result

    def resign(self, employer_id: str) -> None:
        job = self.db.active_job()
        if job and job.employer_id == employer_id:
            raise CareerError("Finish or abandon your current flight before resigning")
        self.db.resign(employer_id)
        for j in self.db.jobs("offered", scope=employer_id):
            self.db.set_job(j.id, status="expired")
        self._fire("employment_changed", employer_id=employer_id)

    def flush_telemetry(self) -> None:
        """Persist buffered telemetry (called periodically so a crash loses little)."""
        rec = self.recorder
        if rec and self._flight_id and rec.samples:
            self.db.add_telemetry(self._flight_id, rec.samples)
            rec.samples.clear()
