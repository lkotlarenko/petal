# Verification report · Petal 0.4.0

Updated 2026-11-17.

**116 passed, 1 GPU test skipped; 117 tests in 76.647 seconds.** Run: `./scripts/test.sh`. Full output: [test-output.txt](test-output.txt). Local runtime: Python 3.14 and PySide6 6.12.0; packaging: PyInstaller 6.22.3. Hosted releases use Python 3.12 on Ubuntu 24.04.

## New behavior tested

- Completion report opens after the batch worker has stopped; its counts, independent snapshot and relocated paths persist after restart.
- Jobs and Finished filter and select the intended files, including a 550-row library and a search selecting its last row. Removing an entry leaves media untouched. Returning a stored result queues the selected file rather than unrelated files.
- Pending work survives restart; interrupted live rows restore as unfinished work. Scans recognize unchanged recorded completions without encoding again.
- The main interface fits 900×740 with usable table space and primary controls inside the window. Report links clear stale searches. Detailed engine configuration remains available.
- A complete source/copy move preserves file bytes, relative path, original extension and mtime. The local cross-filesystem test passes using different workspace/temporary filesystems.
- Collision, source changes, symlink/traversal, corrupted copy, disk-full, cancellation during final checks, replaced destination, source-directory failure immediately after rename and recovery conflicts retain/restore source data.
- Simulated crashes after source hold and after source removal recover correctly; retries use only verified journaled copies. Recovery never replaces a new file at the original path.
- Raster icons render and desktop integration quotes an AppImage path containing spaces and `%`. The package includes its root PNG, `.DirIcon`, six standard icon sizes and an explicit user-menu installer.
- Failed discovery-cache initialization closes its SQLite connection. Hardware readiness correctly recognizes a configuration validated only at 10-bit.

The prior compression tests also pass: real synthetic media, stream/color/geometry checks, VFR/timeline validation, no-clobber publication, interruption recovery, encode/verifier overlap, proof reuse and quality scheduling. Local FFmpeg/libvmaf tests include identity/blur discrimination, downscaling, real 4K-model operation and rejection of mismatched decoded geometry.

## Packaged and visual checks

The final local AppImage passes `scripts/smoke-appimage.py`: extracted CLI/version, real FFprobe discovery through the frozen child trampoline, offscreen native GUI/icon rendering and clean shutdown, root/icon-theme PNG assets, isolated desktop-entry installation, and direct extract-and-run without FUSE. No Fontconfig warnings are accepted by the packaged check.

Actual Qt screenshots with labeled demonstration data were visually inspected: [desktop](screenshots/petal.png), [compact](screenshots/compact.png), [report](screenshots/report.png), and [quality](screenshots/quality.png). Workflow YAML passes actionlint, the desktop entry passes desktop-file-validate, Python parses successfully, shell syntax passes and local documentation links resolve.

## Limits

No AMD render node is available here, so the actual-GPU test is skipped. The software encoder adapters exist only in tests and do not establish GPU throughput or driver compatibility. Native hardware checks remain mandatory on the machine doing compression. Ubuntu's FFmpeg may lack libvmaf; hosted VMAF tests explicitly skip where that optional filter is absent.

Offscreen checks do not establish every real compositor, display scaling factor or screen-reader interaction. File-manager AppImage thumbnails depend on desktop support; package assets and explicit menu integration are verified. Full SHA-256 readback and fsync protect relocation against detected copy errors but cannot guarantee against every hardware failure or hostile concurrent modification. See [organization and recovery](ORGANIZING.md) and [usability QA](UI_QA.md).
