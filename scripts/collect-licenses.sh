#!/usr/bin/env bash
set -euo pipefail
output=$1
mkdir -p "$output"
cp LICENSE THIRD_PARTY.md "$output/"
# Notices cover the entire lockfile, including crates not compiled on this host.
# A fresh Linux CI cache does not contain their source/license files yet.
cargo fetch --locked
cargo metadata --format-version 1 --locked --offline > "$output/dependencies.json"
jq -r '.packages[] | select(.source != null) | [.name,.version,.manifest_path,.license] | @tsv' "$output/dependencies.json" |
while IFS=$'\t' read -r name version manifest license; do
    directory=$(dirname -- "$manifest")
    target="$output/crates/$name-$version"
    mkdir -p "$target"
    printf '%s %s\nLicense: %s\nSource: https://crates.io/api/v1/crates/%s/%s/download\n' "$name" "$version" "$license" "$name" "$version" > "$target/NOTICE"
    while IFS= read -r -d '' file; do
        relative=${file#"$directory"/}
        mkdir -p "$target/$(dirname -- "$relative")"
        cp "$file" "$target/$relative"
    done < <(find "$directory" -maxdepth 4 -type f \( -iname 'license*' -o -iname 'copying*' -o -iname 'notice*' \) -print0)
done
rm -- "$output/dependencies.json"
toolchain=$(rustc --print sysroot)
if [[ -d $toolchain/share/doc/rust/licenses ]]; then
    cp -R "$toolchain/share/doc/rust/licenses" "$output/rust-toolchain"
else
    echo 'Rust license texts are missing; install the rust-docs component before packaging.' >&2
    exit 1
fi
rustc --version --verbose > "$output/build-toolchain.txt"
