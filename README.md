<div align="center">

<img src="assets/petal.png" width="88" alt="Petal flower">

# Petal

**Make your videos smaller. Keep them on your computer.**

[Download for Linux](https://github.com/lkotlarenko/petal/releases/latest) · [How to use it](docs/USAGE.md) · [What changed in Rust](docs/COMPARISON.md)

</div>

Petal helps you save space in a large video collection. Choose your videos and a folder for the smaller copies, then let it work. It checks each new copy before keeping it.

**Nothing is uploaded.** Your videos stay on your computer. Originals stay in place unless you explicitly choose to move them.

![Petal desktop app](docs/screenshots/petal.png)

*The screenshot uses demonstration filenames and numbers.*

## Get started

1. Download the **AppImage** from [Releases](https://github.com/lkotlarenko/petal/releases/latest).
2. In its file properties, allow it to run as a program, then open it.
3. Choose a source folder and a separate output folder, or add videos to the app.
4. Click **Find videos**, review the list, then **Compress videos**.
5. Review the report when the batch finishes.

Start with a few videos and watch the results before processing a whole collection. **Balanced** is a useful starting point; **High Quality** keeps more detail, while **Maximum Savings** aims for smaller files. You can also choose the picture size. Smaller originals are not enlarged.

Petal is for Linux computers with supported AMD graphics. Your system needs FFmpeg and a working graphics driver. If setup is needed, see the requirements at the end of this page. Petal checks graphics support before encoding.

## After a batch

![Batch report](docs/screenshots/report.png)

The report shows how much smaller the successful copies are and how many files finished, were skipped, or need attention.

- **Move skipped originals** puts them in `output/skipped/`, keeping their folders and names.
- **Move failed originals** does the same in `output/failed/`.
- **Move compressed originals to Trash** keeps the smaller copies and moves their successfully compressed originals to your desktop Trash. It requires a warning confirmation and checks that both files still match the successful result.

Trash can be restored until you empty it. Moving originals to Trash does **not** immediately free their disk space, and Petal never empties Trash for you. If a file has changed or its compressed copy cannot be verified, its original is kept.

Moving skipped/failed files on the same filesystem is now an atomic move: Petal does not copy and reread the whole video. Moves between filesystems use a checked copy before removing the source. Existing files are never overwritten. [File organization and recovery](docs/ORGANIZING.md).

## Find things easily

**Jobs** contains waiting or interrupted work. **Finished** contains results. Both lists and the last report survive restarts. Search, filter, select all shown rows, or right-click a file to open it, show its folder, inspect details or return it to Jobs. Removing a row removes it from the list, not from your disk.

**Settings** contains the less common choices: video format, graphics device, verification, audio, extra tracks and sample comparisons. Picture-quality checks compare a few scenes and help you decide what to watch closely. Their scores are guides, not a promise about every frame. [Understanding picture checks](docs/QUALITY.md).

## A smaller Rust app

Petal is now written in Rust, including its interface and file handling. The AppImage no longer carries Python or Qt. The compression itself still uses your system's FFmpeg and graphics driver.

See the [measured comparison](docs/COMPARISON.md) for package sizes, file-move and scan timings, and the limits of those measurements. The Rust rewrite does not make the graphics encoder itself magically faster.

## Requirements and details

Petal targets current Linux x86_64 desktops. Official AppImages are built on Ubuntu 24.04. AMD Mesa/VA-API is the supported graphics path; other GPU vendors are not validated.

System requirements:

- **FFmpeg and FFprobe** for reading, compressing and checking media.
- A working **VA-API graphics driver** and access to a render device such as `/dev/dri/renderD128`.
- Standard desktop OpenGL/window-system libraries and a file-picker portal. X11 sessions also need the `libxkbcommon-x11` runtime library.
- **gio**, from GLib, for desktop Trash; `xdg-open` for opening files in other apps. If Trash is unsupported, Petal keeps the original and reports the problem. It never falls back to permanent deletion.
- Optional **libvmaf** in FFmpeg for scene-quality scores. Missing scores do not prevent normal integrity checks.

On Arch/CachyOS, install the system packages if they are missing:

```bash
sudo pacman -S --needed ffmpeg mesa libva-utils glib2 xdg-utils libxkbcommon-x11
```

Use your desktop's existing portal backend for file pickers. For a menu/dock icon, keep the AppImage in a permanent location and use **Settings → Add AppImage to applications**.

Optional checksum and command-line launch:

```bash
sha256sum -c Petal-<version>-x86_64.AppImage.sha256
chmod +x Petal-<version>-x86_64.AppImage
./Petal-<version>-x86_64.AppImage
```

If FUSE is unavailable:

```bash
APPIMAGE_EXTRACT_AND_RUN=1 ./Petal-<version>-x86_64.AppImage
```

AV1 and H.265 are the supported video formats. When AV1 fails the real output checks, Petal can use H.265 only after H.265 passes its own graphics check, and tells you which format was selected. Video encoding never silently switches to a CPU encoder. Unsupported HDR, interlaced video and unusual formats are skipped with explanations.

Audio is kept by default. The compatible-tracks setting preserves supported tracks, represents timecode as metadata and converts supported text subtitles. Strict preservation and explicit omission remain configurable. Full verification is the default; sampled verification checks fewer decoded sections but still checks full track timing.

<details>
<summary>Build, command line and maintenance</summary>

Rust 1.88+ and the system dependencies above are required to build. Use the locked dependencies:

```bash
cargo build --release --locked
cargo test --locked
./target/release/petal --help
./target/release/petal scan /path/to/Videos /path/to/Compressed
```

`Cargo.toml` contains the version floor. Successful main-branch builds select the next version automatically, test Rust, build and test a Rust-only AppImage, then create a GitHub release with a checksum. Pull requests run tests without creating releases.

- [User guide](docs/USAGE.md)
- [Architecture](docs/DESIGN.md)
- [Builds and releases](docs/RELEASING.md)
- [Verification results](docs/TEST_REPORT.md)
- [Python/Rust comparison](docs/COMPARISON.md)
- [Contributing](CONTRIBUTING.md)
- [GPLv3 license](LICENSE) · [Dependency notices](THIRD_PARTY.md)

</details>
