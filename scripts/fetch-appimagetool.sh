#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")/.."
mkdir -p build/tools
metadata=$(gh api repos/AppImage/appimagetool/releases/latest --jq '.assets[] | select(.name=="appimagetool-x86_64.AppImage") | [.browser_download_url,.digest] | @tsv')
IFS=$'\t' read -r url digest <<< "$metadata"
[[ $digest == sha256:* ]] || { echo 'No upstream SHA-256 provided; refusing the download.' >&2; exit 1; }
curl --fail --location --retry 3 "$url" -o build/tools/appimagetool
printf '%s  %s\n' "${digest#sha256:}" build/tools/appimagetool | sha256sum --check -
chmod +x build/tools/appimagetool
