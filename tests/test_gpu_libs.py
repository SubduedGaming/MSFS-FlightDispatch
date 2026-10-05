import hashlib
import io
import zipfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from skydispatch.voice import gpu


def make_wheel(path: Path, members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def test_extract_takes_only_expected_dlls_and_flattens(tmp_path):
    wheel = tmp_path / "w.whl"
    make_wheel(wheel, {"nvidia/cublas/bin/cublas64_12.dll": b"A", "nvidia/cublas/bin/cublasLt64_12.dll": b"B",
                       "nvidia/cublas/include/cublas.h": b"H", "../evil.dll": b"X",
                       "nvidia/cublas/bin/../../evil2.dll": b"X", "nvidia/cudnn/bin/sub/deep.dll": b"X",
                       "other/bin/foo.dll": b"X", "nvidia/cublas/bin/readme.txt": b"T"})
    out = tmp_path / "out"
    out.mkdir()
    names = gpu.extract_dlls(wheel, out)
    assert sorted(names) == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert sorted(p.name for p in out.iterdir()) == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert not (tmp_path / "evil.dll").exists()


class FakeResponse:
    def __init__(self, data, url):
        self.data, self.url, self.headers = data, httpx.URL(url), {"content-length": str(len(data))}

    def raise_for_status(self):
        pass

    def iter_bytes(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]


def patch_network(monkeypatch, wheels: dict[str, bytes], tamper=False, final_host="files.pythonhosted.org"):
    def fake_get(url, **kw):
        pkg = url.split("/pypi/")[1].split("/")[0]
        data = wheels[pkg]
        meta = {"packagetype": "bdist_wheel", "filename": f"{pkg}-win_amd64.whl", "size": len(data),
                "url": f"https://files.pythonhosted.org/{pkg}.whl",
                "digests": {"sha256": hashlib.sha256(data).hexdigest()}}
        return httpx.Response(200, json={"urls": [meta]}, request=httpx.Request("GET", url))

    @contextmanager
    def fake_stream(method, url, **kw):
        pkg = url.rsplit("/", 1)[1][:-4]
        data = wheels[pkg]
        yield FakeResponse(data[:-1] + b"X" if tamper else data, f"https://{final_host}/{pkg}.whl")
    monkeypatch.setattr(gpu.httpx, "get", fake_get)
    monkeypatch.setattr(gpu.httpx, "stream", fake_stream)


@pytest.fixture
def fake_wheels(tmp_path):
    out = {}
    for pkg, _, sentinel in gpu.PACKAGES:
        lib = "cublas" if "cublas" in pkg else "cudnn"
        out[pkg] = make_wheel(tmp_path / f"{pkg}.whl", {f"nvidia/{lib}/bin/{sentinel}": b"DLL" * 1000,
                                                       f"nvidia/{lib}/bin/extra64.dll": b"E"})
    return out


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(gpu.sys, "platform", "win32")


def test_install_downloads_verifies_and_unpacks(monkeypatch, win, fake_wheels):
    patch_network(monkeypatch, fake_wheels)
    seen = []
    assert not gpu._installed_here()
    gpu.install(lambda f, msg: seen.append((f, msg)))
    assert gpu._installed_here() and (gpu.libs_dir() / "extra64.dll").is_file()
    assert seen[-1][0] == 1.0 and any("Downloading" in m for _, m in seen)
    assert not [p for p in gpu.libs_dir().parent.iterdir() if p.name.startswith("skydispatch-gpu-")]   # staging cleaned
    assert gpu.remove() and not gpu.libs_dir().exists()


def test_corrupt_download_is_rejected_and_nothing_is_installed(monkeypatch, win, fake_wheels):
    patch_network(monkeypatch, fake_wheels, tamper=True)
    with pytest.raises(gpu.GpuLibError, match="integrity"):
        gpu.install()
    assert not gpu.libs_dir().exists()


def test_downloads_must_come_from_pypis_file_server(monkeypatch, win, fake_wheels):
    patch_network(monkeypatch, fake_wheels, final_host="evil.example.com")
    with pytest.raises(gpu.GpuLibError, match="PyPI"):
        gpu.install()
    assert not gpu.libs_dir().exists()


def test_not_enough_disk_space(monkeypatch, win):
    monkeypatch.setattr(gpu.shutil, "disk_usage", lambda p: type("U", (), {"free": 1_000_000_000})())
    with pytest.raises(gpu.GpuLibError, match="disk space"):
        gpu.install()


def test_status_messages(monkeypatch):
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    assert gpu.status()[0] == "unsupported"
    monkeypatch.setattr(gpu.sys, "platform", "win32")
    monkeypatch.setattr(gpu, "has_cuda_device", lambda: False)
    assert gpu.status()[0] == "unsupported"
    monkeypatch.setattr(gpu, "has_cuda_device", lambda: True)
    monkeypatch.setattr(gpu, "available", lambda: False)
    assert gpu.status()[0] == "missing"
    monkeypatch.setattr(gpu, "available", lambda: True)
    assert gpu.status()[0] == "ready"
