#!/usr/bin/env bash
set -euo pipefail
image=$(readlink -f -- "${1:?Pass an AppImage path}")
work=$(mktemp -d /tmp/petal-appimage-test.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
(cd "$work" && "$image" --appimage-extract >/dev/null)
launcher="$work/squashfs-root/AppRun"
export XDG_STATE_HOME="$work/state" XDG_CONFIG_HOME="$work/config" XDG_CACHE_HOME="$work/cache" XDG_DATA_HOME="$work/data" HOME="$work/home" APPIMAGE="$image" PETAL_TEST_TRASH=1
mkdir -p "$HOME" "$XDG_DATA_HOME" "$work/source"
"$launcher" --version
"$launcher" self-test
ffmpeg -nostdin -v error -f lavfi -i testsrc2=size=160x90:rate=12 -t 1 -c:v libx264 "$work/source/demo.mkv"
"$launcher" scan "$work/source" "$work/output" > "$work/scan.json"
jq -e 'length==1 and .[0].media.streams[0].codec_type=="video"' "$work/scan.json"
test -L "$work/squashfs-root/.DirIcon"
for size in 32 48 64 128 256 512; do test -f "$work/squashfs-root/usr/share/icons/hicolor/${size}x${size}/apps/petal.png"; done
"$launcher" --install-desktop
test -f "$XDG_DATA_HOME/applications/petal.desktop"
PETAL_SMOKE=1 timeout 30 "$launcher" --smoke-test
APPIMAGE_EXTRACT_AND_RUN=1 "$image" --version
printf 'Rust AppImage: CLI, media discovery, move/Trash, icons, desktop integration and GUI passed.\n'
