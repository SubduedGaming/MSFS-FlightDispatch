# Building installers

## Windows server installer
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,voice,sim,discovery]"
python packaging/build.py
```

`build.py` renders the icons, runs PyInstaller (`packaging/skydispatch.spec`) and builds the installer with
[Inno Setup 6](https://jrsoftware.org/isinfo.php). The result is `dist/SkyDispatch-Setup-<ver>.exe`, with a built-in
uninstaller. When installed for all users the installer can add a Windows Firewall rule (Private networks only) so
phones can connect; it is removed again on uninstall. It must be built on Windows. There is no automated release
workflow; installers are built by hand.

## Android app
Needs a JDK 17 and the Android SDK (platform 35 and build-tools; `android/local.properties` holds `sdk.dir`).
```bash
cd android
./gradlew testDebugUnitTest assembleDebug          # gradlew.bat on Windows
```
The APK is `android/app/build/outputs/apk/debug/app-debug.apk`. It is signed with the debug key, which is fine for
sideloading; a Play Store release would need its own signing key. Rename it `SkyDispatch-<ver>.apk` for a release.
To try it without a phone, create an Android emulator (API 35, Google APIs image) and pair with the address
`10.0.2.2` (the emulator's name for the PC) and the server's port.

## Signing (recommended before sharing builds)
- **Windows:** sign `SkyDispatch.exe` and the installer with `signtool` to avoid SmartScreen warnings.
- **Android:** sign the release APK with your own key (`apksigner`).

## Size
The speech models are downloaded on demand, but the voice libraries (`faster-whisper`, `onnxruntime`) add a few hundred
MB to the installer. Build with `pip install -e ".[dev]"` instead of `[dev,voice]` for a slim installer without
speech recognition/synthesis (the app then falls back to OS voices for speech output).
