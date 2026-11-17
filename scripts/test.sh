#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")/.."
export LIBVA_DRIVER_NAME=radeonsi
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
if [[ ${1:-} == --gpu ]]; then
    export PETAL_TEST_GPU=1
fi
exec "${PETAL_PYTHON:-python3}" -m unittest discover -v
