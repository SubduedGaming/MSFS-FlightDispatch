#!/usr/bin/env python3
"""One-command build for the current OS.

    pip install -e ".[dev,voice]"        # plus ".[sim]" on Windows
    python packaging/build.py

Output lands in dist/:
  Windows : SkyDispatch-Setup-<ver>.exe        (Inno Setup GUI installer + uninstaller)
  macOS   : SkyDispatch-<ver>.dmg and .pkg     (drag-install disk image and GUI installer package)
  Linux   : skydispatch_<ver>_amd64.deb and SkyDispatch-<ver>-x86_64.AppImage
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from skydispatch import __version__  # noqa: E402


def run(*cmd, **kw) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=kw.pop("cwd", ROOT), **kw)


def main() -> int:
    system = platform.system()
    env = dict(os.environ, SKYDISPATCH_VERSION=__version__)
    run(sys.executable, "packaging/make_icons.py")
    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    if system == "Darwin":
        run(sys.executable, "packaging/macos/make_icns.py")
    run(sys.executable, "-m", "PyInstaller", "packaging/skydispatch.spec", "--noconfirm", "--clean")
    if system == "Windows":
        iscc = shutil.which("ISCC") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        nums = (re.match(r"\d+(?:\.\d+)*", __version__).group(0).split(".") + ["0"] * 4)[:4]
        run(iscc, f"/DAppVersion={__version__}", f"/DAppNumeric={'.'.join(nums)}", "packaging/windows/skydispatch.iss", env=env)
    elif system == "Darwin":
        run("bash", "packaging/macos/build_macos.sh", __version__, env=env)
    else:
        run("bash", "packaging/linux/build_linux.sh", __version__, env=env)
    print("\nDone. Installers are in:", ROOT / "dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
