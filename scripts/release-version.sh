#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")/.."
base=$(sed -n 's/^version = "\([0-9]*\.[0-9]*\.[0-9]*\)"$/\1/p' Cargo.toml)
[[ $base =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
current=$(git tag --points-at HEAD | sed -n 's/^v\([0-9]*\.[0-9]*\.[0-9]*\)$/\1/p' | sort -V | tail -1)
if [[ -n $current && $(printf '%s\n%s\n' "$base" "$current" | sort -V | tail -1) == "$current" ]]; then
    version=$current
else
    last=$(git tag --list | sed -n 's/^v\([0-9]*\.[0-9]*\.[0-9]*\)$/\1/p' | sort -V | tail -1)
    if [[ -n $last ]]; then
        IFS=. read -r major minor patch <<< "$last"
        next="$major.$minor.$((patch+1))"
        version=$(printf '%s\n%s\n' "$base" "$next" | sort -V | tail -1)
    else
        version=$base
    fi
fi
if [[ ${1:-} == --write ]]; then
    sed -i "s/^version = \"$base\"$/version = \"$version\"/" Cargo.toml
    lock=$(mktemp)
    awk -v version="$version" '
      /^\[\[package\]\]/{ours=0}
      /^name = "petal"$/{ours=1}
      ours && /^version = / {$0="version = \""version"\"";ours=0}
      {print}
    ' Cargo.lock > "$lock"
    cat "$lock" > Cargo.lock
    rm -- "$lock"
fi
printf '%s\n' "$version"
