"""Engine: owns the long-lived services (career, database, sim, AI, voice) without any GUI toolkit.

Everything here runs on one "engine thread" (``self.main``) so the career, database and AI services keep their
single-thread rule. Blocking work (LLM, network, disk) goes through ``run_async``; its callbacks are handed back to the
engine thread. The signals are ``core.events.Event`` objects with the same ``connect``/``emit`` surface as Qt signals.

The desktop ``ui.context.AppContext`` subclasses this and swaps in Qt signals and the Qt GUI thread.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..ai.dispatcher import EVENT_IMPORTANCE, Dispatcher, employer_thread, thread_employer
from ..ai.llm import LLMError
from ..ai.personas import HR_VOICE, get_persona
from ..career import ApplicationResult, Career, CareerError, Settlement
from ..pilot import quals as quals_mod
from ..pilot.credentials import COURSES, CredentialError, required_ratings
from ..copilot.copilot import CoPilot
from ..copilot.personas import get_copilot
from ..core import fmt
from ..core.config import Settings
from ..core.events import Event
from ..core.executor import SerialExecutor
from ..core.paths import database_path
from ..db.database import Database
from ..sim.base import SimProvider, SimState
from ..sim.bridge_server import BridgeHost
from ..sim.factory import make_provider
from ..sim.installed import detect_installed, format_ids, parse_ids, searched_locations
from ..sim.simulated import SimulatedProvider
from ..data.employers import flight_label, flight_number, get_employer
from ..planning import simbrief
from ..planning.loadout import LoadoutError, compare, plan_loadout, ready_problem
from .. import __version__, updater
from ..voice import tts as tts_mod
from ..voice.characters import voices_in_use
from ..voice.service import VoiceService
from ..voice.text import speakable
from .discovery import Advertiser
from .speech import SpeechStore, Utterance
from .pairing import PairingManager, format_code, lan_addresses, pair_uri

log = logging.getLogger(__name__)

TICK_SECONDS = 30
CURRENCIES = ("$", "£", "€", "¥")


class Engine:
    # Signal names, as on the desktop context (a subclass may declare real Qt signals of the same names instead).
    EVENTS = ("sim_state", "sim_status", "career_event", "thread_changed", "chat_busy", "ai_status", "toast",
              "listening", "settings_changed", "update_available", "plan_changed", "loadout_report", "speech",
              "speech_stop")

    def __init__(self, settings: Settings, db: Database, main=None):
        self.settings = settings
        self.db = db
        self._own_main = main is None
        self.main = main if main is not None else SerialExecutor()       # needs .post(fn) and .run(fn, timeout)
        self._make_events()
        self.career = Career(db, settings)
        self.dispatcher = Dispatcher(self.career, settings)
        self.copilot = CoPilot(self.career, settings)
        persona = get_persona(settings.ai.persona)
        self.voice = VoiceService(settings, persona.system_voice_hint, persona.piper_voice)
        self.provider: SimProvider | None = None
        self.sim_connected = False
        self.share_host: BridgeHost | None = None
        self.share_error = ""
        self.web = None                       # web.server.WebRemote, created on first start_web()
        self.web_error = ""
        self.advertiser = Advertiser()
        self.speech_store = SpeechStore()     # utterances a phone can fetch the audio for
        self.pairing = PairingManager()      # phones paired with this server (devices.json)
        self.update_info: updater.UpdateInfo | None = None
        self.ofp: simbrief.Ofp | None = None
        try:
            raw = db.get_meta("simbrief_ofp", "")
            self.ofp = simbrief.Ofp.from_json(json.loads(raw)) if raw else None
        except (ValueError, TypeError):
            self.ofp = None
        self._synced_job: int | None = None
        self._auto_problem = ""
        self._stable_since: float | None = None      # since when the aircraft has been ready to load
        self._verify_tries: dict[int, int] = {}
        self.viewing: dict[str, str] = {}      # viewer (desktop page or browser remote) -> conversation it is showing
        self.closed = False
        self._pending: dict[str, int] = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="skydispatch-work")
        # Dispatcher comments, debriefs and follow-up questions must appear in the order things happened.
        self._ai_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skydispatch-ai")
        self.career.subscribe(self._on_career_event)
        self.sim_state.connect(self._feed)
        self.career_event.connect(self._react)
        self._start_ticker()

    # ------------------------------------------------------- threading hooks
    def _make_events(self) -> None:
        for name in self.EVENTS:
            setattr(self, name, Event())

    def _on_main(self) -> bool:
        return self.main.on_worker

    def _post_main(self, fn) -> None:
        self.main.post(fn)

    def _to_main(self, fn) -> None:
        """Run `fn` on the engine thread: straight away if we are on it, else queued."""
        if self._on_main():
            fn()
        else:
            self._post_main(fn)

    def run_async(self, fn, on_done=None, on_error=None, ordered: bool = False) -> None:
        """Run blocking `fn` on a worker thread; `on_done(result)` / `on_error(message)` run on the engine thread.
        `ordered=True` uses a single-worker pool so tasks finish in the order they were started."""
        def finish(cb, value):
            if cb:
                self._post_main(lambda: cb(value))

        def task():
            try:
                result = fn()
            except Exception as exc:
                log.exception("background task failed")
                finish(on_error, str(exc) or exc.__class__.__name__)
            else:
                finish(on_done, result)
        try:
            (self._ai_pool if ordered else self._pool).submit(task)
        except RuntimeError:                       # shutting down
            pass

    def call_later(self, ms: int, fn) -> None:
        t = threading.Timer(ms / 1000, lambda: self._post_main(fn))
        t.daemon = True
        t.start()

    def _start_ticker(self) -> None:
        self._ticker_stop = threading.Event()

        def loop():
            while not self._ticker_stop.wait(TICK_SECONDS):
                self._post_main(self._tick)
        threading.Thread(target=loop, name="skydispatch-tick", daemon=True).start()

    def _stop_ticker(self) -> None:
        self._ticker_stop.set()

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, db_path: Path | None = None) -> "Engine":
        settings = Settings.load()
        db = Database(db_path or database_path())
        engine = cls(settings, db)
        engine.career.resume()
        return engine

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._stop_ticker()
        self.stop_sim()
        self.stop_share()
        self.stop_web()
        self._pool.shutdown(wait=False, cancel_futures=True)       # in-flight tasks finish on their own
        self._ai_pool.shutdown(wait=False, cancel_futures=True)
        self.career.flush_telemetry()
        self.voice.shutdown()
        self.settings.save()
        self.db.close()
        if self._own_main:
            self.main.close()

    def apply_settings(self) -> None:
        """Call after the user edits settings: persist and re-wire services."""
        self.settings.save()
        self.dispatcher.client.cfg = self.settings.ai
        self.copilot.client.cfg = self.settings.ai
        self.dispatcher._prompt_tools = self.settings.ai.tool_mode == "prompt"
        persona = get_persona(self.settings.ai.persona)
        self.voice.tts.voice_hint = persona.system_voice_hint
        self.voice.tts.persona_voice = persona.piper_voice
        self.settings_changed.emit()

    # ------------------------------------------------------------------ sim
    def start_sim(self) -> None:
        self.stop_sim()
        self.provider = make_provider(self.settings.sim)
        self.provider.on_state = self._provider_state
        self.provider.on_status = self._on_status
        self.start_share()
        self.provider.start()
        if self.settings.sim.installed_auto:
            self.detect_installed()

    def stop_sim(self) -> None:
        if self.provider:
            self.provider.on_state = None
            self.provider.on_status = None
            self.provider.stop()
            self.provider = None
        self.sim_connected = False

    def _provider_state(self, state: SimState) -> None:
        host = self.share_host
        if host:
            host.publish_state(state)
        self._to_main(lambda: self.sim_state.emit(state))       # the sim thread must not touch career state

    # -------------------------------------------------------------- sharing
    def start_share(self) -> None:
        """Let SkyDispatch on other computers connect to this one (MSFS runs here, so this is the host)."""
        self.stop_share()
        sim = self.settings.sim
        if not sim.share_enabled or sim.mode == "bridge":
            return
        if not sim.share_token:
            sim.share_token = secrets.token_urlsafe(12)
            self.settings.save()
        host = BridgeHost("0.0.0.0", sim.share_port, sim.share_token,
                          installed=lambda: sorted(parse_ids(self.settings.sim.installed_aircraft)))
        try:
            host.start()
        except OSError as e:
            self.share_error = f"Could not share on port {sim.share_port}: {e}"
            log.warning(self.share_error)
            self.toast.emit("warn", self.share_error)
            return
        self.share_error = ""
        self.share_host = host

    def stop_share(self) -> None:
        if self.share_host:
            self.share_host.stop()
            self.share_host = None

    # ------------------------------------------------------------------ speech
    def character_voices(self) -> tuple[list[tuple[str, str]], list[str]]:
        """(voices the people you talk to use, those of them not downloaded yet)."""
        needed = voices_in_use(self.settings, [e["employer_id"] for e in self.db.employments()])
        return needed, [v for v, _ in needed if not tts_mod.piper_installed(v)]

    def download_character_voices(self, progress=None, done=None) -> None:
        """Fetch the natural (Piper) voices for everyone you talk to. Runs in the background."""
        _needed, missing = self.character_voices()
        if not missing:
            if done:
                done([])
            return

        def finished(fetched):
            if self.closed:
                return
            self.voice.tts._engine = None                  # pick the neural voices up straight away
            self.toast.emit("good", f"Downloaded {len(fetched)} voice(s). Everyone now sounds like themselves.")
            self.settings_changed.emit()
            if done:
                done(fetched)

        def failed(err: str):
            if not self.closed:
                self.toast.emit("warn", f"Voice download failed: {err}")
                if done:
                    done(None)
        self.run_async(lambda: tts_mod.download_voices(missing, progress), finished, failed)

    def audible(self, thread: str) -> bool:
        """Speech for `thread` is wanted only while someone is looking at that conversation. The copilot also speaks
        during a flight, when the pilot is in the sim and not looking at any window."""
        return thread in self.viewing.values() or (thread == "copilot" and self.copilot.in_flight())

    def _audible_tags(self) -> set[str]:
        tags = set(self.viewing.values())
        if self.copilot.in_flight():
            tags.add("copilot")
        return tags

    def set_viewing(self, viewer: str, thread: str | None) -> None:
        """A page (or the browser remote) now shows `thread`, or nothing. Speech for anything else is cancelled."""
        if thread:
            self.viewing[viewer] = thread
        else:
            self.viewing.pop(viewer, None)
        self.silence(self._audible_tags())

    def speak(self, text: str, thread: str, voice: str = "", speed: float | None = None) -> None:
        """Speak `text` as whoever owns `thread`, if speech is on and that conversation is being looked at.
        Where it is heard depends on ``voice.output``: this PC's speakers, or the phone (a ``speech`` event)."""
        if self.closed or not self.settings.voice.auto_speak_replies or not self.audible(thread):
            return
        who = get_copilot(self.settings.ai.copilot) if thread == "copilot" else self.dispatcher.persona_for(thread)
        voice = voice or who.piper_voice
        speed = who.rate if speed is None else speed
        if self.settings.voice.output == "phone":
            self._speak_on_phone(text, thread, voice, speed)
        else:
            self.voice.say(text, voice, tag=thread, speed=speed)

    def _speak_on_phone(self, text: str, thread: str, voice: str, speed: float) -> None:
        clean = speakable(text)
        if not clean:
            return
        utt = self.speech_store.add(thread, clean, voice, speed)
        self.speech.emit({"id": utt.id, "thread": thread, "text": clean, "voice": voice, "speed": speed,
                          "audio": self.voice.tts.can_synthesize(voice)})

    def render_utterance(self, utt: Utterance) -> bytes | None:
        """The WAV for an utterance (made once, then kept), or None when this PC cannot render audio. Slow: call it
        from a request thread, never from the engine thread."""
        with utt.lock:
            if utt.wav is None:
                utt.wav = self.voice.tts.synthesize(utt.text, utt.voice, utt.speed)
            return utt.wav

    def silence(self, keep: set[str] | None = None) -> None:
        """Stop speech. With `keep`, only speech that belongs to other conversations stops. Reaches the phone too."""
        if keep is None:
            self.voice.shut_up()
        else:
            self.voice.drop_unless(keep)
        self.speech_stop.emit({"keep": sorted(keep or ())})

    def submit_player_text(self, text: str, thread: str) -> None:
        """What the player said or typed into a conversation (the copilot has its own entry)."""
        if thread == "copilot":
            self.ask_copilot(text=text)
        else:
            self.ask(text, thread)

    # ------------------------------------------------- flight plan and loadout
    def _job_context(self):
        """(job, aircraft type, hangar aircraft) for the active contract, or None."""
        from ..data.aircraft import get_type
        job = self.career.active_job or self.db.active_job()
        if job is None:
            return None
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        atype = get_type(job.provided_type) if job.employer_id else (get_type(aircraft.type_id) if aircraft else None)
        return (job, atype, aircraft) if atype else None

    def current_ofp(self):
        """The imported SimBrief plan if it is for the active job, else None."""
        jc = self._job_context()
        return self.ofp if jc and self.ofp and self.ofp.matches(jc[0]) else None

    def loadout_plan(self):
        jc = self._job_context()
        return plan_loadout(jc[0], jc[1], jc[2], self.current_ofp()) if jc else None

    def simbrief_link(self) -> str:
        """The SimBrief dispatch page prefilled for the active job."""
        jc = self._job_context()
        if jc is None:
            raise LoadoutError("Accept a job first.")
        job, atype, aircraft = jc
        pilot = self.db.pilot()
        units = "KGS" if self.settings.ui.units_weight == "kg" else "LBS"
        employer = get_employer(job.employer_id) if job.employer_id else None
        return simbrief.dispatch_url(job, atype, aircraft.registration if aircraft else "",
                                     pilot.callsign if pilot else "", f"sd{job.id}", units,
                                     employer.icao if employer else "",
                                     flight_number(job.origin, job.dest) if employer else "")

    def import_simbrief(self) -> None:
        """Fetch the newest SimBrief plan and keep it if it is for the active job."""
        jc = self._job_context()
        if jc is None:
            self.toast.emit("warn", "Accept a job first, then plan it on SimBrief.")
            return
        job, user = jc[0], self.settings.plan.simbrief_user

        def work():
            try:
                ofp = simbrief.fetch_latest(user, f"sd{job.id}")
            except simbrief.SimBriefError:
                ofp = simbrief.fetch_latest(user)              # the plan was made without our prefilled link
            if not ofp.matches(job):
                raise simbrief.SimBriefError(f"Your newest SimBrief plan is {ofp.origin} to {ofp.dest}, not this "
                                             f"flight ({job.origin} to {job.dest}). Generate the plan for this flight first.")
            return ofp

        def done(ofp):
            if self.closed:
                return
            self.ofp = ofp
            self.db.set_meta("simbrief_ofp", json.dumps(ofp.to_json()))
            self._synced_job = None                          # load the aircraft again with the new fuel
            msg = f"SimBrief plan imported: {ofp.origin} to {ofp.dest}, block fuel {ofp.block_fuel_lb:,.0f} lb."
            if ofp.age_hours() > simbrief.MAX_AGE_H:
                msg += " It is more than a day old."
            self.toast.emit("good", msg)
            self.plan_changed.emit()
        self.run_async(work, done, lambda e: None if self.closed else self.toast.emit("warn", e))

    def sync_loadout(self, auto: bool = False) -> None:
        """Put the plan's fuel and payload into the simulator aircraft, check that it stuck, and say what happened."""
        jc = self._job_context()
        plan = self.loadout_plan()
        if jc is None or plan is None:
            if not auto:
                self.toast.emit("warn", "Accept a job first.")
            return
        if self.career.has_flown():
            if not auto:
                self.toast.emit("warn", "The aircraft has already flown this flight, so it can't be loaded any more.")
            return
        prov = self.provider
        try:
            if prov is None:
                raise LoadoutError("The simulator is not connected.")
            fut = prov.apply_loadout(plan, jc[1])
        except LoadoutError as exc:
            log.info("Loadout not applied: %s", exc)
            if not auto:
                self.toast.emit("warn", str(exc))
            return
        job_id = jc[0].id
        log.info("Loading the sim aircraft for job %d: %.1f gal, %d pax, %.0f lb cargo (%s)", job_id, plan.fuel_gal,
                 plan.pax, plan.cargo_lb, plan.fuel_source)

        def done(res):
            if self.closed:
                return
            self._synced_job = job_id
            self.career.reset_fuel_baseline(res.fuel_gal)       # the recording may have begun with engines running
            note = " ".join(res.messages)
            good = res.fuel_ok and res.payload_ok
            self.toast.emit("good" if good else "warn",
                            (f"Aircraft loaded: {res.fuel_gal:.0f} gal fuel, {res.payload_lb:,.0f} lb payload. "
                             if good else "The sim only partly accepted the load. ") + note)
            self.plan_changed.emit()
            self.call_later(6000, lambda: self._verify_loadout(job_id))

        def failed(err: str):
            log.warning("Could not load the aircraft: %s", err)
            if not self.closed:
                self.toast.emit("warn", f"Could not load the aircraft: {err}")
        self.run_async(lambda: fut.result(timeout=30), done, failed)

    def _verify_loadout(self, job_id: int) -> None:
        """MSFS can reset the weights while a flight is still settling in. If what we loaded has been undone and the
        flight has not started, load it once or twice more."""
        job = self.career.active_job
        if self.closed or job is None or job.id != job_id or self.career.has_flown():
            return
        status = compare(self.loadout_plan(), self.provider.latest() if self.provider else None) \
            if self.loadout_plan() else None
        if status is None or status.matches or self._verify_tries.get(job_id, 0) >= 2:
            return
        self._verify_tries[job_id] = self._verify_tries.get(job_id, 0) + 1
        log.info("The sim aircraft no longer matches the plan (fuel %.1f vs %.1f); loading again", status.sim_fuel_gal,
                 status.plan_fuel_gal)
        self.sync_loadout(auto=True)

    def diagnose_loadout(self) -> None:
        """Read (never write) what the loadout relies on and show it, so a problem can be pinned down."""
        try:
            if self.provider is None:
                raise LoadoutError("The simulator is not connected.")
            fut = self.provider.diagnose_loadout()
        except LoadoutError as exc:
            self.loadout_report.emit(str(exc))
            return
        self.run_async(lambda: fut.result(timeout=30), lambda lines: self.loadout_report.emit("\n".join(lines)),
                  lambda e: self.loadout_report.emit(f"Could not read the sim: {e}"))

    def _maybe_auto_sync(self, state: SimState) -> None:
        """Once per job, when the right aircraft is parked and has settled, load fuel and payload. Say why if waiting."""
        if not self.settings.plan.auto_sync_loadout or not self.sim_connected:
            return
        job = self.career.active_job
        if job is None or self._synced_job == job.id or self.career.has_flown():
            self._stable_since = None
            return
        if not state.on_ground or state.gs > 3:
            self._stable_since = None                       # moving or airborne: nothing to say yet
            return
        jc = self._job_context()
        if jc is None:
            return
        plan = self.loadout_plan()
        problem = ready_problem(state, jc[1], plan)
        if problem:
            self._stable_since = None
            if problem != self._auto_problem:
                self._auto_problem = problem                  # say it once, not on every sample
                self.toast.emit("info", "Not loading the aircraft yet: " + problem)
            return
        self._auto_problem = ""
        if self._stable_since is None:
            self._stable_since = state.timestamp
        if state.timestamp - self._stable_since < 3.0:        # let MSFS finish setting the flight up first
            return
        self._synced_job = job.id                             # one automatic attempt per job; the button can repeat it
        self.sync_loadout(auto=True)

    def plan_summary(self) -> dict:
        """Everything the Flight page (desktop and web remote) shows about the plan and the loadout, as text."""
        jc = self._job_context()
        if jc is None:
            return {"has_job": False}
        job, atype, aircraft = jc
        s, ofp, plan = self.settings, self.current_ofp(), self.loadout_plan()
        status = compare(plan, self.provider.latest() if self.provider else None)
        out: dict = {"has_job": True, "user_set": bool(s.plan.simbrief_user.strip()), "auto": s.plan.auto_sync_loadout,
                     "ofp": None, "sim": None, "can_refuel": False, "flight": flight_label(job)}
        if ofp:
            pdf = ofp.pdf_url if ofp.pdf_url and simbrief.is_trusted_link(ofp.pdf_url) else ""
            out["ofp"] = {"route": f"{ofp.origin} {ofp.route} {ofp.dest}".strip(),
                          "altitude": f"{ofp.cruise_alt_ft:,} ft", "distance": fmt.dist(s, ofp.distance_nm),
                          "ete": fmt.duration(ofp.ete_min), "alternate": ofp.alternate or "-",
                          "block_fuel": f"{ofp.block_fuel_lb:,.0f} lb ({ofp.block_fuel_gal(atype):.0f} gal)",
                          "reserve_fuel": f"{ofp.reserve_fuel_lb:,.0f} lb", "pdf": pdf,
                          "stale": ofp.age_hours() > simbrief.MAX_AGE_H}
            out["can_refuel"] = aircraft is not None and ofp.block_fuel_gal(atype) > aircraft.fuel_gal + 0.5
        label = {"hangar": "the aircraft's fuel in your hangar", "simbrief": "the SimBrief block fuel",
                 "estimate": "an estimate (no SimBrief plan yet)"}[plan.fuel_source]
        out["loadout"] = {"fuel": f"{plan.fuel_gal:.0f} gal", "fuel_source": label,
                          "payload": f"{plan.pax} pax, {fmt.weight(s, plan.cargo_lb)} cargo "
                                     f"({fmt.weight(s, plan.payload_lb)})", "notes": plan.notes}
        if status:
            out["sim"] = {"fuel": f"{status.sim_fuel_gal:.0f} gal",
                          "payload": "unknown" if status.sim_payload_lb is None else fmt.weight(s, status.sim_payload_lb),
                          "matches": status.matches, "fuel_ok": status.fuel_ok, "payload_ok": status.payload_ok}
        return out

    def alerts(self) -> list[dict]:
        """Things the pilot should know about right now: [{'level': good|info|warn|bad, 'text': ...}]."""
        out: list[dict] = []
        pilot, cr, econ, s = self.db.pilot(), self.career.credentials, self.career.economy, self.settings
        if not pilot:
            return out
        cur = s.ui.currency
        grounded = cr.grounded_reason()
        if grounded:
            out.append({"level": "bad", "text": grounded})
        for st in cr.status():
            if st["valid"] and st["days_left"] is not None and st["days_left"] <= 14:
                out.append({"level": "warn", "text": f"Your {st['label'].lower()} expires in {st['days_left']} days "
                                                     f"(renewal {cur}{st['fee']:,.0f})."})
        active = cr.active_course()
        if active:
            out.append({"level": "info", "text": f"In training: {active[0].name}, finishes {active[1]:%d %b}."})
        due, total = econ.next_due(), econ.monthly_total()
        if due and total:
            days = (due - datetime.now(timezone.utc)).days
            if pilot.balance < total:
                out.append({"level": "warn", "text": f"Bills of {cur}{total:,.0f} fall due on {due:%d %b} and your balance "
                                                     f"is {cur}{pilot.balance:,.0f}."})
            elif days <= 7:
                out.append({"level": "info", "text": f"Bills of {cur}{total:,.0f} fall due on {due:%d %b}."})
        return out

    def training_summary(self) -> dict:
        """Licence, medical, ratings, courses and the monthly bills, as text for the Training page."""
        s, cr, econ = self.settings, self.career.credentials, self.career.economy
        pilot = self.db.pilot()
        cur = s.ui.currency
        money = lambda v: f"{cur}{v:,.0f}"
        active = cr.active_course()
        certs = [{"kind": st["kind"], "label": st["label"], "valid": st["valid"], "can_renew": st["can_renew"],
                  "expires": f"{st['expires']:%d %b %Y}" if st["expires"] else "-", "days_left": st["days_left"],
                  "fee": money(st["fee"]), "affordable": bool(pilot and pilot.balance >= st["fee"])}
                 for st in cr.status()]
        courses = []
        for c in COURSES:
            held = cr.has(c.id)
            fee = cr.course_fee(c)
            problem = None if held else cr.course_problem(c)
            courses.append({"id": c.id, "name": c.name, "blurb": c.blurb, "fee": money(fee),
                            "duration": f"{cr.course_days(c):g} days", "held": held,
                            "in_training": bool(active and active[0].id == c.id),
                            "finishes": f"{active[1]:%d %b}" if active and active[0].id == c.id else "",
                            "problem": problem, "affordable": bool(pilot and pilot.balance >= fee),
                            "can_start": not held and problem is None and bool(pilot and pilot.balance >= fee)})
        items = econ.monthly_items()
        total = sum(a for _, a, _ in items)
        due = econ.next_due()
        months = (pilot.balance / total) if pilot and total else 0.0
        return {"certs": certs, "courses": courses, "bills": [{"label": d, "amount": money(a)} for d, a, _ in items],
                "total": money(total), "next_due": f"{due:%d %b %Y}" if due else "-",
                "runway": f"Your balance covers about {max(0.0, months):.1f} months of bills." if total else "",
                "employers": [{"name": e.name, "needs": [r for r in required_ratings(e)]}
                              for e in (get_employer(x["employer_id"]) for x in self.db.employments()) if e]}

    def renew_credential(self, kind: str) -> None:
        try:
            fee = self.career.credentials.renew(kind)
        except CredentialError as exc:
            self.toast.emit("warn", str(exc))
            return
        self.toast.emit("good", f"Renewed for {self.settings.ui.currency}{fee:,.0f}.")
        self.career._fire("pilot_changed")
        self.career._fire("credentials_changed")

    def start_course(self, course_id: str) -> None:
        try:
            course = self.career.credentials.start_course(course_id)
        except CredentialError as exc:
            self.toast.emit("warn", str(exc))
            return
        self.toast.emit("good", f"Enrolled in the {course.name}. You'll be notified when you pass.")
        self.career._fire("pilot_changed")
        self.career._fire("credentials_changed")

    def refuel_to_plan(self) -> None:
        """Top the hangar aircraft up to the SimBrief block fuel (paying for it), then it can be loaded."""
        jc = self._job_context()
        ofp = self.current_ofp()
        if jc is None or jc[2] is None or ofp is None:
            self.toast.emit("warn", "Import a SimBrief plan for this flight first.")
            return
        job, atype, aircraft = jc
        need = ofp.block_fuel_gal(atype) - aircraft.fuel_gal
        if need <= 0.5:
            self.toast.emit("info", "The aircraft already has the planned fuel.")
            return
        cost = self.career.hangar.refuel(aircraft.id, need)
        self.career._fire("hangar_changed")
        self.career._fire("pilot_changed")
        self._synced_job = None
        self.toast.emit("info", f"Refuelled {need:.0f} gal for {self.settings.ui.currency}{cost:,.0f}.")
        self.plan_changed.emit()

    # ----------------------------------------------------------- web remote
    def start_web(self) -> None:
        """Serve the browser remote (a Mac/tablet/phone UI) when it is switched on in Settings."""
        self.stop_web()
        r = self.settings.remote
        if not r.enabled:
            return
        if not r.token:
            r.token = secrets.token_urlsafe(9)
            self.settings.save()
        from ..web.server import WebRemote
        if self.web is None:
            self.web = WebRemote(self)
        self.web.host, self.web.port, self.web.token = "0.0.0.0", r.port, r.token
        try:
            self.web.start()
        except OSError as e:
            self.web_error = f"Could not start the browser remote on port {r.port}: {e}"
            log.warning(self.web_error)
            self.toast.emit("warn", self.web_error)
            return
        self.web_error = ""
        self.advertiser.start(r.port)

    def stop_web(self) -> None:
        self.advertiser.stop()
        if self.web is not None and self.web.running:
            self.web.stop()

    # -------------------------------------------------------------- updates
    def check_for_updates(self, manual: bool = False) -> None:
        """Look for a newer GitHub release. Automatic checks stay quiet unless something newer exists."""
        if not manual and (not self.settings.ui.check_updates or os.environ.get("SKYDISPATCH_NO_UPDATE_CHECK")):
            return

        def done(info):
            if self.closed:
                return
            self.update_info = info
            if info:
                self.update_available.emit(info)
                self.settings_changed.emit()
            elif manual:
                self.toast.emit("good", f"You're up to date (version {__version__}).")

        def failed(err: str):
            log.info("update check failed: %s", err)
            if manual and not self.closed:
                self.toast.emit("warn", err if "GitHub" in err else "Could not check for updates. Are you online?")
        self.run_async(updater.check_for_update, done, failed)

    def _on_status(self, status: str, message: str) -> None:
        self._to_main(lambda: self._handle_status(status, message))

    def _handle_status(self, status: str, message: str) -> None:
        if self.share_host:
            self.share_host.publish_status(status, message)
        self.sim_connected = status == "connected"
        self.sim_status.emit(status, message)
        if status == "connected" and self.provider and self.provider.installed is not None \
                and self.settings.sim.installed_auto:
            self._store_installed(set(self.provider.installed), "your simulator PC")

    def _feed(self, state: SimState) -> None:
        if self.closed:
            return
        self.career.feed(state)
        self._maybe_auto_sync(state)
        try:
            for c in self.copilot.callouts(state, time.time()):
                self.copilot.record_callout(c.text)
                self.speak(c.text, "copilot")
        except Exception:
            log.exception("copilot callouts failed")

    @property
    def simulated(self) -> SimulatedProvider | None:
        return self.provider if isinstance(self.provider, SimulatedProvider) else None

    # --------------------------------------------------- installed aircraft
    def detect_installed(self, force: bool = False) -> None:
        """Scan this computer for MSFS aircraft (a bridge reports its own list when it connects)."""
        path = self.settings.sim.packages_path
        self.run_async(lambda: detect_installed(path),
                  lambda found: self._detected(found, force),
                  lambda e: log.warning("aircraft detection failed: %s", e))

    def _detected(self, found: set[str] | None, force: bool) -> None:
        if self.closed:
            return
        if found is None:
            if force:
                self.toast.emit("warn", "No MSFS packages folder found. Looked in: "
                                        f"{searched_locations(self.settings.sim.packages_path)}. Set your MSFS "
                                        "packages folder in Settings > Simulator, or choose aircraft manually.")
            return
        if not found and force:
            self.toast.emit("warn", "Found your MSFS folder but no supported aircraft in it. Check that the packages "
                                    "folder is the one containing Community and Official, or choose manually.")
        self._store_installed(found, "this computer")

    def _store_installed(self, found: set[str], where: str) -> None:
        text = format_ids(found)
        if text != self.settings.sim.installed_aircraft:
            self.settings.sim.installed_aircraft = text
            self.settings.save()
            self.toast.emit("info", f"Found {len(found)} supported aircraft installed on {where}.")
            self.settings_changed.emit()
            self.career_event.emit("market_changed", {})

    def demo_fly_active_job(self) -> str | None:
        """In simulated mode: place the aircraft at the departure airport and fly the active job."""
        sim = self.simulated
        job = self.career.active_job or self.db.active_job()
        if sim is None or job is None:
            return "Accept a job first (and use Simulated mode)."
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        o, d = self.db.airport(job.origin), self.db.airport(job.dest)
        from ..data.aircraft import get_type
        t = get_type(job.provided_type) if job.employer_id else (get_type(aircraft.type_id) if aircraft else None)
        if not (o and d and t):
            return "Job data is incomplete."
        fuel = t.fuel_cap_gal if job.employer_id else (aircraft.fuel_gal if aircraft else t.fuel_cap_gal)
        sim.configure_aircraft(f"{t.name} (Simulated)", t.cruise_kts * 0.9, 6500 if t.cruise_kts < 250 else 24000,
                               t.fuel_gph, fuel)
        sim.set_position(o.lat, o.lon, fuel_gal=fuel, elev_ft=o.elevation_ft)
        sim.fly(d.lat, d.lon, d.elevation_ft)
        return None

    # ------------------------------------------------------- career -> UI/AI
    def _on_career_event(self, name: str, payload: dict) -> None:
        if self.closed:
            return
        self._to_main(lambda: self.career_event.emit(name, payload))    # may arrive from any thread

    def _thread_for_job(self, job) -> str:
        return employer_thread(job.employer_id) if job is not None and job.employer_id else "general"

    def _react(self, name: str, payload: dict) -> None:
        if self.closed:
            return
        if name == "thread_changed":
            self.thread_changed.emit(payload.get("thread", "general"))
        elif name == "job_accepted":
            job = payload["job"]
            thread = self._thread_for_job(job)
            self._ai_task(lambda: self.dispatcher.brief_job(job), thread, kind="brief")
        elif name == "flight_event":
            kind = payload["kind"]
            job = payload.get("job")
            thread = self._thread_for_job(job)
            if EVENT_IMPORTANCE.get(kind, 0) >= 1 and self.settings.ai.proactive_comms:
                self._ai_task(lambda: self.dispatcher.react(kind, payload["detail"], payload.get("data"), thread),
                              thread, kind="react", important=EVENT_IMPORTANCE.get(kind, 0) >= 2)
        elif name == "flight_finished":
            s: Settlement = payload["settlement"]
            thread = employer_thread(s.employer_id) if s.employer_id else "general"

            def work():
                text = self.dispatcher.debrief(s)
                self.db.finish_flight(s.flight_id, debrief=text)
                return text

            def after(text):
                if s.employer_id and s.metrics.outcome != "crashed" and self.db.is_employed_by(s.employer_id):
                    self.dispatcher.ask_availability(thread, "Ready for another one?")
            self._ai_task(work, thread, kind="debrief", then=after)
            kind = "good" if s.metrics.outcome == "completed" else "bad"
            self.toast.emit(kind, f"Flight {s.metrics.outcome}: score {s.score.score:.0f}, "
                                  f"payout {self.settings.ui.currency}{s.payout:,.0f}")
        elif name == "hired":
            employer = payload["employer"]
            self.toast.emit("good", f"You're hired at {employer.name}! Check your messenger.")
        elif name == "vacancy_opened":
            self._vacancy_opened(payload["employer_id"])
        elif name == "training_done":
            c = payload["course"]
            self._ops_note(f"Congratulations, Captain: you've passed the {c.name}. It's on your licence now and counts "
                           "for any company that asks for it.", "good")
        elif name == "bills_charged":
            total = sum(a for _, a in payload["items"])
            pilot = self.db.pilot()
            msg = f"Monthly bills paid: {self.settings.ui.currency}{total:,.0f}."
            if pilot and pilot.balance < 0:
                self._ops_note(msg + f" Your balance is now {self.settings.ui.currency}{pilot.balance:,.0f}. Fly some "
                               "paid flights to get back in the black.", "bad")
            else:
                self.toast.emit("info", msg)
        elif name == "credential_expiring":
            label = payload["label"].lower()
            if payload["state"] == "expired":
                self._ops_note(f"Your {label} has expired, so you can't take contracts or be rostered until you renew it "
                               "in Training.", "bad")
            else:
                self._ops_note(f"Your {label} expires in {payload['days_left']} days. You can renew it in Training.", "warn")

    def _ops_note(self, text: str, level: str = "info") -> None:
        """A note from your operations desk: kept in the general conversation and shown as a notification."""
        self.db.add_message("assistant", text, "general")
        self.thread_changed.emit("general")
        self.toast.emit(level, text)

    def _vacancy_opened(self, employer_id: str) -> None:
        """Tell the pilot when a company they could actually join starts recruiting (others are visible on the board)."""
        employer = get_employer(employer_id)
        if employer is None or self.db.is_employed_by(employer_id) or self.career.employer_fleet_note(employer):
            return
        if not quals_mod.meets(employer, self.career.qualifications()):
            return
        until = self.career.hiring.state(employer_id)["until"]
        self._ops_note(f"{employer.name} is recruiting pilots until {until:%d %b}, and you meet their requirements. "
                       "Apply on the Job Board.", "info")

    def _ai_task(self, fn, thread: str, kind: str, important: bool = True, then=None) -> None:
        def done(text):
            if self.closed:
                return
            if text:
                self.dispatcher.say(text, thread)
                if important or kind != "react":
                    self.speak(text, thread)
            if then:
                then(text)
        self.run_async(fn, done, lambda e: log.warning("AI task %s failed: %s", kind, e), ordered=True)

    # ------------------------------------------------------------- employers
    def apply_to_employer(self, employer_id: str) -> ApplicationResult:
        """Apply (instant decision). A hire opens the company's messenger thread in the background."""
        result = self.career.apply_to_employer(employer_id)
        employer = result.employer
        if result.accepted:
            def work():
                hr = self.dispatcher.hr_reply(employer, True, result.message)
                self.dispatcher.start_thread(employer, hr)
                return hr

            def opened(hr):
                if self.closed:
                    return
                thread = employer_thread(employer.id)
                self.thread_changed.emit(thread)
                self.speak(hr, thread, voice=HR_VOICE, speed=1.0)     # HR is a different person from the dispatcher
            self.run_async(work, opened, lambda e: log.warning("could not open thread: %s", e))
        return result

    def resign(self, employer_id: str) -> None:
        self.career.resign(employer_id)

    # ------------------------------------------------------------- messenger
    def ask(self, text: str, thread: str = "general") -> None:
        text = text.strip()
        if not text:
            return
        self.db.add_message("user", text, thread)
        self.thread_changed.emit(thread)
        self._busy(thread, +1)

        def done(_reply):
            self._busy(thread, -1)
            if self.closed:
                return
            self.ai_status.emit(True, "AI dispatcher online")
            last = self.db.last_message(thread)
            if last and last["role"] == "assistant":
                self.speak(last["content"], thread)

        def failed(err: str):
            self._busy(thread, -1)
            if self.closed:
                return
            self.db.add_message("system", err, thread)
            self.thread_changed.emit(thread)
            self.ai_status.emit(False, err)

        self.run_async(lambda: self.dispatcher.chat(text, thread, add_user=False), done, failed)

    def answer_availability(self, thread: str, minutes: int) -> None:
        """Quick-answer chips: tell the dispatcher how long you have."""
        label = f"{minutes} minutes" if minutes < 60 else f"{minutes / 60:g} hours"
        self.db.add_message("user", f"I've got about {label}.", thread)
        self.thread_changed.emit(thread)
        self._busy(thread, +1)

        def done(_r):
            self._busy(thread, -1)
            if self.closed:
                return
            last = self.db.messages(40, thread)
            intro = next((m for m in reversed(last) if m["role"] == "assistant" and m["kind"] == "text"), None)
            if intro:
                self.speak(intro["content"], thread)
        self.run_async(lambda: self.dispatcher.handle_availability(thread, minutes), done,
                  lambda e: (self._busy(thread, -1), log.warning("availability failed: %s", e)))

    def accept_offer(self, job_id: int, thread: str) -> None:
        try:
            self.dispatcher.accept_offer(job_id, thread)
        except CareerError as exc:
            self.toast.emit("bad", str(exc))
            return
        self.career_event.emit("market_changed", {})

    def decline_offer(self, job_id: int, thread: str) -> None:
        try:
            self.dispatcher.decline_offer(job_id, thread)
        except CareerError as exc:
            self.toast.emit("bad", str(exc))

    def open_thread(self, thread: str) -> None:
        """Make sure an employer thread has its opening messages (and the availability question)."""
        employer = thread_employer(thread)
        if employer and self.db.is_employed_by(employer.id) and self.db.last_message(thread) is None:
            self.dispatcher.start_thread(employer)
        elif employer and self.db.is_employed_by(employer.id) and not self.db.active_job() \
                and not self.db.jobs("offered", scope=employer.id) and not self.dispatcher.is_awaiting_availability(thread):
            self.dispatcher.ask_availability(thread)

    def _busy(self, thread: str, delta: int) -> None:
        n = self._pending.get(thread, 0) + delta
        self._pending[thread] = max(0, n)
        self.chat_busy.emit(thread, n > 0)

    # --------------------------------------------------------------- copilot
    def ask_copilot(self, text: str | None = None, quick: str | None = None) -> None:
        label = text or quick or ""
        if not label:
            return
        self._busy("copilot", +1)

        def done(reply):
            self._busy("copilot", -1)
            if self.closed or not reply:
                return
            self.speak(reply, "copilot")

        def failed(err):
            self._busy("copilot", -1)
            log.warning("copilot failed: %s", err)

        if quick:
            self.run_async(lambda: self.copilot.say_quick(quick), done, failed)
        else:
            self.db.add_message("user", text, "copilot")
            self.thread_changed.emit("copilot")
            self.run_async(lambda: self.copilot.ask(text, add_user=False), done, failed)

    def refresh_market(self) -> None:
        def work():
            n = self.career.refresh_market()
            try:
                self.dispatcher.enhance_jobs(self.db.jobs("offered")[:8])   # newest first
            except LLMError:
                pass
            return n
        self.run_async(work, lambda n: None if self.closed else (
                      self.toast.emit("info", f"{n} new contract(s) on the board"),
                      self.career_event.emit("market_changed", {})),
                  lambda e: None if self.closed else self.toast.emit("bad", f"Could not refresh: {e}"))

    def check_ai(self) -> None:
        self.run_async(self.dispatcher.test_connection,
                  lambda r: None if self.closed else self.ai_status.emit(bool(r[0]), r[1]),
                  lambda e: None if self.closed else self.ai_status.emit(False, e))

    # ---------------------------------------------------------------- timers
    def _tick(self) -> None:
        if self.closed:
            return
        try:
            self.career.flush_telemetry()
            self.career.housekeeping()
            self.db.expire_jobs()
            if self.db.pilot() and len(self.db.jobs("offered")) < max(3, self.settings.game.job_count // 3):
                self.career.refresh_market()
        except Exception:
            log.exception("periodic tick failed")

    # ------------------------------------------------------------- new career
    def career_options(self) -> dict:
        """What a new pilot can choose from (the phone's setup screen)."""
        from ..data.aircraft import STARTER_IDS, get_type
        from ..pilot.quals import EXPERIENCE_PRESETS
        from ..sim.installed import installed_types
        s = self.settings
        installed = installed_types(s, self.db)
        starters = [{"id": tid, "name": get_type(tid).name, "installed": installed is None or tid in installed}
                    for tid in STARTER_IDS if get_type(tid)]
        return {"starters": starters,
                "experience": [{"id": k, "label": v[0]} for k, v in EXPERIENCE_PRESETS.items()],
                "difficulty": ["relaxed", "normal", "realistic"], "currencies": list(CURRENCIES),
                "defaults": {"name": s.pilot.name if s.pilot.name != "Captain" else "", "callsign": s.pilot.callsign,
                             "home": s.pilot.home_icao, "balance": s.game.start_balance,
                             "difficulty": s.game.difficulty, "currency": s.ui.currency,
                             "experience": "new", "aircraft": starters[1]["id"] if len(starters) > 1 else ""}}

    def create_career(self, name: str, home: str, aircraft: str, callsign: str = "", experience: str = "new",
                      difficulty: str = "normal", balance: float | None = None, currency: str | None = None) -> None:
        """Start a new career (replacing any existing one). Raises CareerError for choices that are not allowed."""
        from ..data.aircraft import STARTER_IDS
        from ..pilot.quals import EXPERIENCE_PRESETS, apply_experience_preset
        from ..sim.installed import installed_types
        s = self.settings
        name = " ".join(str(name).split())[:40]
        home = str(home).strip().upper()
        if not name:
            raise CareerError("Enter your pilot name.")
        if not self.db.airport(home):
            raise CareerError(f"I don't know an airport called '{home}'. Use its ICAO code, for example EGLL or KSEA.")
        if aircraft not in STARTER_IDS:
            raise CareerError("Choose one of the starter aircraft.")
        installed = installed_types(s, self.db)
        if installed is not None and any(t in installed for t in STARTER_IDS) and aircraft not in installed:
            raise CareerError("That aircraft is not installed in your simulator.")
        if experience not in EXPERIENCE_PRESETS:
            raise CareerError("Choose your flying experience.")
        if difficulty not in ("relaxed", "normal", "realistic"):
            raise CareerError("Choose a difficulty.")
        if currency is not None and currency not in CURRENCIES:
            raise CareerError("Choose a currency.")
        amount = s.game.start_balance if balance is None else float(balance)
        if not 0 <= amount <= 5_000_000:
            raise CareerError("The starting balance must be between 0 and 5,000,000.")
        s.pilot.name = name
        s.pilot.callsign = "".join(ch for ch in str(callsign).upper() if ch.isalnum())[:8] or "SKY1"
        s.pilot.home_icao = home
        if currency:
            s.ui.currency = currency
        s.game.difficulty, s.game.start_balance = difficulty, amount
        s.ui.first_run_complete = True
        self.career.start_career(s.pilot.name, s.pilot.callsign, home, aircraft, amount)
        apply_experience_preset(self.db, experience)
        self.db.clear_messages()
        self.apply_settings()
        if self.provider is None:
            self.start_sim()
        self.check_ai()

    # --------------------------------------------------------------- pairing
    def pairing_info(self, new_code: bool = False) -> dict:
        """What the admin window shows to pair a phone: the address, a one-time code and the QR text."""
        if new_code or not self.pairing.code_state()[0]:
            self.pairing.new_code()
        code, left = self.pairing.code_state()
        port = self.settings.remote.port
        hosts = lan_addresses()
        return {"running": bool(self.web and self.web.running), "port": port, "addresses": hosts,
                "code": format_code(code), "expires_in": int(left),
                "uri": pair_uri(hosts[0], port, code) if hosts else "",
                "urls": [f"http://{h}:{port}/" for h in hosts]}
