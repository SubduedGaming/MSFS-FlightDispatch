# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller packaging/skydispatch.spec --noconfirm
# Produces dist/SkyDispatch (the app; on Windows it also hosts the data sharing for other computers).
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from skydispatch import __version__  # noqa: E402

ICON = {"win32": ROOT / "packaging/windows/skydispatch.ico",
        "darwin": ROOT / "packaging/macos/SkyDispatch.icns"}.get(sys.platform)
ICON = str(ICON) if ICON and ICON.exists() else None

datas = [(str(ROOT / "src/skydispatch/resources"), "resources"), (str(ROOT / "LICENSE"), ".")]
binaries, hidden = [], collect_submodules("skydispatch")

# Optional feature packages: bundled only when they are installed in the build environment.
for pkg in ("faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "av", "piper", "sounddevice",
            "_sounddevice_data", "pynput", "numpy", "SimConnect"):
    if importlib.util.find_spec(pkg):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h

EXCLUDES = ["tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization"]

gui = Analysis([str(ROOT / "packaging/entry_gui.py")], pathex=[str(ROOT / "src")], binaries=binaries, datas=datas,
               hiddenimports=hidden, excludes=EXCLUDES, noarchive=False)
gui_pyz = PYZ(gui.pure)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="SkyDispatch", console=False, icon=ICON,
              disable_windowed_traceback=False, upx=False)
gui_coll = COLLECT(gui_exe, gui.binaries, gui.datas, strip=False, upx=False, name="SkyDispatch")

if sys.platform == "darwin":
    app = BUNDLE(gui_coll, name="SkyDispatch.app", icon=ICON, bundle_identifier="com.skydispatch.app",
                 version=__version__,
                 info_plist={
                     "CFBundleDisplayName": "SkyDispatch",
                     "CFBundleShortVersionString": __version__,
                     "NSHighResolutionCapable": True,
                     "NSMicrophoneUsageDescription": "SkyDispatch listens to you when you hold the talk button, "
                                                     "so you can speak to your AI dispatcher.",
                     "LSApplicationCategoryType": "public.app-category.games",
                 })
