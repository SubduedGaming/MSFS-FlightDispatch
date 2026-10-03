import socket
import time

from skydispatch.sim.base import SimState
from skydispatch.sim.bridge import BridgeClientProvider
from skydispatch.sim.bridge_server import serve
from skydispatch.sim.simulated import SimulatedProvider


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_state_roundtrip():
    s = SimState(lat=51.5, lon=-0.4, ias=120, on_ground=False, title="C172")
    assert SimState.from_dict(s.to_dict()) == s
    assert SimState.from_dict({"lat": 1, "bogus": 5}).lat == 1      # unknown keys ignored


def test_bridge_streams_state_with_token():
    port = free_port()
    src = SimulatedProvider(sample_hz=20)
    src.set_position(51.47, -0.46, "Test Plane", fuel_gal=30)
    server = serve(src, "127.0.0.1", port, token="s3cret")
    got = []
    client = BridgeClientProvider("127.0.0.1", port, "s3cret", 20)
    client.on_state = got.append
    try:
        client.start()
        assert wait(lambda: len(got) >= 3), "no telemetry received"
        assert got[0].title == "Test Plane"
        assert client.status == "connected"
    finally:
        client.stop()
        src.stop()
        server.shutdown()
        server.server_close()


def test_bridge_rejects_bad_token():
    port = free_port()
    src = SimulatedProvider(sample_hz=20)
    server = serve(src, "127.0.0.1", port, token="right")
    statuses = []
    client = BridgeClientProvider("127.0.0.1", port, "wrong", 20)
    client.on_status = lambda st, msg: statuses.append((st, msg))
    got = []
    client.on_state = got.append
    try:
        client.start()
        assert wait(lambda: any(s == "error" for s, _ in statuses))
        assert not got
    finally:
        client.stop()
        src.stop()
        server.shutdown()
        server.server_close()


def test_client_retries_until_server_appears():
    port = free_port()
    client = BridgeClientProvider("127.0.0.1", port, "", 20)
    got = []
    client.on_state = got.append
    client.start()
    time.sleep(0.5)
    assert not got
    src = SimulatedProvider(sample_hz=20)
    server = serve(src, "127.0.0.1", port, "")
    try:
        assert wait(lambda: len(got) > 0, timeout=10)
    finally:
        client.stop()
        src.stop()
        server.shutdown()
        server.server_close()
