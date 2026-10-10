# Building installers

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,voice]"                            # add ",sim" on Windows
python packaging/build.py
```

`build.py` renders icons, runs PyInstaller (`packaging/skydispatch.spec`) and then builds the native installer for the
OS you are on. Results are written to `dist/`.

| OS | Output | Requires |
|----|--------|----------|
| Windows | `SkyDispatch-Setup-<ver>.exe` GUI installer with built-in uninstaller (`unins000.exe`) | [Inno Setup 6](https://jrsoftware.org/isinfo.php) |
| macOS | `SkyDispatch-<ver>.dmg` and `SkyDispatch-<ver>.pkg` + `Uninstall SkyDispatch.command` | Xcode command-line tools |
| Linux | `skydispatch_<ver>_amd64.deb` and `SkyDispatch-<ver>-x86_64.AppImage` | `dpkg-deb`; `appimagetool` for the AppImage |

Installers must be built on their own OS (PyInstaller does not cross-compile), by running `python packaging/build.py`
on that OS. There is no automated release workflow; installers are built by hand.

## Signing (recommended before sharing builds)
- **macOS:** set `MACOS_CODESIGN_IDENTITY` and `MACOS_INSTALLER_IDENTITY`, then notarise the result with `notarytool`.
  Unsigned builds work, but Gatekeeper requires right-click > Open on first launch.
- **Windows:** sign `SkyDispatch.exe` and the installer with `signtool` to avoid SmartScreen warnings.

## Size
The speech models are downloaded on demand, but the voice libraries (`faster-whisper`, `onnxruntime`) add a few hundred
MB to the installer. Build with `pip install -e ".[dev]"` instead of `[dev,voice]` for a slim installer without
speech recognition/synthesis (the app then falls back to OS voices for speech output).
