"""AppContext: owns the long-lived services and bridges them to Qt signals."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..ai.dispatcher import Dispatcher, EVENT_IMPORTANCE
from ..ai.llm import LLMError
from ..career import Career, Settlement
from ..core.config import Settings
from ..core.paths import database_path
from ..db.database import Database
from ..sim.base import SimProvider, SimState
from ..sim.factory import make_provider
from ..sim.simulated import SimulatedProvider
from ..voice.service import VoiceService
from ..ai.personas import get_persona
from .workers import run_async

log = logging.getLogger(__name__)


class AppContext(QObject):
    sim_state = Signal(object)
    sim_status = Signal(str, str)
    career_event = Signal(str, dict)
    chat = Signal(str, str)               # role, text
    chat_busy = Signal(bool)
    ai_status = Signal(bool, str)
    toast = Signal(str, str)              # level (info/good/warn/bad), message
    listening = Signal(bool)
    settings_changed = Signal()

    def __init__(self, settings: Settings, db: Database):
        super().__init__()
        self.settings = settings
        self.db = db
        self.career = Career(db, settings)
        self.dispatcher = Dispatcher(self.career, settings)
        self.voice = VoiceService(settings, get_persona(settings.ai.persona).system_voice_hint)
        self.provider: SimProvider | None = None
        self.sim_connected = False
        self.closed = False
        self._chat_pending = 0
        self.career.subscribe(self._on_career_event)
        self.sim_state.connect(self._feed)
        self.career_event.connect(self._react)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30_000)

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, db_path: Path | None = None) -> "AppContext":
        settings = Settings.load()
        db = Database(db_path or database_path())
        ctx = cls(settings, db)
        ctx.career.resume()
        return ctx

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._timer.stop()
        self.stop_sim()
        self.career.flush_telemetry()
        self.voice.shutdown()
        self.settings.save()
        self.db.close()

    def apply_settings(self) -> None:
        """Call after the user edits settings: persist and re-wire services."""
        self.settings.save()
        self.dispatcher.client.cfg = self.settings.ai
        self.dispatcher._prompt_tools = self.settings.ai.tool_mode == "prompt"
        self.voice.tts.voice_hint = get_persona(self.settings.ai.persona).system_voice_hint
        self.settings_changed.emit()

    # ------------------------------------------------------------------ sim
    def start_sim(self) -> None:
        self.stop_sim()
        self.provider = make_provider(self.settings.sim)
        self.provider.on_state = lambda s: self.sim_state.emit(s)
        self.provider.on_status = self._on_status
        self.provider.start()

    def stop_sim(self) -> None:
        if self.provider:
            self.provider.on_state = None
            self.provider.on_status = None
            self.provider.stop()
            self.provider = None
        self.sim_connected = False

    def _on_status(self, status: str, message: str) -> None:
        self.sim_connected = status == "connected"
        self.sim_status.emit(status, message)

    def _feed(self, state: SimState) -> None:
        self.career.feed(state)

    @property
    def simulated(self) -> SimulatedProvider | None:
        return self.provider if isinstance(self.provider, SimulatedProvider) else None

    def demo_fly_active_job(self) -> str | None:
        """In simulated mode: place the aircraft at the departure airport and fly the active job."""
        sim = self.simulated
        job = self.career.active_job or self.db.active_job()
        if sim is None or job is None:
            return "Accept a job first (and use Simulated mode)."
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        o, d = self.db.airport(job.origin), self.db.airport(job.dest)
        if not (o and d and aircraft):
            return "Job data is incomplete."
        from ..data.aircraft import get_type
        t = get_type(aircraft.type_id)
        if t is None:
            return "Unknown aircraft type."
        sim.configure_aircraft(f"{t.name} (Simulated)", t.cruise_kts * 0.9, 6500 if t.cruise_kts < 250 else 24000,
                               t.fuel_gph, aircraft.fuel_gal)
        sim.set_position(o.lat, o.lon, fuel_gal=aircraft.fuel_gal, elev_ft=o.elevation_ft)
        sim.fly(d.lat, d.lon, d.elevation_ft)
        return None

    # ------------------------------------------------------- career -> UI/AI
    def _on_career_event(self, name: str, payload: dict) -> None:
        if self.closed:
            return
        self.career_event.emit(name, payload)    # may arrive from any thread; Qt queues it

    def _react(self, name: str, payload: dict) -> None:
        if name == "job_accepted":
            job = payload["job"]
            self._ai_task(lambda: self.dispatcher.brief_job(job), kind="brief")
        elif name == "flight_event":
            kind = payload["kind"]
            if EVENT_IMPORTANCE.get(kind, 0) >= 1 and self.settings.ai.proactive_comms:
                self._ai_task(lambda: self.dispatcher.react(kind, payload["detail"], payload.get("data")),
                              kind="react", important=EVENT_IMPORTANCE.get(kind, 0) >= 2)
        elif name == "flight_finished":
            s: Settlement = payload["settlement"]
            def work():
                text = self.dispatcher.debrief(s)
                self.db.finish_flight(s.flight_id, debrief=text)
                return text
            self._ai_task(work, kind="debrief")
            kind = "good" if s.metrics.outcome == "completed" else "bad"
            self.toast.emit(kind, f"Flight {s.metrics.outcome}: score {s.score.score:.0f}, "
                                  f"payout {self.settings.ui.currency}{s.payout:,.0f}")

    def _ai_task(self, fn, kind: str, important: bool = True) -> None:
        def done(text):
            if text:
                self.db.add_message("assistant", text)
                self.chat.emit("assistant", text)
                if self.settings.voice.auto_speak_replies and (important or kind != "react"):
                    self.voice.say(text)
        run_async(fn, done, lambda e: log.warning("AI task %s failed: %s", kind, e), owner=self)

    # ------------------------------------------------------------- dispatcher
    def ask(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.chat.emit("user", text)
        self._chat_pending += 1
        self.chat_busy.emit(True)

        def done(reply: str):
            self._chat_pending -= 1
            self.chat_busy.emit(self._chat_pending > 0)
            self.chat.emit("assistant", reply)
            self.ai_status.emit(True, "AI dispatcher online")
            if self.settings.voice.auto_speak_replies:
                self.voice.say(reply)

        def failed(err: str):
            self._chat_pending -= 1
            self.chat_busy.emit(self._chat_pending > 0)
            self.chat.emit("system", err)
            self.ai_status.emit(False, err)

        run_async(lambda: self.dispatcher.chat(text), done, failed, owner=self)

    def refresh_market(self) -> None:
        def work():
            n = self.career.refresh_market()
            try:
                self.dispatcher.enhance_jobs(self.db.jobs("offered")[:8])   # newest first
            except LLMError:
                pass
            return n
        run_async(work, lambda n: (self.toast.emit("info", f"{n} new contract(s) on the board"),
                                   self.career_event.emit("market_changed", {})),
                  lambda e: self.toast.emit("bad", f"Could not refresh: {e}"), owner=self)

    def check_ai(self) -> None:
        run_async(self.dispatcher.test_connection,
                  lambda r: self.ai_status.emit(bool(r[0]), r[1]),
                  lambda e: self.ai_status.emit(False, e), owner=self)

    # ---------------------------------------------------------------- timers
    def _tick(self) -> None:
        if self.closed:
            return
        try:
            self.career.flush_telemetry()
            self.db.expire_jobs()
            if self.db.pilot() and len(self.db.jobs("offered")) < max(3, self.settings.game.job_count // 3):
                self.career.refresh_market()
        except Exception:
            log.exception("periodic tick failed")
