"""Start SkyDispatch when the user signs in to Windows (a per-user Run key; no admin rights needed)."""
from __future__ import annotations

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SkyDispatch"
MINIMIZED_FLAG = "--minimized"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """The command Windows should run at sign-in: this program, starting hidden in the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {MINIMIZED_FLAG}'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):                 # no console window when started from source
        candidate = exe[:-len("python.exe")] + "pythonw.exe"
        exe = candidate
    return f'"{exe}" -m skydispatch {MINIMIZED_FLAG}'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except OSError:
                pass
