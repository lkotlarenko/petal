# Changelog

## 0.5.0 — Native Rust app

- Replaced the maintained app, desktop UI, CLI and tests with native Rust. Removed the old runtime, GUI framework and freezing/packaging files.
- Simplified the README from the first-use experience through advanced details. Clarified that files stay local and nothing is uploaded.
- Added warning-confirmed **Move compressed originals to Trash** to the report. Full verification and unchanged full-file hashes are required; failed Trash never becomes permanent deletion.
- Used atomic no-replacement moves on the same filesystem. Cross-filesystem moves retain verified-copy recovery.
- Kept Jobs/Finished, saved reports, relative-folder organization, advanced media settings, hardware-only encoding and integrity checks.
- Added checked H.265 fallback when AV1 fails real output geometry checks.
- Cleaned registered incomplete move copies during crash recovery while retaining originals and unknown files.
- Added native AppImage icons, Rust CI, automatic version/release packaging and isolated artifact checks.
- Documented measured size/performance changes and interface differences in [Comparison](docs/COMPARISON.md).

## Earlier releases

The 0.4.0 and earlier release tags preserve their original code and notes for rollback. They are not maintained alongside the Rust app. See [GitHub releases](https://github.com/lkotlarenko/petal/releases).
