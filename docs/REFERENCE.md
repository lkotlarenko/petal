# ✿ Petal

A native Linux batch video compressor built with Python, PySide6, FFmpeg and AMD VA-API. Choose separate source/output folders, scan the collection, review the queue, then compress. Successful outputs retain the directory hierarchy. Originals stay in place and are only opened for reading.

**Validation status:** software media/safety integration tests and native offscreen Qt smoke tests run in this workspace. `/dev/dri` is unavailable here, so local GPU checks remain blocked; the user has reported successful host encodes. Petal requires passing hardware checks on the machine doing the compression. See [test results](TEST_REPORT.md).

## Install on CachyOS / Arch

Use native distribution packages first:

```bash
sudo pacman -Syu --needed python pyside6 ffmpeg libva-utils mesa qt6-wayland qt6-svg
git clone https://github.com/lkotlarenko/petal.git
cd petal
./launch.sh
```

The Arch package is [`pyside6`](https://archlinux.org/packages/extra/x86_64/pyside6/). Mesa supplies the [AMD VA-API backend](https://archlinux.org/packages/extra/x86_64/mesa/); `vainfo` is supplied by [`libva-utils`](https://archlinux.org/packages/extra/x86_64/libva-utils/). No AMF SDK, browser, server or cloud account is needed. The application does not need microphone/audio-device access; PipeWire settings are unaffected.

Alternatively, for an isolated Python installation:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./launch.sh
```

FFmpeg, FFprobe, the VA-API driver and render-node permissions are still system requirements. The launcher uses the virtual environment only if it contains PySide6, otherwise system Python. Use a Python version supported by the installed PySide6 release.

Optional KDE application-menu entry, installed for your user:

```bash
python3 scripts/install-desktop.py
```

The entry references this checkout. Keep the project at that path, or rerun the installer after moving it. No installation action is performed automatically.

## Before a whole library

1. First setup validates hardware in the background. Later launches reuse saved evidence; driver, FFmpeg, device or target-resolution changes trigger a new check. Settings → Hardware check always forces a fresh test.
2. Confirm `/dev/dri/renderD128` resolves to the RX 7600 at PCI `0000:03:00.0`. The hardware report includes the PCI identity when sysfs is available.
3. Select source and destination folders with no overlap, then **Scan folders**. Review all jobs and any scan failures.
4. Select one representative SDR video, open **Settings → Compare selected video samples**, and choose a folder outside the source tree. Petal encodes three identical middle excerpts, verifies them, measures sizes/times and SSIM, and retains the samples for visual review.
5. Inspect the samples in your preferred video player. Check motion, texture, dark gradients and fine detail. Then start a small batch and examine its results before processing hundreds of videos.

The default profile values are **provisional starting points**, not a measured RX 7600 quality recommendation. No hardware quality or speed measurements were possible in this workspace. SSIM is an additional signal, not a perceptual-quality guarantee; it compares the encoded sample to a reference scaled to the same output dimensions.

## Controls and behavior

- **Start compression:** one GPU encode overlaps one bounded CPU verifier. Completed files are published only after verification. Unchanged recorded files reuse their saved proof after current fingerprints and metadata are checked.
- **Pause after current / Resume queue:** drains the current encode and pending verification, then waits without polling. Optional analysis of already completed files may finish in the background.
- **Stop safely:** terminates the active process group, cleans its temporary output and cancels remaining waiting jobs. Start resumes cancelled jobs.
- **Retry failed:** queues failed files again. Existing completed files are verified and retained.
- **Skip / restore selected:** toggles selected pending/skipped rows while idle. During a batch, it requests skipping selected waiting jobs; it cannot interrupt or restore the active file.
- **Drag and drop:** add local video files or folders anywhere in the window. Folder hierarchy is retained under stable source-folder names. Drops during processing are staged until the operation finishes; dropping never moves originals.
- **Target resolution:** 480p, 720p, 1080p, 1440p, 4K or Original. Portrait ceilings rotate and smaller sources stay small. Quantizers adapt to actual output dimensions.
- **Recommended for you:** measured quality, savings and verification times drive optional one-click suggestions. Changes apply to subsequent files after pending verification drains.
- **Settings:** AV1 or H.265 VA-API, profile, render node, verification mode, minimum savings, original/Opus audio, and maximum-compatibility / strict / explicit-omission stream policies.
- **Detailed log:** bounded on screen; rotating technical logs and the hardware report live in the XDG state directory.

Compression publishes **worthwhile, verified compressions**. Skipped files are not copied automatically. The batch report offers separate, explicit moves of skipped/failed originals into `skipped/` and `failed/`. This is not a complete backup tool.

Unknown source durations use file-count progress and no ETA. Known durations weight aggregate progress. Current encoding progress, FPS and speed come from FFmpeg's structured `-progress` output. Full decode verification has its own progress bar, so it does not overwrite the current encoding display. The first three eligible new conversions receive automatic low-priority VMAF spot checks and a separate quality report. ETA is approximate encoding time plus an unestimated verification phase.

## Quality and media policy

| Profile | AV1 CQP `global_quality` | HEVC CQP `qp` | Intended tradeoff |
| --- | ---: | ---: | --- |
| High Quality | 80 | 20 | More retained detail; larger files |
| Balanced (default) | 105 | 24 | Quality-oriented starting point |
| Maximum Savings | 135 | 29 | Smaller files; detail/gradient degradation is possible |

The table gives the **1080p baseline**. Automatic offsets by actual output pixel count are AV1 −12/−6/0/+4/+8 and HEVC −2/−1/0/+1/+2 for up to 480p/720p/1080p/1440p/4K-or-larger respectively. A 4K ceiling does not change the quantizer for an unchanged 720p source. These modest offsets are provisional heuristics, not hardware-calibrated visual-quality guarantees.

These scales are different; neither is SVT-AV1 RF. Lower values target better quality. Profiles use CQP only after that exact codec/profile/bit-depth combination passes a real hardware encode and full decode. GOP length is approximately four seconds at the reported average frame rate; this does not impose a frame-rate conversion. References, tiles, AQ and compression level stay at driver defaults instead of assuming AMF-specific controls. See [design and tuning notes](DESIGN.md).

- At the default 1080p ceiling, landscape fits 1920×1080; portrait fits 1080×1920; square fits 1080×1080. Other presets use the same orientation rules; Original removes the size ceiling. Smaller display dimensions are not enlarged. Rotation and sample aspect ratio participate in the calculation. Even coded dimensions plus a near-unity sample aspect ratio preserve exact display aspect for odd sizes.
- Progressive SDR 8-bit and supported 10-bit 4:2:0 are supported. The 10-bit hardware path must pass its own test.
- **HDR10, HLG, Dolby Vision, wide-gamut metadata, interlaced video, unusual rotations, alpha and unvalidated chroma formats are skipped with explanations.** Reliable HDR preservation was not validated, so there is no automatic HDR conversion or tone mapping.
- Framerate is passed through, including VFR timestamps. No universal FPS cap, interpolation, sharpening, denoising, cropping or padding is applied.
- GPU decode/scaling is tried first for unrotated input. A failure retries software decode/scaling with hardware upload and the **same hardware encoder**. Rotated sources use software autorotation before hardware upload. CPU **encoding is never a fallback**.
- Audio is copied by default, with all selected tracks, languages and channel counts. Optional Opus uses 96 kbit/s mono, 160 stereo, or 64 kbit/s/channel up to 512 kbit/s for supported multichannel layouts. It never requests a downmix. Lossy audio conversion is optional and may itself increase some files.
- **Maximum compatibility (default):** supported MKV tracks and attachments are retained. JPEG/PNG cover art is preserved as an MKV attachment without recompression. QuickTime `tmcd` tracks become `PETAL_TIMECODE_STREAM_<index>` metadata retaining the starting timecode; the original timecode track representation is replaced and the conversion is logged. MOV/plain-text subtitles become ASS with text and cue timing retained; styling may differ. Video and audio tracks are kept.
- Unsupported streams outside these conversions still cause a clear skip. Select **Skip file and explain** for strict stream-copy compatibility, or **Explicitly omit incompatible streams / cover art** if you authorize dropping unsupported streams. Every conversion/omission is reported in the log and job result. Multiple real video angles and unvalidated HDR still require manual handling. Legacy default strict settings migrate once to maximum compatibility; existing completed records for files needing no conversion remain reusable.

## Output safety and recovery

1. Reject equal, nested or parent/child source/output roots, including aliases resolved through symlinks. Do not traverse symlinked source directories or files. Refuse symlinked destination subdirectories.
2. Resolve naming collisions before encoding. Normally `folder/video.mp4` becomes `folder/video.mkv`; conflicting basenames receive an extension and deterministic hash suffix. Long Unicode filenames are shortened safely.
3. Encode to a UUID-named hidden `.petal-….partial.mkv` in the final directory, using FFmpeg `-n` and argument arrays. No shell is used.
4. Check FFmpeg status and corruption warnings, then FFprobe streams, codec, dimensions, display aspect, bit depth, color labels, stream counts, copied codecs, track metadata, dispositions, channels, chapters and duration. Compare actual packet endpoints and relative stream offsets, so duration tags alone cannot disguise truncation.
5. Reject no-savings outputs before spending CPU on decoding. Strict verification (default) fully decodes output video/audio with error detection using up to four video decoder threads. It overlaps the next GPU encode. The lighter setting decodes beginning/middle/end samples (overlapping intervals are merged) and still checks complete packet timelines. Unknown durations automatically require full decoding. Neither can prove perceptual quality or detect every possible bitstream defect.
6. Recheck source identity. Compare complete output bytes, including audio and container overhead. Reject equal/larger outputs and outputs below the default 5% savings threshold.
7. Flush the file, atomically create a hard link at the final name without replacement, sync the directory, remove the temporary name and sync again. If the filesystem cannot hard-link or sync correctly, publication fails; there is no overwriting fallback.
8. Persist a resume record atomically. Source/output identity uses size, inode, modification time and first/last MiB hashes. Matching records are checked against current metadata and reuse their stored verification proof without another full decode. Switching from sampled to strict verification upgrades that proof once. A filename alone never establishes completion.

An advisory lock prevents cooperating Petal processes from sharing an output tree. A Linux parent-death guard kills the media subprocess if the application crashes. Interrupted temporary paths are recorded before encoding. On startup with saved valid folders, or when the output is scanned/used again, only registered temporary files in that tree are removed under the lock. Unregistered partials are identified in the log and left untouched for manual inspection. Samples use the same temporary/publication/recovery mechanism. Extracted artwork uses registered `.petal-<uuid>.asset.jpg` / `.asset.png` files, which are also cleaned after use or recovered after interruption.

An existing final output is **never replaced**, even if state is absent or settings changed. A crash between publication and state persistence may leave a valid final output without a matching resume record; Petal retains it and reports a skip. Keep state alongside the collection's use of Petal if automatic resume matters.

XDG defaults:

```text
~/.config/petal/settings.json
~/.local/state/petal/queue-<source-and-output-hash>.json
~/.local/state/petal/hardware-report.json
~/.local/state/petal/petal.log  (rotated)
```

Filesystem full, permission, subprocess, verification and GPU failures appear on the affected job and in the log. Completed files are not exposed while encoding. Compression never deletes, renames or overwrites a source. The separate, explicitly confirmed organization action verifies a complete copy before removing a skipped/failed original; see [organization and recovery](ORGANIZING.md).

## CLI and tests

The core CLI and tests do not require Qt:

```bash
./launch.sh doctor --report /tmp/petal-hardware.json
./launch.sh scan /path/to/Videos /path/to/Compressed
./launch.sh sample /path/to/video.mkv /path/to/samples
./launch.sh compress /path/to/Videos /path/to/Compressed
./launch.sh compress /path/to/Videos /path/to/Compressed --codec hevc_vaapi
./scripts/test.sh
./scripts/test.sh --gpu
```

`doctor` performs real 1920×1080 encodes and full decodes for both encoders, three profiles, and 8/10-bit inputs. Output coded/display dimensions must be correct. `--gpu` opts into the actual-device test and fails if Balanced AV1 does not work. PySide6 tests run offscreen when available; otherwise they are explicitly skipped. Set `PETAL_PYTHON=.venv/bin/python` for tests using a virtual environment.

Hardware checks use short tagged H.264/HEVC fixtures (`libx264`/`libx265`) to exercise both encoding and GPU decoding/scaling at 8/10 bits; library encoding remains VA-API only.

Most FFmpeg integration tests use clearly isolated **CPU AV1/HEVC test adapters** to test real media, timestamps, mapping, cancellation and publication without a GPU. This adapter exists only in `tests/`; it does not establish that VA-API works and is not accessible to the application.

See [test report](TEST_REPORT.md) for what was actually run and [usage/troubleshooting](USAGE.md) for common failures.

## Automatic quality guard and faster verification

VMAF checks the first three **new, successfully verified SDR conversions** with a known duration of at least two seconds. Long videos get three reproducibly jittered two-second windows from early/middle/late sections; short videos get one window. One low-priority analysis worker runs independently of encoding and verification. Results persist with the job record and appear under **Quality details**, with mean score, 10th percentile, worst scene and exact sample positions. Low scores suggest visual review; they do not replace integrity verification. See the [quality/performance guide](QUALITY.md) for model assumptions and thresholds.

For quicker integrity checks, uncheck **Settings → Thorough verification** or pass `--sampled-verification`. This retains metadata, complete packet-timeline and sampled decode checks, with less corruption coverage than full decode. Full mode remains the default, now overlapping GPU encoding. `--no-auto-quality` disables optional VMAF work. Normal batch processing remains local and keeps one GPU encoder at a time.
