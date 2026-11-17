#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
export LIBVA_DRIVER_NAME=radeonsi
if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import PySide6' >/dev/null 2>&1; then
    exec .venv/bin/python -m petal "$@"
fi
exec python3 -m petal "$@"
