import socket
import time

from skydispatch.sim.base import SimState
from skydispatch.sim.bridge import BridgeClientProvider
from skydispatch.sim.bridge_server import BridgeHost, local_addresses, serve
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
        server.stop()


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
        server.stop()


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
        server.stop()


def test_bridge_reports_installed_aircraft_to_the_client():
    port = free_port()
    src = SimulatedProvider(sample_hz=20)
    server = serve(src, "127.0.0.1", port, "", installed=["c172", "a320"])
    client = BridgeClientProvider("127.0.0.1", port, "", 20)
    try:
        client.start()
        assert wait(lambda: client.status == "connected")
        assert client.installed == ["c172", "a320"]
    finally:
        client.stop()
        src.stop()
        server.stop()


def test_app_context_shares_to_a_remote_client(qtbot):
    from skydispatch.core.config import Settings
    from skydispatch.db.database import Database
    from skydispatch.ui.context import AppContext

    port = free_port()
    settings = Settings()
    settings.sim.mode = "simulated"
    settings.sim.share_enabled = True
    settings.sim.share_port = port
    settings.sim.installed_aircraft = "c172,da40"
    ctx = AppContext(settings, Database())
    client = BridgeClientProvider("127.0.0.1", port, "", 20)
    got = []
    try:
        ctx.start_sim()
        assert ctx.share_host is not None and settings.sim.share_token      # token auto-generated
        client.token = settings.sim.share_token
        client.on_state = got.append
        client.start()
        assert wait(lambda: len(got) > 3)
        assert client.installed == ["c172", "da40"]
        # switching sharing off stops the host
        settings.sim.share_enabled = False
        ctx.start_sim()
        assert ctx.share_host is None
    finally:
        client.stop()
        ctx.shutdown()


def test_share_is_skipped_in_client_mode(qtbot):
    from skydispatch.core.config import Settings
    from skydispatch.db.database import Database
    from skydispatch.ui.context import AppContext

    settings = Settings()
    settings.sim.share_enabled = True
    settings.sim.mode = "bridge"
    ctx = AppContext(settings, Database())
    try:
        ctx.start_share()
        assert ctx.share_host is None
    finally:
        ctx.shutdown()


def test_host_rejects_wrong_token_and_lists_addresses():
    port = free_port()
    host = BridgeHost("127.0.0.1", port, "right")
    host.start()
    bad = BridgeClientProvider("127.0.0.1", port, "wrong", 20)
    statuses = []
    bad.on_status = lambda st, msg: statuses.append(st)
    try:
        bad.start()
        assert wait(lambda: "error" in statuses)
        assert host.client_count == 0
        with __import__("pytest").raises(OSError):
            BridgeHost("127.0.0.1", port, "x").start()          # port already in use
    finally:
        bad.stop()
        host.stop()
    assert isinstance(local_addresses(), list)
