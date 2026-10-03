import hashlib
from contextlib import contextmanager

import httpx
import pytest

from skydispatch import updater
from skydispatch.updater import UpdateError, UpdateInfo


def release(tag="v9.9.9", **kw):
    data = {"tag_name": tag, "html_url": "https://github.com/x/y/releases/tag/" + tag, "body": "notes",
            "assets": [{"name": "SkyDispatch-Setup-9.9.9.exe", "size": 5, "digest": "sha256:" + "ab" * 32,
                        "browser_download_url": "https://github.com/x/y/releases/download/v9.9.9/SkyDispatch-Setup-9.9.9.exe"},
                       {"name": "SkyDispatch-9.9.9.dmg", "size": 5,
                        "browser_download_url": "https://github.com/x/y/releases/download/v9.9.9/SkyDispatch-9.9.9.dmg"},
                       {"name": "skydispatch_9.9.9_amd64.deb", "size": 5,
                        "browser_download_url": "https://github.com/x/y/releases/download/v9.9.9/a.deb"}]}
    data.update(kw)
    return data


def test_version_parsing_and_comparison():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.10") == (1, 10)
    assert updater.parse_version("garbage") == ()
    assert updater.is_newer("1.1.2", "1.1.1") and updater.is_newer("1.10.0", "1.9.9")
    assert not updater.is_newer("1.1.1", "1.1.1") and not updater.is_newer("1.1", "1.1.0")
    assert updater.is_newer("2", "1.9.9") and not updater.is_newer("", "1.0.0")


def test_parse_release_picks_the_asset_for_this_os(monkeypatch):
    for plat, name in (("win32", "SkyDispatch-Setup-9.9.9.exe"), ("darwin", "SkyDispatch-9.9.9.dmg")):
        monkeypatch.setattr(updater.sys, "platform", plat)
        info = updater.parse_release(release(), current="1.0.0")
        assert info.version == "9.9.9" and info.asset_name == name
    monkeypatch.setattr(updater.sys, "platform", "win32")
    info = updater.parse_release(release(), current="1.0.0")
    assert info.sha256 == "ab" * 32 and info.installable
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    assert not updater.parse_release(release(), current="1.0.0").installable      # only Windows installs itself


def test_parse_release_ignores_old_drafts_and_prereleases():
    assert updater.parse_release(release("v1.0.0"), current="1.0.0") is None
    assert updater.parse_release(release(draft=True), current="1.0.0") is None
    assert updater.parse_release(release(prerelease=True), current="1.0.0") is None


