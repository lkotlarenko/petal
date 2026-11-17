<div align="center">

<img src="petal/petal.svg" width="88" alt="Petal flower icon">

# Petal

**Careful GPU video compression for Linux.**

A native desktop app for compressing video collections with AMD VA-API.
Review your queue, keep your originals, and inspect measured picture quality.

[Download AppImage](https://github.com/lkotlarenko/petal/releases/latest) · [Usage](docs/USAGE.md) · [Quality guide](docs/QUALITY.md) · [Contributing](CONTRIBUTING.md)

[![Tests](https://github.com/lkotlarenko/petal/actions/workflows/ci.yml/badge.svg)](https://github.com/lkotlarenko/petal/actions/workflows/ci.yml)
[![AppImage release](https://github.com/lkotlarenko/petal/actions/workflows/release.yml/badge.svg)](https://github.com/lkotlarenko/petal/actions/workflows/release.yml)

</div>

![Petal desktop interface](docs/screenshots/petal.png)

*Actual Qt interface with demonstration filenames and values; these are not benchmark results.*

## What Petal does

- Batch AV1 or H.265 compression with one VA-API encoder; verification overlaps the next encode.
- Drag in files or folders, preserve folder hierarchy, and choose an output resolution from 480p to 4K or Original.
- Read metadata with two bounded readers and reuse a persistent discovery cache on repeat scans.
- Inspect local VMAF spot checks with a large colored score, scene bars and links to both videos.
- Pause, stop safely, retry failures and resume recorded completions.
- Keep originals in place. Publish compressed files only after verification, with no replacement of existing outputs.

Petal's destination contains only worthwhile verified compressions. Skipped files are not copied. Use your own backup process for a complete library copy.

## Download and run

Get the Linux x86_64 AppImage and its `.sha256` file from [Releases](https://github.com/lkotlarenko/petal/releases/latest):

```bash
sha256sum -c Petal-<version>-x86_64.AppImage.sha256
chmod +x Petal-<version>-x86_64.AppImage
./Petal-<version>-x86_64.AppImage
```

Release builds target Ubuntu 24.04 or newer compatible Linux distributions, including current Arch/CachyOS. Python and Qt are bundled. **FFmpeg, FFprobe, Fontconfig, VA-API drivers and access to `/dev/dri/renderD*` are system requirements.** AMD Mesa/radeonsi is the tested application target; other GPU vendors are not validated. Optional VMAF needs an FFmpeg build with `libvmaf` and its built-in models. Metric unavailability does not prevent integrity verification.

On Arch/CachyOS:

```bash
sudo pacman -S --needed ffmpeg libva-utils mesa
```

If FUSE is unavailable, run with `APPIMAGE_EXTRACT_AND_RUN=1 ./Petal-<version>-x86_64.AppImage`. No system-wide installation is required. See [troubleshooting](docs/USAGE.md).

## Run from source

```bash
git clone https://github.com/lkotlarenko/petal.git
cd petal
sudo pacman -S --needed python pyside6 ffmpeg libva-utils mesa qt6-wayland qt6-svg
./launch.sh
```

For an isolated installation, use Python 3.11+ with `python3 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`. System media dependencies still apply. Optional application-menu entry: `python3 scripts/install-desktop.py`.

## First compression

1. Let the hardware check finish. Petal validates actual encode/decode combinations before enabling them.
2. Choose separate source and output directories, or drop local videos into the window.
3. Scan and review the queue. Repeat scans reuse unchanged metadata; encoding always probes again.
4. Use **Settings → Compare selected video samples** on representative content. The default quality profiles are starting points, not calibrated guarantees.
5. Start a small batch, inspect the results, then expand to your collection.

Audio is copied by default. Maximum compatibility preserves supported tracks, represents timecode as metadata and converts supported text subtitles; strict copying and explicit omission are selectable. Unsupported HDR, interlacing and unusual formats are skipped with explanations. See [complete media and safety policy](docs/REFERENCE.md).

## Picture quality at a glance

![Picture quality dialog](docs/screenshots/quality.png)

*Demonstration scores. VMAF compares short samples, not the entire file.*

Mint means no concern in the checked scenes; amber or rose suggests visual comparison. Each scene bar includes its time and score. The score measures similarity at the output resolution, not the percentage of quality retained or the separate detail loss from downscaling.

A decoded/container frame-size mismatch shows **Frame size needs attention**, hiding misleading scores. New mismatched outputs fail verification before publication. Existing outputs are retained for inspection. Read [quality interpretation and geometry troubleshooting](docs/QUALITY.md).

## Development and releases

```bash
./scripts/test.sh
./launch.sh --version
./launch.sh scan /path/to/Videos /path/to/Compressed
```

Every successful `main` release workflow selects the next patch version automatically, runs tests, builds an AppImage, tests the packaged CLI and native GUI, and publishes a GitHub release with the AppImage and checksum. Pull requests run tests without publishing. No personal access token is required.

- [Contributing and test setup](CONTRIBUTING.md)
- [Architecture and tuning](docs/DESIGN.md)
- [Release process and local packaging](docs/RELEASING.md)
- [Verification evidence and limits](docs/TEST_REPORT.md)
- [Full controls, CLI and safety reference](docs/REFERENCE.md)
- [GPLv3 license](LICENSE) · [Third-party notices](THIRD_PARTY.md)

GPU throughput and driver compatibility must be checked on the target host. Software fixtures and offscreen Qt tests do not establish hardware compatibility.
