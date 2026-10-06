import pytest
import dataclasses

from skydispatch.data.aircraft import get_type, match_title
from skydispatch.db.models import Airport
from skydispatch.flight.recorder import FlightRecorder
from skydispatch.flight.scoring import FlightMetrics, score_flight
from skydispatch.sim.base import SimState


def test_scoring_perfect_and_penalties():
    perfect = score_flight(FlightMetrics(landing_fpm=-120, max_g=1.2, air_min=30, distance_nm=100))
    assert perfect.score == 100 and perfect.payout_mult == 1.1 and perfect.grade == "A"
    rough = score_flight(FlightMetrics(landing_fpm=-550, max_g=2.4, overspeed_s=30, air_min=30))
    assert rough.score < 50 and rough.payout_mult < 1 and rough.penalties
    assert score_flight(FlightMetrics(outcome="crashed")).payout_mult == 0
    assert score_flight(FlightMetrics(outcome="diverted")).payout_mult == 0


def test_difficulty_changes_strictness():
    m = FlightMetrics(landing_fpm=-350, max_g=1.2)
    assert score_flight(m, "relaxed").score > score_flight(m, "normal").score > score_flight(m, "realistic").score


def test_late_and_slew_penalties():
    late = score_flight(FlightMetrics(landing_fpm=-100, deadline_min=60, on_time=False, minutes_late=20))
    assert late.score < 100
    cheat = score_flight(FlightMetrics(landing_fpm=-100, slews=1))
    assert cheat.payout_mult <= 0.55


def test_match_title():
    assert match_title("Cessna 172 Skyhawk G1000 NXi").id == "c172"
    assert match_title("Airbus A320neo FlyByWire").id == "a320"
    assert match_title("Boeing 747-8i").id == "b748"
    assert match_title("Some Random Addon") is None


# ---- recorder edge cases (hand-built samples) --------------------------------
def apt(icao, lat, lon):
    return Airport(icao, icao, "", "", lat, lon, 0, 5000, "M")


class Feeder:
    def __init__(self, rec, **base):
        self.rec, self.t = rec, 0.0
        self.s = SimState(timestamp=0, lat=51.0, lon=0.0, engine_running=True, parking_brake=False, gs=10,
                          ias=10, fuel_gal=40, title="Cessna 172 Skyhawk", **base)

    def step(self, dt=1.0, **kw):
        self.t += dt
        self.s = dataclasses.replace(self.s, timestamp=self.t, **kw)
        self.rec.feed(self.s)


def test_overspeed_bounce_and_low_fuel_events():
    events = []
    rec = FlightRecorder(apt("AAAA", 51.0, 0.0), apt("BBBB", 51.0, 0.5), get_type("c172"), 0,
                         on_event=lambda k, d, data: events.append(k))
    f = Feeder(rec)
    f.step()
    f.step(gs=60, ias=60)
    f.step(on_ground=False, ias=70, gs=70, vs=600, alt_msl=300, alt_agl=300)
    for _ in range(40):
        f.step(on_ground=False, ias=170, gs=170, vs=0, alt_msl=3000, alt_agl=3000, fuel_gal=3)  # > Vne 163, <10% fuel
    assert "overspeed" in events and "low_fuel" in events
    assert rec.overspeed_s > 30
    # land, bounce, land again
    f.step(on_ground=True, ias=60, gs=60, vs=-300, touchdown_fpm=None, g_force=1.4)
    f.step(on_ground=False, ias=60, gs=60, vs=200, alt_msl=10, alt_agl=10)
    for _ in range(30):
        f.step(on_ground=False, ias=60, gs=60, vs=-100, alt_msl=20, alt_agl=20)
    f.step(on_ground=True, ias=55, gs=55, vs=-120, g_force=1.2)
    assert rec.bounces == 1
    assert rec.landing_fpm is not None and abs(rec.landing_fpm) < 200
    m = rec.metrics()
    assert m.bounces == 1 and m.low_fuel


def test_crash_ends_flight():
    events = []
    rec = FlightRecorder(None, None, get_type("c172"), 0, on_event=lambda k, d, data: events.append(k))
    f = Feeder(rec)
    f.step()
    f.step(crashed=True)
    assert rec.finished and rec.outcome == "crashed" and "crash" in events


def test_paused_sim_does_not_advance_clock():
    rec = FlightRecorder(None, None, get_type("c172"), 0)
    f = Feeder(rec)
    f.step()
    f.step(dt=1, gs=50, ias=50)
    t0 = rec.t
    for _ in range(20):
        f.step(dt=5, sim_paused=True)
    assert rec.t == t0


def test_short_hop_is_not_a_landing():
    rec = FlightRecorder(None, None, get_type("c172"), 0)
    f = Feeder(rec)
    f.step()
    f.step(gs=60, ias=60)
    f.step(on_ground=False, ias=65, gs=65, vs=300, alt_msl=20, alt_agl=20)
    f.step(on_ground=True, ias=60, gs=60, vs=-100)
    assert rec.landings == 0


def _land_at(rec, f, lat, lon):
    f.step()
    f.step(gs=60, ias=60)
    f.step(on_ground=False, ias=70, gs=70, vs=600, alt_msl=300, alt_agl=300)
    for _ in range(40):
        f.step(on_ground=False, ias=100, gs=100, vs=0, alt_msl=3000, alt_agl=3000)
    f.step(on_ground=True, ias=60, gs=60, vs=-120, lat=lat, lon=lon)


def test_end_now_settles_a_landed_flight_that_was_never_detected_as_parked():
    events = []
    rec = FlightRecorder(apt("AAAA", 51.0, 0.0), apt("BBBB", 51.0, 0.5), get_type("c172"), 0,
                         on_event=lambda k, d, data: events.append(k))
    f = Feeder(rec)
    _land_at(rec, f, 51.0, 0.5)
    f.step(gs=0, ias=0, lat=51.0, lon=0.5)             # stopped, but engines running and brake off: not "parked"
    assert not rec.finished
    assert rec.end_now() and rec.finished and rec.outcome == "completed" and "arrived" in events


def test_end_now_refuses_in_the_air_and_aborts_if_never_landed():
    rec = FlightRecorder(apt("AAAA", 51.0, 0.0), apt("BBBB", 51.0, 0.5), get_type("c172"), 0)
    f = Feeder(rec)
    f.step()
    f.step(gs=60, ias=60)
    f.step(on_ground=False, ias=70, gs=70, vs=600, alt_msl=300, alt_agl=300)
    with pytest.raises(ValueError):
        rec.end_now()
    rec2 = FlightRecorder(None, None, get_type("c172"), 0)
    f2 = Feeder(rec2)
    f2.step()
    f2.step(gs=5)
    assert rec2.started and rec2.end_now() and rec2.outcome == "aborted"
