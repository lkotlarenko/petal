#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")/.."
python=${PETAL_PYTHON:-python3}
version=$("$python" -c 'from petal import __version__; print(__version__)')
[[ $(uname -m) == x86_64 ]] || { echo 'This recipe targets x86_64.' >&2; exit 1; }
command -v "${APPIMAGETOOL:-appimagetool}" >/dev/null
# A unique staging directory preserves previous builds and avoids stale files.
stage=$(mktemp -d "$PWD/build-appimage.XXXXXXXX")
"$python" -m PyInstaller --noconfirm --clean --workpath "$stage/work" --distpath "$stage/frozen" packaging/petal.spec
appdir="$stage/Petal.AppDir"
mkdir -p "$appdir/usr/bin" "$appdir/usr/share/licenses/petal" dist
cp -a "$stage/frozen/petal" "$appdir/usr/bin/"
cp packaging/AppRun "$appdir/AppRun"
chmod +x "$appdir/AppRun"
cp packaging/petal.desktop "$appdir/petal.desktop"
QT_QPA_PLATFORM=offscreen "$python" -c 'from pathlib import Path; from petal.icons import render_icons; import sys; render_icons(Path(sys.argv[1]))' "$stage/icons"
cp "$stage/icons/512/petal.png" "$appdir/petal.png"
ln -s petal.png "$appdir/.DirIcon"
mkdir -p "$appdir/usr/share/applications"
cp packaging/petal.desktop "$appdir/usr/share/applications/petal.desktop"
for size in 32 48 64 128 256 512; do
    mkdir -p "$appdir/usr/share/icons/hicolor/${size}x${size}/apps"
    cp "$stage/icons/$size/petal.png" "$appdir/usr/share/icons/hicolor/${size}x${size}/apps/petal.png"
done
cp THIRD_PARTY.md "$appdir/usr/share/licenses/petal/"
cp LICENSE "$appdir/usr/share/licenses/petal/"
cp -a packaging/licenses "$appdir/usr/share/licenses/petal/"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "${APPIMAGETOOL:-appimagetool}" "$appdir" "$PWD/dist/Petal-$version-x86_64.AppImage"
(cd dist && sha256sum "Petal-$version-x86_64.AppImage" > "Petal-$version-x86_64.AppImage.sha256")
rm -rf -- "${stage:?}"
printf 'Built dist/Petal-%s-x86_64.AppImage\n' "$version"
