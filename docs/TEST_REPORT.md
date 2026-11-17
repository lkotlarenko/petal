# Verification report

Updated 2026-11-17 for Petal 0.3.0 release preparation.

## Automated checks

**88 passed, 1 GPU test skipped; 89 tests in 70.977 seconds.** Run: `PETAL_PYTHON=/tmp/petal-build-venv/bin/python ./scripts/test.sh`.

The release toolchain uses PySide6 6.12.0 and PyInstaller 6.22.3. Local checks use Python 3.14; GitHub Actions uses Python 3.12 on Ubuntu 24.04. The full local test output is retained in [test-output.txt](test-output.txt).

The suite covers command generation, geometry, discovery, filesystem safety, subprocess cancellation and parent-death cleanup; resolution policy and mixed dropped inputs; hardware/probe cache invalidation; native offscreen Qt controls; real FFmpeg encode/verify/publish/resume fixtures; controlled pipeline overlap/pause/stop; real VMAF scoring, downscaling and the 4K model; and release version selection. The actual-GPU test is skipped here because `/dev/dri` is unavailable.

## Regressions checked for this release

- Decoded 1920×1082 frames with declared 1920×1080 dimensions fail verification before publication, including metadata-only resume checks.
- An actual padded FFV1 fixture with simulated smaller container dimensions returns a geometry warning and no VMAF score.
- A legacy quality report containing a numeric score plus a geometry warning hides that score in the quality dialog, queue and tooltip. Clearing the report list clears the displayed panel.
- Real metadata scans reuse cached probe results, including video with an unknown extension; changed files invalidate the cache.
- A SQLite write failure does not turn a successfully probed video into a failed job.
- Two readers preserve discovery ordering and respect cancellation.
- Release versions advance monotonically, respect an increased source floor and remain stable on reruns of a tagged commit.

## Packaged application

A local x86_64 AppImage was built and checked with `scripts/smoke-appimage.py`. It passed:

- Version output from the extracted application.
- Real FFmpeg-generated fixture discovery through the frozen child-process entry point and system FFprobe.
- Offscreen Qt window rendering, bundled icon availability and clean shutdown.
- Direct AppImage extract-and-run without FUSE.

A shutdown crash found during packaged testing was addressed by disposing the Qt window before interpreter teardown. The older PySide6 6.10.1 pin also crashed while connecting Qt signals under the local Python 3.14 interpreter; the final toolchain uses 6.12.0 and reruns native tests. The older failing build is not published.

Workflow YAML passed `actionlint`; the desktop entry passed `desktop-file-validate`. Python compilation, shell syntax, package metadata/wheel construction and local documentation links were checked. Screenshots render actual Qt widgets with isolated XDG directories and explicit demonstration data; they do not expose user media or claim measured GPU results.

## Discovery measurement

A synthetic local collection of 30 small clips took **2.286 seconds cold** and **0.005 seconds warm** with 30 cache hits on the second scan. This establishes that repeat discovery avoids FFprobe calls. It does not establish speed on a large collection, a hard drive or a network filesystem.

## Hardware and interpretation limits

No AMD render device is available in this workspace. Software encoder adapters live only in tests and cannot establish VA-API quality, throughput or driver compatibility. Hardware encoding never falls back to a CPU encoder in production. Run hardware checks and representative visual sample comparisons on the target host.

The reported 1082/1080 discrepancy is detected and rejected, but its particular GPU/driver cause is not established here. Existing outputs and originals are retained; quality analysis does not repair or replace encoded media. Initial decoded-frame checks do not prove constant dimensions throughout every possible stream. VMAF checks cover short samples, compare at output resolution and remain visual-review aids.

Actual Wayland/X11 compositor integration, desktop scaling and GPU throughput are not manually validated by offscreen tests. CI's packaged smoke check runs before any release is published.
