import json
import time

import pytest

from skydispatch.server.pairing import (CODE_ALPHABET, MAX_DEVICES, PairingError, PairingManager, format_code,
                                        normalise_code, pair_uri)


@pytest.fixture
def pm(tmp_path):
    return PairingManager(tmp_path / "devices.json")


def test_code_is_single_use_and_typed_forgivingly(pm):
    code = pm.new_code()
    assert len(code) == 8 and set(code) <= set(CODE_ALPHABET)
    typed = format_code(code).lower().replace("-", " ")
    device, token = pm.redeem(typed, "  My   Pixel ")
    assert device.name == "My Pixel" and pm.authenticate(token).id == device.id
    with pytest.raises(PairingError):
        pm.redeem(code, "again")                     # spent


def test_wrong_and_expired_codes_are_refused_without_spending_the_real_one(pm):
    code = pm.new_code()
    with pytest.raises(PairingError):
        pm.redeem("WRONGCOD", "x")
    pm.redeem(code, "x")                             # the real code still works after a bad guess
    pm.new_code(ttl=-1)
    with pytest.raises(PairingError):
        pm.redeem(pm._code, "x")
    assert pm.code_state() == ("", 0.0)
    with pytest.raises(PairingError):
        PairingManager(pm.path.with_name("none.json")).redeem("ABCDEFGH", "x")      # no code was ever shown


def test_tokens_are_hashed_on_disk_and_survive_a_restart(pm, tmp_path):
    pm.new_code()
    device, token = pm.redeem(pm._code, "Tablet")
    raw = pm.path.read_text()
    assert token not in raw and token.split(".")[1] not in raw
    again = PairingManager(pm.path)
    assert again.authenticate(token).name == "Tablet"
    assert again.authenticate(token + "x") is None and again.authenticate("nope") is None
    assert again.authenticate(device.id + ".") is None and again.authenticate("") is None


def test_revoke_signs_the_device_out(pm):
    pm.new_code()
    device, token = pm.redeem(pm._code, "A")
    assert pm.revoke(device.id) is True and pm.revoke(device.id) is False
    assert pm.authenticate(token) is None
    pm.new_code()
    _d, t2 = pm.redeem(pm._code, "B")
    pm.revoke_all()
    assert pm.authenticate(t2) is None and pm.devices() == []


def test_device_limit(pm):
    for i in range(MAX_DEVICES):
        pm.new_code()
        pm.redeem(pm._code, f"d{i}")
    pm.new_code()
    with pytest.raises(PairingError, match="paired devices"):
        pm.redeem(pm._code, "one too many")


def test_last_seen_is_updated_but_not_on_every_call(pm):
    pm.new_code()
    device, token = pm.redeem(pm._code, "A")
    device.last_seen = time.time() - 3600
    pm.authenticate(token)
    assert time.time() - device.last_seen < 5
    saved = json.loads(pm.path.read_text())["devices"][0]["last_seen"]
    assert time.time() - saved < 5


def test_helpers():
    assert normalise_code("ab-cd 12") == "ABCD12"
    assert pair_uri("192.168.1.20", 8766, "ABCD2345") == "skydispatch://pair?host=192.168.1.20&port=8766&code=ABCD2345"
    assert format_code("ABCD2345") == "ABCD-2345"
