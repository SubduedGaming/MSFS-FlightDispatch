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


@pytest.fixture(autouse=True)
def collect_garbage_on_the_main_thread():
    """Qt objects (the app context and its timers) sit in reference cycles. If Python's cyclic collector happens to run on
    another thread (an HTTP client thread in the web tests) it destroys them there, which segfaults. Collect here."""
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
