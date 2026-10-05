"""Optional NVIDIA GPU acceleration for speech recognition (Windows).

The installer stays small: the CUDA libraries (cuBLAS and cuDNN, about 1.3 GB) are not shipped. If you want the GPU,
Settings > Voice downloads them once from PyPI (NVIDIA publishes them there as wheels), checks their SHA-256 and
unpacks just the DLLs into SkyDispatch's data folder. Without them speech recognition simply uses the CPU, which is
plenty for the small default model.
"""
from __future__ import annotations

import ctypes.util
import hashlib
import logging
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from ..core import paths

log = logging.getLogger(__name__)

# CUDA 12 / cuDNN 9 is what the bundled CTranslate2 (faster-whisper) is built against.
PACKAGES = (("nvidia-cublas-cu12", "12.9.2.10", "cublas64_12.dll"),
            ("nvidia-cudnn-cu12", "9.27.0.42", "cudnn64_9.dll"))
APPROX_SIZE_MB = 1300
NEEDED_FREE_GB = 5                      # downloads (~1.3 GB) + unpacked DLLs + headroom
FILE_HOSTS = ("files.pythonhosted.org",)
_DLL_RX = re.compile(r"^nvidia/[a-z0-9_]+/bin/([A-Za-z0-9_.\-]+\.dll)$")

_handles: list = []                     # keep os.add_dll_directory handles alive


class GpuLibError(Exception):
    pass


def libs_dir() -> Path:
    return paths.data_dir() / "gpu-libs"


def _installed_here() -> bool:
    d = libs_dir()
    return d.is_dir() and all((d / sentinel).is_file() for _, _, sentinel in PACKAGES)


def _on_system_path() -> bool:
    return all(ctypes.util.find_library(sentinel[:-4]) for _, _, sentinel in PACKAGES)


def available() -> bool:
    """True when the CUDA libraries can be found (downloaded here, or installed with the CUDA toolkit)."""
    return sys.platform == "win32" and (_installed_here() or _on_system_path())


def has_cuda_device() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def activate() -> None:
    """Make downloaded DLLs loadable. Call before creating a GPU model."""
    if sys.platform != "win32" or not _installed_here():
        return
    d = str(libs_dir())
    if d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")      # LoadLibrary searches PATH
    if not _handles:
        _handles.append(os.add_dll_directory(d))


def status() -> tuple[str, str]:
    """(state, message); state is one of unsupported | ready | missing."""
    if sys.platform != "win32":
        return "unsupported", "GPU acceleration is only offered on Windows."
    if not has_cuda_device():
        return "unsupported", "No NVIDIA GPU was found, so the CPU is used."
    if available():
        return "ready", "GPU libraries installed. Speech recognition uses your NVIDIA GPU."
    return "missing", (f"Speech recognition uses the CPU. Download the NVIDIA libraries (~{APPROX_SIZE_MB / 1000:.1f} GB, "
                       "one time) to use your GPU.")


# ------------------------------------------------------------------ download
def _release_file(pkg: str, version: str) -> dict:
    """The win_amd64 wheel for an exact version from PyPI's JSON API."""
    r = httpx.get(f"https://pypi.org/pypi/{pkg}/{version}/json", timeout=20.0)
    r.raise_for_status()
    for f in r.json().get("urls", []):
        if f.get("packagetype") == "bdist_wheel" and f["filename"].endswith("win_amd64.whl"):
            return f
    raise GpuLibError(f"No Windows build of {pkg} {version} was found on PyPI.")


def _check_host(url: str) -> None:
    u = httpx.URL(url)
    if u.scheme != "https" or u.host not in FILE_HOSTS:
        raise GpuLibError("The download is not coming from PyPI's file server, so it was refused.")


def _download(file: dict, dest: Path, progress: Callable[[int, int], None]) -> None:
    url, want_sha, size = file["url"], file["digests"]["sha256"].lower(), int(file.get("size") or 0)
    _check_host(url)
    sha, done = hashlib.sha256(), 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
        r.raise_for_status()
        _check_host(str(r.url))
        total = int(r.headers.get("content-length") or size)
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1024 * 1024):
                fh.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                progress(done, total)
    if (size and done != size) or sha.hexdigest() != want_sha:
        raise GpuLibError("A downloaded file failed its integrity check, so it was discarded.")


def extract_dlls(wheel: Path, dest: Path) -> list[str]:
    """Unpack only nvidia/<lib>/bin/*.dll, flattened into `dest`. Returns the file names."""
    names = []
    with zipfile.ZipFile(wheel) as z:
        for info in z.infolist():
            m = _DLL_RX.match(info.filename)
            if not m:
                continue                                    # only DLLs from the expected folders; nothing else is written
            target = dest / m.group(1)                      # the name comes from the regex, never from a raw path
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            names.append(m.group(1))
    return names


def install(progress: Callable[[float, str], None] | None = None) -> None:
    """Download and unpack the libraries. Raises GpuLibError / httpx.HTTPError."""
    progress = progress or (lambda *_: None)
    if sys.platform != "win32":
        raise GpuLibError("GPU acceleration is only offered on Windows.")
    target = libs_dir()
    free = shutil.disk_usage(target.parent).free / 1e9
    if free < NEEDED_FREE_GB:
        raise GpuLibError(f"Not enough free disk space: need about {NEEDED_FREE_GB} GB, have {free:.1f} GB.")
    staging = Path(tempfile.mkdtemp(prefix="skydispatch-gpu-", dir=target.parent))
    try:
        files = [(_release_file(pkg, ver), pkg) for pkg, ver, _ in PACKAGES]
        unpacked = staging / "dlls"
        unpacked.mkdir()
        for i, (f, pkg) in enumerate(files):
            wheel = staging / f["filename"]

            def step(done: int, total: int, i=i, pkg=pkg) -> None:
                frac = (i + (done / total if total else 0)) / len(files)
                progress(frac * 0.95, f"Downloading {pkg} ({done / 1e6:.0f} of {total / 1e6:.0f} MB)")
            _download(f, wheel, step)
            progress((i + 1) / len(files) * 0.95, f"Unpacking {pkg}")
            extract_dlls(wheel, unpacked)
            wheel.unlink()
        if not all((unpacked / s).is_file() for _, _, s in PACKAGES):
            raise GpuLibError("The downloaded packages did not contain the expected libraries.")
        if target.exists():
            shutil.rmtree(target)
        unpacked.replace(target)
        progress(1.0, "Done")
        log.info("Installed GPU libraries to %s", target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove() -> bool:
    """Delete the downloaded libraries. False if some are still in use (restart SkyDispatch, then try again)."""
    shutil.rmtree(libs_dir(), ignore_errors=True)
    return not libs_dir().exists()
