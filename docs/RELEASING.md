# Builds and releases

## Automatic release

`.github/workflows/release.yml` runs on pushes to `main` and manual dispatches from `main`. Pull requests use the read-only test workflow. The release job:

1. Fetches tags and chooses a version with `scripts/release-version.py`.
2. Runs the test suite before packaging.
3. Downloads official appimagetool and verifies the asset against GitHub's published SHA-256.
4. Freezes Python/PySide6 with PyInstaller, stages an AppDir and produces the x86_64 AppImage plus SHA-256 sidecar.
5. Extracts the image and checks its CLI, real FFprobe discovery through the frozen subprocess trampoline, Qt window/icon and extract-and-run mode.
6. Uploads a workflow artifact, then creates the GitHub tag and release with generated notes and both assets.

Publication requires all preceding steps to succeed. The job's built-in `GITHUB_TOKEN` has `contents: write`; no repository secret or PAT is necessary. Action versions are pinned to commit SHAs. Runs are serialized and do not interrupt an active release. GitHub concurrency may replace intermediate pending runs during a rapid burst of pushes.

## Version policy

`petal/__init__.py` is the source version floor; Python package metadata reads it dynamically. With no previous tags, the first release is `0.3.0`. Subsequent main builds choose the greater of the source floor or the highest stable `vX.Y.Z` tag plus one patch. To start a minor/major series, raise the source floor in a reviewed change. Nonstable or unrelated tags are ignored.

CI stamps the build checkout before packaging and tags the original commit when creating the release; it does not push a version-bump commit. Thus a source checkout shows its version floor, while the released AppImage shows the selected release version. Re-running an already tagged commit keeps its version and retains existing release assets. For a correction, push a new commit and release a new version.

## Local AppImage build

Use x86_64 Linux, preferably Ubuntu 24.04 for the release compatibility baseline. A newer build host can raise the required glibc baseline. Install FFmpeg, desktop-file-utils and Qt system runtime dependencies (EGL/OpenGL/xkbcommon/xcb; the CI workflow lists Ubuntu packages), then:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r packaging/requirements.txt
python3 scripts/fetch-appimagetool.py  # requires authenticated gh
PETAL_PYTHON=.venv/bin/python APPIMAGETOOL="$PWD/build/tools/appimagetool" ./scripts/build-appimage.sh
PETAL_APPIMAGE=dist/Petal-0.3.0-x86_64.AppImage python3 scripts/smoke-appimage.py
```

Successful builds remove their unique `build-appimage.*` staging directory. Failed builds retain it for diagnosis; remove that directory manually once it is no longer needed. `dist/` holds the AppImage and checksum. Neither is tracked. The build recipe leaves system FFmpeg, FFprobe and GPU drivers outside the bundle. Dynamic Qt libraries and third-party notices can be inspected through `--appimage-extract`.

A packaged smoke check cannot prove GPU compatibility. Run the application hardware check and representative sample comparisons on AMD hardware before using a release for a full library. See [test report](TEST_REPORT.md).

Implementation references: [GitHub token permissions](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token), [PyInstaller runtime paths](https://pyinstaller.org/en/stable/runtime-information.html), [external system programs from frozen apps](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application), [appimagetool](https://github.com/AppImage/appimagetool).

Fontconfig is deliberately a host dependency: its library reads the host's configuration and caches. The PyInstaller specification excludes the older build-host Fontconfig library so newer distribution configuration remains readable. Packaged GUI smoke tests reject Fontconfig warnings.

Starting with 0.4, packaged checks also validate the root PNG/`.DirIcon`, standard hicolor icon sizes and isolated `--install-desktop` behavior. The main UI, finished history, report and relocation protocol are tested before packaging. Release media moves are never exercised on real user files during CI.
