import json
import sqlite3

import pytest

from skydispatch.core import paths
from skydispatch.core.config import Settings
from skydispatch.db.database import Database


def test_settings_roundtrip_and_defaults(tmp_path):
    p = tmp_path / "s.json"
    s = Settings()
    s.ai.base_url = "http://10.0.0.5:1234/v1"
    s.voice.tts_rate = 1.25
    s.game.difficulty = "realistic"
    s.save(p)
    s2 = Settings.load(p)
    assert s2.ai.base_url == "http://10.0.0.5:1234/v1"
    assert s2.voice.tts_rate == 1.25 and s2.game.difficulty == "realistic"


def test_settings_tolerates_unknown_missing_and_bad_types(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"ai": {"base_url": "x", "temperature": "hot", "future_key": 1},
                             "voice": {"tts_enabled": 0}, "mystery": {}}))
    s = Settings.load(p)
    assert s.ai.base_url == "x"
    assert s.ai.temperature == 0.8            # bad value -> default
    assert s.voice.tts_enabled is False
    assert s.sim.mode == "simulated"          # missing section -> defaults


def test_corrupt_settings_file_is_set_aside(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    s = Settings.load(p)
    assert s.pilot.name == "Captain"
    assert (tmp_path / "s.broken.json").exists()


def test_paths_honour_home_override(tmp_path):
    assert str(paths.data_dir()).startswith(str(tmp_path))
    assert paths.database_path().name == "career.db"


def test_db_migration_is_idempotent_and_persists(tmp_path):
    f = tmp_path / "c.db"
    d = Database(f)
    d.create_pilot("A", "B", "EGLL", 500)
    d.close()
    d2 = Database(f)
    assert d2.pilot().name == "A"
    assert d2.q1("PRAGMA user_version")[0] >= 1
    d2.close()


def test_backup_and_restore(tmp_path):
    f, b = tmp_path / "c.db", tmp_path / "b.db"
    d = Database(f)
    d.create_pilot("Original", "O1", "EGLL", 1000)
    d.backup(b)
    d.update_pilot(name="Changed")
    d.close()
    Database.restore(b, f)
    d2 = Database(f)
    assert d2.pilot().name == "Original"
    d2.close()


def test_restore_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite")
    with pytest.raises(Exception):
        Database.restore(bad, tmp_path / "c.db")
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x(a)").connection.commit()
    with pytest.raises(Exception):
        Database.restore(other, tmp_path / "c.db")      # valid sqlite but not a career file


def test_import_airports_csv(db):
    before = len(db.airports())
    apt = ("id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality\n"
           "1,KXYZ,medium_airport,Test Field,40.0,-100.0,1200,NA,US,US-NE,Testville\n"
           "2,EGLL,large_airport,London Heathrow,51.47,-0.45,83,EU,GB,GB-ENG,London\n"
           "3,00AA,small_airport,Skip Me,40,-100,10,NA,US,US-KS,X\n"
           "4,KHEL,heliport,Heli,40,-100,10,NA,US,US-KS,X\n")
    rw = "id,airport_ident,length_ft,closed\n1,KXYZ,7000,0\n2,KXYZ,9000,1\n"
    added = db.import_airports_csv(apt, rw)
    assert added == 1
    a = db.airport("KXYZ")
    assert a.runway_ft == 7000 and a.size == "M"
    assert db.airport("00AA") is None and db.airport("KHEL") is None
    assert len(db.airports()) == before + 1


def test_unknown_fields_rejected(db):
    db.create_pilot("A", "B", "EGLL", 1)
    with pytest.raises(ValueError):
        db.update_pilot(balance=1e9)         # money may only change through add_transaction


def test_transactions_track_balance(db):
    db.create_pilot("A", "B", "EGLL", 1000)
    assert db.add_transaction(-250, "fuel", "x") == 750
    assert db.add_transaction(100, "job", "y") == 850
    assert db.pilot().balance == 850
    assert db.transactions(1)[0]["balance_after"] == 850
