# Builds and releases

The maintained app is Rust only. Build scripts are Bash; Python, PyInstaller and Qt are not build or runtime requirements.

## Local checks

Install Rust (stable, minimum declared in Cargo.toml), FFmpeg/FFprobe, GLib/GIO, pkg-config, OpenGL and XKB development libraries, and your desktop portal/driver. On Ubuntu 24.04 the CI workflow lists the exact distribution packages.

```bash
./scripts/test.sh
cargo build --release --locked
./target/release/petal --version
rustup component add rust-docs
./scripts/fetch-appimagetool.sh
APPIMAGETOOL="$PWD/build/tools/appimagetool" ./scripts/build-appimage.sh
./scripts/test-appimage.sh dist/Petal-0.5.0-x86_64.AppImage
```

The downloader uses upstream GitHub asset SHA-256 metadata and refuses a missing digest. Packaging also downloads an AppImage runtime. Building/downloading needs network access. The build copies a native executable, AppRun, desktop entry, six icon sizes and dependency notices into a temporary AppDir, then compresses it using zstd. The stage is removed after success. FFmpeg is not bundled.

Packaged checks extract into an isolated temporary home, test CLI/version, synthetic discovery, file move/Trash self-test, icon/menu integration, GUI startup/shutdown and extract-and-run. Run GUI checks in a real session; CI uses Xvfb with software OpenGL. CI graphics smoke tests do not validate VA-API hardware. Run `doctor` and a synthetic batch on the actual graphics host separately.

## Automatic version and release

Pull requests run Rust formatting, Clippy and tests. Pushes to main additionally run the release workflow, serialized to avoid version races. The source version is a floor. `scripts/release-version.sh --write` chooses the greater of that floor and the next stable patch tag and stamps Cargo.toml/Cargo.lock for the build. Rerunning an already tagged commit keeps that tag.

The release job tests before packaging, tests the AppImage, uploads the artifact and creates the GitHub release/tag only after success. The checksum is included beside the AppImage. An existing release is retained rather than overwriting its assets. Release permissions are scoped to that job.

For review, inspect workflow logs and download the actual GitHub artifact to recheck its checksum/version and launch it. A local build and CI build can differ due to compiler/system versions. Use release tags to retrieve matching source. Preserve old tags/releases for rollback; the maintained tree contains no old implementation.
