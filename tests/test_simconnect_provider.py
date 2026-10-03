import math
import time

from skydispatch.sim.simconnect_provider import SimConnectProvider


class FakeAQ:
    """Mimics Python-SimConnect's AircraftRequests.get() including radians for angle variables."""
    def __init__(self, **v):
        self.v = {"PLANE_LATITUDE": 51.47, "PLANE_LONGITUDE": -0.45, "PLANE_ALTITUDE": 1200.0,
                  "PLANE_ALT_ABOVE_GROUND": 1100.0, "AIRSPEED_INDICATED": 110.0, "GROUND_VELOCITY": 115.0,
                  "VERTICAL_SPEED": -500.0, "G_FORCE": 1.1, "SIM_ON_GROUND": 0.0,
                  "PLANE_HEADING_DEGREES_MAGNETIC": math.radians(270.0), "PLANE_PITCH_DEGREES": math.radians(-3.0),
                  "PLANE_BANK_DEGREES": math.radians(10.0), "FUEL_TOTAL_QUANTITY": 33.5,
                  "BRAKE_PARKING_POSITION": 0.0, "GEAR_HANDLE_POSITION": 1.0, "FLAPS_HANDLE_PERCENT": 0.5,
                  "GENERAL_ENG_COMBUSTION:1": 1.0, "SIM_DISABLED": 0.0, "TITLE": b"Cessna 172 Skyhawk G1000"}
        self.v.update(v)

    def get(self, key):
        return self.v.get(key)


class FakeReq:
    def __init__(self, value):
        self.value = value


def test_units_are_converted_to_degrees_and_percent():
    p = SimConnectProvider()
    s = p._read(FakeAQ(), FakeReq(0.0), refresh_slow=True)
    assert abs(s.heading - 270.0) < 1e-6          # radians -> degrees, even for small headings
    assert abs(s.pitch + 3.0) < 1e-6 and abs(s.bank - 10.0) < 1e-6
    assert s.flaps == 50.0 and s.title == "Cessna 172 Skyhawk G1000"
    assert s.engine_running and s.gear_down and not s.on_ground and s.fuel_gal == 33.5


def test_small_heading_not_misread():
    p = SimConnectProvider()
    s = p._read(FakeAQ(PLANE_HEADING_DEGREES_MAGNETIC=math.radians(5.0)), FakeReq(0.0), True)
    assert abs(s.heading - 5.0) < 1e-6


def test_touchdown_value_used_only_when_it_changes():
    p = SimConnectProvider()
    aq = FakeAQ()
    first = p._read(aq, FakeReq(3.0), True)              # stale value from an earlier landing: ignored
    assert first.touchdown_fpm is None
    s = p._read(aq, FakeReq(2.0), False)                 # value changed -> fresh touchdown, 2 ft/s = 120 fpm
    assert s.touchdown_fpm == -120.0
    assert p._read(aq, FakeReq(2.0), False).touchdown_fpm == -120.0     # held briefly for the recorder
    p._touch_until = time.time() - 1
    assert p._read(aq, FakeReq(2.0), False).touchdown_fpm is None


def test_slow_values_are_cached_between_fast_reads():
    p = SimConnectProvider()
    aq = FakeAQ()
    p._read(aq, FakeReq(0.0), True)
    aq.v["FUEL_TOTAL_QUANTITY"] = 1.0
    assert p._read(aq, FakeReq(0.0), False).fuel_gal == 33.5
    assert p._read(aq, FakeReq(0.0), True).fuel_gal == 1.0


def test_no_position_yet_returns_none():
    assert SimConnectProvider()._read(FakeAQ(PLANE_LATITUDE=None), FakeReq(None), True) is None
