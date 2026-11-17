#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")/.."
[[ $(uname -m) == x86_64 ]] || { echo 'This recipe targets x86_64.' >&2; exit 1; }
cargo build --release --locked
binary="${CARGO_TARGET_DIR:-target}/release/petal"
version=$(sed -n 's/^version = "\([0-9]*\.[0-9]*\.[0-9]*\)"$/\1/p' Cargo.toml)
stage=$(mktemp -d "$PWD/build-appimage.XXXXXXXX")
appdir="$stage/Petal.AppDir"
mkdir -p "$appdir/usr/bin" "$appdir/usr/share/applications" dist
cp "$binary" "$appdir/usr/bin/petal"
cp packaging/AppRun "$appdir/AppRun"
chmod +x "$appdir/AppRun"
cp packaging/petal.desktop "$appdir/petal.desktop"
cp packaging/petal.desktop "$appdir/usr/share/applications/petal.desktop"
mkdir -p "$appdir/usr/share/metainfo"
sed -E "s/<release version=\"[^\"]*\" date=\"[^\"]*\"/<release version=\"$version\" date=\"$(date -u +%F)\"/" packaging/petal.metainfo.xml > "$appdir/usr/share/metainfo/io.github.lkotlarenko.petal.metainfo.xml"
cp assets/petal.png "$appdir/petal.png"
ln -s petal.png "$appdir/.DirIcon"
"$binary" --write-icons "$appdir/usr/share/icons/hicolor"
./scripts/collect-licenses.sh "$appdir/usr/share/licenses/petal"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "${APPIMAGETOOL:-appimagetool}" --comp zstd "$appdir" "$PWD/dist/Petal-$version-x86_64.AppImage"
(cd dist && sha256sum "Petal-$version-x86_64.AppImage" > "Petal-$version-x86_64.AppImage.sha256")
rm -rf -- "${stage:?}"
printf 'Built dist/Petal-%s-x86_64.AppImage\n' "$version"
