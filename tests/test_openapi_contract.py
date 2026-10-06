"""docs/api/openapi.json is the contract the Android app is built against: keep it in step with the server."""
import json
import re
from pathlib import Path

import pytest

from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.server.engine import Engine
from skydispatch.web.api import RemoteApi

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.json"
# Handled by the HTTP layer (web/server.py) rather than RemoteApi.
HTTP_LAYER = {("get", "/api/v1/ping"), ("post", "/api/v1/pair"), ("post", "/api/v1/unpair"), ("get", "/api/v1/stream")}


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def api(tmp_path):
    e = Engine(Settings(), Database(tmp_path / "c.db"))
    e.main.run(lambda: e.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000))
    yield RemoteApi(e), e
    e.shutdown()


def spec_operations(spec):
    return {(m, p) for p, ops in spec["paths"].items() for m in ops}


def route_operations(api):
    ops = set()
    for method, rx, _fn in api._routes:
        path = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", rx.pattern[1:-1]).replace("\\", "")
        ops.add((method.lower(), "/api/v1/" + path[len("/api/"):]))
    return ops


def test_every_route_is_documented_and_nothing_extra_is(spec, api):
    documented = spec_operations(spec)
    served = route_operations(api[0]) | HTTP_LAYER
    assert served - documented == set(), "routes missing from docs/api/openapi.json"
    assert documented - served == set(), "documented routes the server does not have"


def test_operation_ids_are_unique_and_described(spec):
    ids = [op["operationId"] for ops in spec["paths"].values() for op in ops.values()]
    assert len(ids) == len(set(ids))
    assert all(op.get("summary") for ops in spec["paths"].values() for op in ops.values())


def test_all_refs_resolve(spec):
    refs = set(re.findall(r'"\$ref": "#/([^"]+)"', json.dumps(spec)))
    for ref in refs:
        node = spec
        for part in ref.split("/"):
            node = node[part]


def test_path_parameters_are_declared(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            declared = {p["name"] for p in op.get("parameters", []) if p["in"] == "path"}
            assert set(re.findall(r"{(\w+)}", path)) == declared, f"{method} {path}"


def test_documented_request_fields_cover_what_handlers_read(spec, api):
    import inspect
    for method, rx, fn in api[0]._routes:
        if method != "POST":
            continue
        read = set(re.findall(r'\bbody(?:\.get\(|\[)\s*"(\w+)"', inspect.getsource(fn)))
        path = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", rx.pattern[1:-1]).replace("\\", "")
        schema = spec["paths"]["/api/v1/" + path[len("/api/"):]]["post"]["requestBody"]["content"]["application/json"]["schema"]
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
        assert read <= set(schema["properties"]), f"{path} reads undocumented fields {read - set(schema['properties'])}"


def test_state_response_matches_its_schema(spec, api):
    status, data = api[0].handle("GET", "/api/state")
    schema = spec["components"]["schemas"]["State"]
    assert status == 200 and set(schema["required"]) <= set(data)
    assert set(data) <= set(schema["properties"])
    assert set(data["pilot"]) <= set(schema["properties"]["pilot"]["properties"])


def test_new_career_schema_matches_the_engine(spec, api):
    opts = api[1].career_options()
    schema = spec["components"]["schemas"]["NewCareer"]["properties"]
    assert set(schema["aircraft"]["enum"]) == {s["id"] for s in opts["starters"]}
    assert set(schema["difficulty"]["enum"]) == set(opts["difficulty"])
    assert set(schema["currency"]["enum"]) == set(opts["currencies"])
