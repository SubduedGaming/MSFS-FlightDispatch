import dataclasses
import gc
import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from skydispatch.career import Career
from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.sim.simulated import SimulatedProvider


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYDISPATCH_HOME", str(tmp_path / "home"))
    # never pick up a real MSFS install on the machine running the tests
    monkeypatch.setattr("skydispatch.sim.installed.default_roots", lambda: [])
    monkeypatch.setattr("skydispatch.sim.installed.userconfig_candidates", lambda: [])


@pytest.fixture(scope="session", autouse=True)
def no_automatic_garbage_collection():
    """Qt objects (the app context, its timers, the main-thread poster) sit in reference cycles. Python's automatic cyclic
    collector can run on whichever thread happens to allocate, for example an HTTP client thread in the web tests, and
    destroying a QObject there crashes or hangs. So collect only when we say so, on the main thread."""
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def collect_garbage_on_the_main_thread():
    yield
    gc.collect()


@pytest.fixture
def db():
    d = Database()
    yield d
    d.close()


@pytest.fixture
def career(db):
    c = Career(db, Settings())
    c.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000)
    c.jobs.rng = random.Random(1)
    return c


def run_flight(career, provider: SimulatedProvider, dest_icao: str, step: float = 2.0, max_steps=20000):
    """Drive the simulated aircraft synchronously (no threads) and feed the career."""
    dest = career.db.airport(dest_icao)
    provider.fly(dest.lat, dest.lon, dest.elevation_ft)
    clock = 1000.0
    for _ in range(max_steps):
        provider._step(step)
        clock += step / provider.speed
        snap = dataclasses.replace(provider._state)
        snap.timestamp = clock
        snap.sim_time_scale = provider.speed
        career.feed(snap)
        if career.recorder is None or (career.recorder.finished):
            if not provider.flying or career.last_settlement:
                break
    return career.last_settlement
