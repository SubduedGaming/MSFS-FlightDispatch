#!/usr/bin/env bash
# Builds dist/SkyDispatch-<ver>.dmg (drag-to-Applications) and dist/SkyDispatch-<ver>.pkg (GUI installer).
# Optional signing/notarisation (recommended for distribution):
#   MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   MACOS_INSTALLER_IDENTITY="Developer ID Installer: Your Name (TEAMID)"
set -euo pipefail
VERSION="${1:?usage: build_macos.sh <version>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
APP="dist/SkyDispatch.app"
[ -d "$APP" ] || { echo "Missing $APP - run PyInstaller first"; exit 1; }

# 1. Code signing (ad-hoc when no identity is configured, so the app at least launches on Apple Silicon)
if [ -n "${MACOS_CODESIGN_IDENTITY:-}" ]; then
  codesign --force --deep --options runtime --timestamp --sign "$MACOS_CODESIGN_IDENTITY" "$APP"
else
  echo "No MACOS_CODESIGN_IDENTITY set: ad-hoc signing (users must right-click > Open the first time)."
  codesign --force --deep --sign - "$APP"
fi

# 2. Disk image with an Applications shortcut and the uninstaller
STAGE="build/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "packaging/macos/Uninstall SkyDispatch.command" "$STAGE/"
chmod +x "$STAGE/Uninstall SkyDispatch.command"
hdiutil create -volname "SkyDispatch $VERSION" -srcfolder "$STAGE" -ov -format UDZO "dist/SkyDispatch-$VERSION.dmg"

# 3. Installer package (double-click GUI installer with welcome/licence/finish pages)
PKGROOT="build/pkgroot"
rm -rf "$PKGROOT" && mkdir -p "$PKGROOT/Applications" "$PKGROOT/Library/Application Support/SkyDispatch"
cp -R "$APP" "$PKGROOT/Applications/"
cp "packaging/macos/Uninstall SkyDispatch.command" "$PKGROOT/Library/Application Support/SkyDispatch/"
chmod +x "$PKGROOT/Library/Application Support/SkyDispatch/Uninstall SkyDispatch.command"
pkgbuild --root "$PKGROOT" --identifier com.skydispatch.app --version "$VERSION" --install-location / \
         "build/SkyDispatch-component.pkg"
cp LICENSE packaging/macos/resources/license.txt
SIGN=()
[ -n "${MACOS_INSTALLER_IDENTITY:-}" ] && SIGN=(--sign "$MACOS_INSTALLER_IDENTITY")
productbuild --distribution packaging/macos/distribution.xml --resources packaging/macos/resources \
             --package-path build "${SIGN[@]}" "dist/SkyDispatch-$VERSION.pkg"
rm -f packaging/macos/resources/license.txt
echo "Built dist/SkyDispatch-$VERSION.dmg and dist/SkyDispatch-$VERSION.pkg"
