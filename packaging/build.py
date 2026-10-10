#!/usr/bin/env python3
"""One-command build of the Windows server installer.

    pip install -e ".[dev,voice,sim,discovery]"
    python packaging/build.py

Output lands in dist/:  SkyDispatch-Setup-<ver>.exe   (Inno Setup GUI installer + uninstaller)
The Android app is built separately (android/, see docs/BUILDING.md).
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
    if platform.system() != "Windows":
        print("The SkyDispatch server is a Windows app (it talks to MSFS through SimConnect): build it on Windows.")
        return 1
    env = dict(os.environ, SKYDISPATCH_VERSION=__version__)
    run(sys.executable, "packaging/make_icons.py")
    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "packaging/skydispatch.spec", "--noconfirm", "--clean")
    iscc = shutil.which("ISCC") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    nums = (re.match(r"\d+(?:\.\d+)*", __version__).group(0).split(".") + ["0"] * 4)[:4]
    run(iscc, f"/DAppVersion={__version__}", f"/DAppNumeric={'.'.join(nums)}", "packaging/windows/skydispatch.iss", env=env)
    print("\nDone. The installer is in:", ROOT / "dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
