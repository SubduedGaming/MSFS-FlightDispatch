#!/usr/bin/env bash
# Builds dist/skydispatch_<ver>_<arch>.deb and (if appimagetool is available) an AppImage.
set -euo pipefail
VERSION="${1:?usage: build_linux.sh <version>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
[ -d dist/SkyDispatch ] || { echo "Missing dist/SkyDispatch - run PyInstaller first"; exit 1; }
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

# ---------------------------------------------------------------- .deb
PKG="build/deb/skydispatch_${VERSION}_${ARCH}"
rm -rf "$PKG" && mkdir -p "$PKG"/{DEBIAN,opt/skydispatch,usr/bin,usr/share/applications,usr/share/doc/skydispatch} \
                          "$PKG/usr/share/icons/hicolor/256x256/apps"
cp -a dist/SkyDispatch/. "$PKG/opt/skydispatch/"
mkdir -p "$PKG/opt/skydispatch/bridge" && cp -a dist/SkyDispatch-Bridge/. "$PKG/opt/skydispatch/bridge/"
ln -s /opt/skydispatch/SkyDispatch "$PKG/usr/bin/skydispatch"
ln -s /opt/skydispatch/bridge/SkyDispatch-Bridge "$PKG/usr/bin/skydispatch-bridge"
cp packaging/linux/skydispatch.desktop "$PKG/usr/share/applications/"
cp packaging/linux/skydispatch.png "$PKG/usr/share/icons/hicolor/256x256/apps/"
cp LICENSE "$PKG/usr/share/doc/skydispatch/copyright"
SIZE="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: skydispatch
Version: ${VERSION}
Section: games
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${SIZE}
Maintainer: SkyDispatch <noreply@example.com>
Depends: libc6, libegl1, libgl1, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libfontconfig1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0
Recommends: libportaudio2, espeak-ng
Description: Career mode add-on for Microsoft Flight Simulator with an AI dispatcher
 Accept contracts from a job market, record your flights, manage a hangar and
 talk to an AI flight dispatcher by text or voice. Connects to MSFS through the
 SkyDispatch Bridge running on the Windows PC.
CONTROL
cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
exit 0
POST
cp "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
find "$PKG" -type d -exec chmod 755 {} +
dpkg-deb --build --root-owner-group "$PKG" "dist/skydispatch_${VERSION}_${ARCH}.deb"
echo "Built dist/skydispatch_${VERSION}_${ARCH}.deb"

# ------------------------------------------------------------- AppImage
APPIMAGETOOL="$(command -v appimagetool || true)"
[ -z "$APPIMAGETOOL" ] && [ -x ./appimagetool ] && APPIMAGETOOL=./appimagetool
if [ -n "$APPIMAGETOOL" ]; then
  APPDIR="build/SkyDispatch.AppDir"
  rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr"
  cp -a dist/SkyDispatch/. "$APPDIR/usr/"
  mkdir -p "$APPDIR/usr/bridge" && cp -a dist/SkyDispatch-Bridge/. "$APPDIR/usr/bridge/"
  cp packaging/linux/skydispatch.desktop "$APPDIR/skydispatch.desktop"
  cp packaging/linux/skydispatch.png "$APPDIR/skydispatch.png"
  printf '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/SkyDispatch" "$@"\n' > "$APPDIR/AppRun"
  chmod +x "$APPDIR/AppRun"
  ARCH_NAME="$(uname -m)"
  ARCH="$ARCH_NAME" "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "dist/SkyDispatch-${VERSION}-${ARCH_NAME}.AppImage" \
    || ARCH="$ARCH_NAME" "$APPIMAGETOOL" "$APPDIR" "dist/SkyDispatch-${VERSION}-${ARCH_NAME}.AppImage"
  echo "Built AppImage"
else
  echo "appimagetool not found: skipping AppImage (https://github.com/AppImage/appimagetool/releases)"
fi
