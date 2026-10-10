"""Self-update from GitHub releases: find a newer release, download the installer for this OS, run it.

Only the Windows installer is launched automatically. On other systems the app opens the release page instead.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from . import __version__
from .core import paths

log = logging.getLogger(__name__)

REPO = "SubduedGaming/MSFS-FlightDispatch"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
DOWNLOAD_HOST_SUFFIXES = ("github.com", "githubusercontent.com")   # only ever download from GitHub
INNO_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B1E5C0A-9F3D-4B7E-8A52-3C9D2E41F7A8}_is1"
TOKEN_ENV = "SKYDISPATCH_GITHUB_TOKEN"     # optional: lets a private repository's releases be found and downloaded


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256: str = ""                 # from GitHub's asset digest when it provides one
    asset_api_url: str = ""          # needed (with a token) to download from a private repository

    @property
    def installable(self) -> bool:
        return bool(self.asset_url) and sys.platform == "win32"


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Anything non-numeric after the numbers (e.g. '-rc1') is ignored."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _is_prerelease(text: str) -> bool:
    return bool(re.match(r"\s*v?\d+(?:\.\d+)*[-+.]?(?:a|b|rc|alpha|beta|pre)", text or "", re.I))


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    if a == b:                       # 2.0.0 is newer than 2.0.0-beta.1
        return bool(a) and _is_prerelease(current) and not _is_prerelease(candidate)
    return bool(a) and a > b


def _asset_for_this_os(assets: list[dict]) -> dict | None:
    if sys.platform == "win32":
        pat = re.compile(r"^SkyDispatch-Setup-.*\.exe$", re.I)
    elif sys.platform == "darwin":
        pat = re.compile(r"^SkyDispatch-.*\.dmg$", re.I)
    else:
        pat = re.compile(r"\.AppImage$", re.I)
    return next((a for a in assets if pat.search(a.get("name", ""))), None)


def parse_release(data: dict, current: str = __version__) -> UpdateInfo | None:
    """UpdateInfo for a GitHub 'latest release' payload if it is newer than `current`, else None."""
    if data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name", "")).lstrip("vV")
    if not is_newer(version, current):
        return None
    info = UpdateInfo(version=version, notes=str(data.get("body") or ""),
                      page_url=str(data.get("html_url") or RELEASES_URL))
    asset = _asset_for_this_os(data.get("assets") or [])
    if asset:
        info.asset_name = asset["name"]
        info.asset_url = asset.get("browser_download_url", "")
        info.asset_size = int(asset.get("size") or 0)
        info.asset_api_url = asset.get("url", "")
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            info.sha256 = digest[7:].lower()
    return info


def _token() -> str:
    return os.environ.get(TOKEN_ENV, "").strip()


def _headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    h = {"Accept": accept, "User-Agent": f"SkyDispatch/{__version__}"}
    if _token():
        h["Authorization"] = f"Bearer {_token()}"       # only ever sent to GitHub; httpx drops it on cross-host redirects
    return h


def check_for_update(current: str = __version__, timeout: float = 10.0) -> UpdateInfo | None:
    """Ask GitHub for the latest release. Raises UpdateError/httpx.HTTPError when it can't be read."""
    r = httpx.get(API_URL, timeout=timeout, headers=_headers())
    if r.status_code in (403, 404):
        raise UpdateError("GitHub does not list any public releases for SkyDispatch (the repository may be private, "
                          f"or the rate limit was hit). Set {TOKEN_ENV} to use a private repository.")
    r.raise_for_status()
    return parse_release(r.json(), current)


def _trusted(url: str) -> bool:
    u = httpx.URL(url)
    return u.scheme == "https" and any(u.host == s or u.host.endswith("." + s) for s in DOWNLOAD_HOST_SUFFIXES)


def download(info: UpdateInfo, progress: Callable[[int, int], None] | None = None,
             dest_dir: Path | None = None) -> Path:
    """Download the installer to a temp folder, checking size and (when published) SHA-256. Returns its path."""
    url, accept = info.asset_url, "application/octet-stream"
    if _token() and info.asset_api_url:                  # private repositories only serve assets through the API
        url = info.asset_api_url
    if not url or not _trusted(url):
        raise UpdateError("No trusted installer download is available for this release.")
    dest_dir = dest_dir or Path(tempfile.mkdtemp(prefix="skydispatch-update-"))
    dest = dest_dir / Path(info.asset_name).name        # never trust a path from the network
    sha = hashlib.sha256()
    done = 0
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=30.0, headers=_headers(accept)) as r:
            r.raise_for_status()
            if not _trusted(str(r.url)):
                raise UpdateError("The download redirected to an untrusted host.")
            total = int(r.headers.get("content-length") or info.asset_size or 0)
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(256 * 1024):
                    fh.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc}") from exc
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    if info.asset_size and done != info.asset_size:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download was incomplete. Try again.")
    if info.sha256 and sha.hexdigest() != info.sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download failed its integrity check, so it was discarded.")
    return dest


def install_scope(app_dir: Path | None = None) -> tuple[str, Path | None]:
    """How this copy was installed: ('allusers' | 'currentuser', its folder), read from the installer's registry entry.

    The update must repeat the same kind of install: an all-users copy lives in Program Files, which only an
    elevated installer can change."""
    app_dir = app_dir if app_dir is not None else (Path(sys.executable).parent if getattr(sys, "frozen", False) else None)
    if sys.platform == "win32":
        import winreg
        for hive, scope in ((winreg.HKEY_LOCAL_MACHINE, "allusers"), (winreg.HKEY_CURRENT_USER, "currentuser")):
            try:
                with winreg.OpenKey(hive, INNO_UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    location = Path(winreg.QueryValueEx(key, "InstallLocation")[0])
            except OSError:
                continue
            if app_dir is None or location.resolve() == app_dir.resolve():
                return scope, location
    return "currentuser", app_dir


def installer_args(scope: str, app_dir: Path | None, log_file: Path | None = None) -> list[str]:
    args = ["/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART", "/UPDATING=1",
            "/ALLUSERS" if scope == "allusers" else "/CURRENTUSER"]
    if app_dir is not None:
        args.append(f'/DIR="{app_dir}"')
    if log_file is not None:
        args.append(f'/LOG="{log_file}"')
    return args


def launch_installer(path: Path) -> None:
    """Start the Windows installer: it waits for this app to exit, upgrades the same install in place (career data
    is kept) and reopens SkyDispatch. An all-users install needs Windows' permission prompt (UAC). The caller should
    quit right after this returns; it raises UpdateError (and nothing changes) if the prompt is declined."""
    if sys.platform != "win32":
        raise UpdateError("Automatic install is only available on Windows.")
    scope, app_dir = install_scope()
    args = " ".join(installer_args(scope, app_dir, paths.log_dir() / "installer.log"))
    workdir = os.path.dirname(str(path))
    if scope == "allusers":
        # "runas" shows the UAC prompt; ShellExecute returns > 32 once the elevated installer has started
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), args, workdir, 1)
        if rc <= 32:
            raise UpdateError("The update was not started: Windows did not allow the installer to run "
                              "(was the permission prompt declined?).")
    else:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(f'"{path}" {args}', creationflags=flags, close_fds=True, cwd=workdir)
