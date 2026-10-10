from conftest import AI_URL, NO_AI_URL  # noqa: F401
import http.client
import json
import re
import socket
import threading

from skydispatch.server import cli
from skydispatch.server.engine import Engine


def test_server_cli_runs_pairs_and_stops(tmp_path, monkeypatch):
    lines, stop = [], threading.Event()
    box = {}

    def make():
        from skydispatch.core.config import Settings
        from skydispatch.db.database import Database
        s = Settings()
        s.ai.base_url = AI_URL
        e = Engine(s, Database(tmp_path / "c.db"))
        box["engine"] = e
        return e
    monkeypatch.setattr("skydispatch.server.pairing.lan_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr("skydispatch.server.engine.lan_addresses", lambda: ["127.0.0.1"])

    def out(line):
        lines.append(line)
        if line.startswith("Press Ctrl+C"):
            code = re.search(r"Pairing code: ([A-Z0-9-]+)", "\n".join(lines)).group(1)
            port = int(re.search(r"port (\d+)", "\n".join(lines)).group(1))
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("POST", "/api/v1/pair", json.dumps({"code": code, "device_name": "CLI test"}),
                      {"X-SkyDispatch": "1"})
            box["status"] = c.getresponse().status
            stop.set()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    assert cli.run(["--port", str(free_port), "--no-sim"], stop=stop, out=out, make_engine=make) == 0
    text = "\n".join(lines)
    assert "Phone API on port" in text and "Pairing link: skydispatch://pair?host=127.0.0.1" in text
    assert box["status"] == 200


def test_parser_options():
    a = cli.build_parser().parse_args(["--port", "9000", "--sim", "simulated", "--revoke-all"])
    assert a.port == 9000 and a.sim == "simulated" and a.revoke_all and not a.no_sim