def test_release_without_a_matching_asset_still_reports_the_version(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    info = updater.parse_release(release(assets=[]), current="1.0.0")
    assert info.version == "9.9.9" and not info.installable and info.page_url


class FakeStream:
    def __init__(self, data, url="https://objects.githubusercontent.com/f.exe"):
        self.data, self.url = data, url
        self.headers = {"content-length": str(len(data))}

    def raise_for_status(self):
        pass

    def iter_bytes(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]


def patch_stream(monkeypatch, fake):
    @contextmanager
    def stream(*a, **kw):
        yield fake
    monkeypatch.setattr(updater.httpx, "stream", stream)


def info_for(data, **kw):
    return UpdateInfo(version="9.9.9", notes="", page_url="https://github.com/x/y", asset_name="SkyDispatch-Setup-9.9.9.exe",
                      asset_url="https://github.com/x/y/releases/download/v9.9.9/SkyDispatch-Setup-9.9.9.exe",
                      asset_size=len(data), sha256=hashlib.sha256(data).hexdigest(), **kw)


def test_download_verifies_and_reports_progress(monkeypatch, tmp_path):
    data = b"installer-bytes" * 1000
    patch_stream(monkeypatch, FakeStream(data))
    seen = []
    path = updater.download(info_for(data), lambda d, t: seen.append((d, t)), tmp_path)
    assert path.read_bytes() == data and seen[-1] == (len(data), len(data))


def test_download_rejects_a_corrupt_file(monkeypatch, tmp_path):
    data = b"installer-bytes" * 1000
    info = info_for(data)
    patch_stream(monkeypatch, FakeStream(data[:-1] + b"X"))
    with pytest.raises(UpdateError, match="integrity"):
        updater.download(info, None, tmp_path)
    assert not list(tmp_path.iterdir())                    # nothing left behind


def test_download_rejects_a_truncated_file(monkeypatch, tmp_path):
    data = b"installer-bytes" * 1000
    patch_stream(monkeypatch, FakeStream(data[:100]))
    with pytest.raises(UpdateError, match="incomplete"):
        updater.download(info_for(data), None, tmp_path)


def test_download_refuses_untrusted_hosts(monkeypatch, tmp_path):
    data = b"x" * 10
    bad = info_for(data)
    bad.asset_url = "https://evil.example.com/SkyDispatch-Setup-9.9.9.exe"
    with pytest.raises(UpdateError, match="trusted"):
        updater.download(bad, None, tmp_path)
    bad.asset_url = "http://github.com/x.exe"                          # plain http is not allowed either
    with pytest.raises(UpdateError, match="trusted"):
        updater.download(bad, None, tmp_path)
    patch_stream(monkeypatch, FakeStream(data, url="https://evil.example.com/redirected.exe"))
    with pytest.raises(UpdateError, match="untrusted"):
        updater.download(info_for(data), None, tmp_path)
    assert not list(tmp_path.iterdir())


def test_download_path_cannot_escape_the_folder(monkeypatch, tmp_path):
    data = b"ok"
    info = info_for(data)
    info.asset_name = "..\\..\\evil.exe"
    patch_stream(monkeypatch, FakeStream(data))
    path = updater.download(info, None, tmp_path)
    assert path.parent == tmp_path


def test_check_for_update_uses_the_api(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")

    def fake_get(url, **kw):
        assert url == updater.API_URL
        return httpx.Response(200, json=release(), request=httpx.Request("GET", url))
    monkeypatch.setattr(updater.httpx, "get", fake_get)
    assert updater.check_for_update("1.0.0").version == "9.9.9"
    assert updater.check_for_update("9.9.9") is None


def test_launch_installer_is_windows_only(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    with pytest.raises(UpdateError):
        updater.launch_installer(tmp_path / "x.exe")
    calls = []
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: calls.append(args))
    updater.launch_installer(tmp_path / "x.exe")
    assert calls[0][1:] == ["/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/UPDATING=1"]


def test_context_reports_updates(qtbot, tmp_path, monkeypatch):
    from skydispatch.core.config import Settings
    from skydispatch.db.database import Database
    from skydispatch.ui.context import AppContext
    ctx = AppContext(Settings(), Database(tmp_path / "u.db"))
    info = UpdateInfo(version="9.9.9", notes="n", page_url="https://github.com/x/y")
    monkeypatch.setattr(updater, "check_for_update", lambda: info)
    try:
        with qtbot.waitSignal(ctx.update_available, timeout=5000) as sig:
            ctx.check_for_updates()
        assert sig.args[0] is info and ctx.update_info is info
        ctx.settings.ui.check_updates = False
        ctx.update_info = None
        ctx.check_for_updates()                               # automatic checks respect the setting
        qtbot.wait(300)
        assert ctx.update_info is None
    finally:
        ctx.voice.shutdown()


def test_private_repository_gives_a_clear_error(monkeypatch):
    monkeypatch.delenv(updater.TOKEN_ENV, raising=False)
    monkeypatch.setattr(updater.httpx, "get", lambda url, **kw: httpx.Response(404, request=httpx.Request("GET", url)))
    with pytest.raises(UpdateError, match="private"):
        updater.check_for_update("1.0.0")


def test_token_is_used_for_private_repositories(monkeypatch, tmp_path):
    monkeypatch.setenv(updater.TOKEN_ENV, "tok123")
    seen = {}

    def fake_get(url, **kw):
        seen["get"] = kw["headers"]
        return httpx.Response(200, json=release(), request=httpx.Request("GET", url))
    monkeypatch.setattr(updater.httpx, "get", fake_get)
    updater.check_for_update("1.0.0")
    assert seen["get"]["Authorization"] == "Bearer tok123"

    data = b"installer" * 100
    info = info_for(data)
    info.asset_api_url = "https://api.github.com/repos/x/y/releases/assets/1"

    @contextmanager
    def stream(method, url, **kw):
        seen["url"], seen["headers"] = url, kw["headers"]
        yield FakeStream(data)
    monkeypatch.setattr(updater.httpx, "stream", stream)
    updater.download(info, None, tmp_path)
    assert seen["url"] == info.asset_api_url and seen["headers"]["Accept"] == "application/octet-stream"
    assert seen["headers"]["Authorization"] == "Bearer tok123"
    monkeypatch.delenv(updater.TOKEN_ENV)
    updater.download(info, None, tmp_path)                       # without a token the public asset URL is used
    assert seen["url"] == info.asset_url and "Authorization" not in seen["headers"]
