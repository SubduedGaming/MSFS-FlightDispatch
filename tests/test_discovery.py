import sys
import types

from skydispatch.server import discovery
from skydispatch.server.discovery import Advertiser


def test_without_zeroconf_nothing_happens(monkeypatch):
    monkeypatch.setitem(sys.modules, "zeroconf", None)          # makes `import zeroconf` raise ImportError
    a = Advertiser()
    assert a.start(8766) is False and not a.running
    a.stop()


def test_registers_and_unregisters_the_service(monkeypatch):
    calls = []

    class Info:
        def __init__(self, type_, name, **kw):
            calls.append(("info", type_, name, kw["port"], kw["properties"]))

    class Zc:
        def register_service(self, info): calls.append(("register",))
        def unregister_service(self, info): calls.append(("unregister",))
        def close(self): calls.append(("close",))

    fake = types.SimpleNamespace(ServiceInfo=Info, Zeroconf=Zc)
    monkeypatch.setitem(sys.modules, "zeroconf", fake)
    monkeypatch.setattr(discovery, "lan_addresses", lambda: ["192.168.1.20"])
    a = Advertiser()
    assert a.start(8766, "My PC") is True and a.running
    a.stop()
    assert not a.running
    kinds = [c[0] for c in calls]
    assert kinds == ["info", "register", "unregister", "close"]
    assert calls[0][1] == "_skydispatch._tcp.local." and calls[0][3] == 8766 and calls[0][4]["api"] == "1"


def test_a_failing_zeroconf_never_breaks_the_server(monkeypatch):
    class Boom:
        def __init__(self, *a, **k): raise OSError("no network")
    monkeypatch.setitem(sys.modules, "zeroconf", types.SimpleNamespace(ServiceInfo=lambda *a, **k: object(), Zeroconf=Boom))
    monkeypatch.setattr(discovery, "lan_addresses", lambda: ["192.168.1.20"])
    a = Advertiser()
    assert a.start(8766) is False and not a.running
