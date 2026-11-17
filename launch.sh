#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ -x ${CARGO_TARGET_DIR:-target}/release/petal ]]; then
    exec "${CARGO_TARGET_DIR:-target}/release/petal" "$@"
fi
exec cargo run --locked --release -- "$@"
