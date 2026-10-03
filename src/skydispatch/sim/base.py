"""Simulator abstraction: every source (MSFS, bridge, demo) yields `SimState`."""
from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import asdict, dataclass, fields
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class SimState:
    """One telemetry sample. Units: feet, knots, feet/min, gallons, degrees."""
    timestamp: float = 0.0            # wall-clock seconds
    lat: float = 0.0
    lon: float = 0.0
    alt_msl: float = 0.0
    alt_agl: float = 0.0
    ias: float = 0.0
    gs: float = 0.0
    heading: float = 0.0
    vs: float = 0.0
    pitch: float = 0.0
    bank: float = 0.0
    g_force: float = 1.0
    on_ground: bool = True
    engine_running: bool = False
    parking_brake: bool = True
    gear_down: bool = True
    flaps: float = 0.0
    fuel_gal: float = 0.0
    fuel_flow_gph: float = 0.0
    sim_paused: bool = False
    sim_rate: float = 1.0
    crashed: bool = False
    touchdown_fpm: float | None = None   # sim-reported touchdown velocity if available
    title: str = ""
    sim_time_scale: float = 1.0          # simulated seconds elapsed per wall second (demo mode)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SimState":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


StateCallback = Callable[[SimState], None]
StatusCallback = Callable[[str, str], None]   # (status, message) status: connected|connecting|disconnected|error


class SimProvider(abc.ABC):
    """Runs its own thread; calls `on_state` ~sample_hz times a second while connected."""

    name = "base"

    def __init__(self, sample_hz: float = 2.0):
        self.sample_hz = max(0.2, sample_hz)
        self.on_state: StateCallback | None = None
        self.on_status: StatusCallback | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest: SimState | None = None
        self.status = "disconnected"
        self.installed: list[str] | None = None   # catalog ids installed in the sim, when the provider knows

    # -- public ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_safe, name=f"sim-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._set_status("disconnected", "Stopped")

    def latest(self) -> SimState | None:
        return self._latest

    # -- for subclasses ---------------------------------------------------
    @abc.abstractmethod
    def _run(self) -> None: ...

    def _emit(self, state: SimState) -> None:
        self._latest = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:  # never let a UI bug kill the sim thread
                log.exception("on_state callback failed")

    def _set_status(self, status: str, message: str = "") -> None:
        if status != self.status:
            log.info("Sim %s: %s %s", self.name, status, message)
        self.status = status
        if self.on_status:
            try:
                self.on_status(status, message)
            except Exception:
                log.exception("on_status callback failed")

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _run_safe(self) -> None:
        try:
            self._run()
        except Exception as exc:
            log.exception("Sim provider crashed")
            self._set_status("error", str(exc))


def make_state_time() -> float:
    return time.time()
