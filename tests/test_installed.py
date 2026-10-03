from pathlib import Path

from skydispatch.core.config import Settings
from skydispatch.sim import installed as inst


def make_pkg(root: Path, where: str, pkg: str, planes: dict[str, str]):
    base = root / where / pkg / "SimObjects" / "Airplanes"
    for folder, title in planes.items():
        d = base / folder
        d.mkdir(parents=True)
        (d / "aircraft.cfg").write_text(f'[FLTSIM.0]\ntitle = "{title}"\nmodel = ""\n')


def test_scan_finds_official_and_community_aircraft(tmp_path):
    make_pkg(tmp_path, "Official/OneStore", "asobo-aircraft-c172sp-as1000", {"Asobo_C172sp_AS1000": "TT:AIRCRAFT.X"})
    make_pkg(tmp_path, "Official/Steam", "asobo-aircraft-b747-8i", {"Asobo_B747_8i": "Boeing 747-8i Asobo"})
    make_pkg(tmp_path, "Community", "fbw-a32nx", {"flybywire-aircraft-a320-neo": "FlyByWire Simulations Airbus A320neo"})
    make_pkg(tmp_path, "Community", "some-livery-pack", {"Pitts_Special": "Pitts S-1 Skin"})   # not in catalog
    assert inst.scan_packages(tmp_path) == {"c172", "b748", "a320"}


def test_scan_uses_aircraft_cfg_titles(tmp_path):
    make_pkg(tmp_path, "Community", "mystery", {"mod_folder_123": "Daher TBM 930 (Custom Paint)"})
    assert "tbm9" in inst.scan_packages(tmp_path)


def test_detect_returns_none_without_install(tmp_path, monkeypatch):
    monkeypatch.delenv("MSFS_PACKAGES_PATH", raising=False)
    assert inst.detect_installed("") is None
    assert inst.detect_installed(str(tmp_path / "missing")) is None


def test_detect_with_custom_path(tmp_path):
    make_pkg(tmp_path, "Community", "x", {"Asobo_DA40NG": "Diamond DA40 NG"})
    assert inst.detect_installed(str(tmp_path)) == {"da40"}


def test_usercfg_parsing(tmp_path):
    cfg = tmp_path / "UserCfg.opt"
    cfg.write_text('{Version 1}\nInstalledPackagesPath "E:\\Pkgs"\nOther 1\n')
    assert inst.packages_path_from_usercfg(cfg) == Path("E:\\Pkgs")
    cfg.write_text('{InstalledPackagesPath "D:\\MSFS Packages"}\n')          # braced variant
    assert inst.packages_path_from_usercfg(cfg) == Path("D:\\MSFS Packages")
    cfg.write_text("nothing useful")
    assert inst.packages_path_from_usercfg(cfg) is None
    assert inst.packages_path_from_usercfg(tmp_path / "missing.opt") is None


def test_settings_helpers(db):
    s = Settings()
    assert inst.installed_types(s, db) is None                  # unknown -> unrestricted
    assert inst.is_installed("a320", s, db)
    s.sim.installed_aircraft = inst.format_ids({"c172", "baron"})
    assert inst.installed_types(s, db) == {"c172", "baron"}
    assert not inst.is_installed("a320", s, db)
    db.record_aircraft_flown("Boeing 737-800 NG", "b738", 30)    # flown in the sim => installed
    assert inst.is_installed("b738", s, db)
    s.sim.restrict_to_installed = False
    assert inst.installed_types(s, db) is None
    assert inst.parse_ids("c172, nonsense ,baron") == {"c172", "baron"}


def test_custom_path_may_point_at_parent_community_or_official(tmp_path):
    make_pkg(tmp_path / "MSFS24" / "Packages", "Community", "x", {"Asobo_DA40NG": "Diamond DA40 NG"})
    make_pkg(tmp_path / "MSFS24" / "Packages", "Official/OneStore", "asobo-aircraft-c172sp", {"Asobo_C172sp": "x"})
    pk = tmp_path / "MSFS24" / "Packages"
    for given in (tmp_path / "MSFS24", pk, pk / "Community", pk / "Official", pk / "Official" / "OneStore"):
        assert {"da40", "c172"} >= inst.detect_installed(str(given)) >= {"da40" if given.name != "OneStore" else "c172"}, given
    assert inst.detect_installed(str(tmp_path / "MSFS24")) == {"da40", "c172"}      # parent of Packages


def test_folder_of_packages_without_community_or_official(tmp_path):
    make_pkg(tmp_path, "", "asobo-aircraft-tbm930", {"Asobo_TBM930": "Daher TBM 930"})
    assert inst.detect_installed(str(tmp_path)) == {"tbm9"}


def test_searched_locations_mentions_custom_path(tmp_path):
    assert str(tmp_path) in inst.searched_locations(str(tmp_path))


def test_msfs2024_store_layout(tmp_path):
    """MSFS 2024 keeps Community2024 / Official2020 / Official2024 (each with OneStore) beside Community."""
    make_pkg(tmp_path, "Official2024/OneStore", "asobo-aircraft-tbm930", {"Asobo_TBM930": "Daher TBM 930"})
    make_pkg(tmp_path, "Official2020/OneStore", "asobo-aircraft-c172sp", {"Asobo_C172sp": "x"})
    make_pkg(tmp_path, "Community2024", "fbw-a32nx", {"flybywire-aircraft-a320-neo": "FlyByWire Airbus A320neo"})
    assert inst.detect_installed(str(tmp_path)) == {"tbm9", "c172", "a320"}
    assert inst.detect_installed(str(tmp_path / "Official2024")) == {"tbm9", "c172", "a320"}
