# Python 0.4 versus Rust 0.5

Measured locally on 2026-11-17. The baseline is the actual 0.4.0 release AppImage and its source. The Rust package is a stripped, zstd-compressed native release build. Raw measurements are in [comparison-results.json](comparison-results.json).

## Download and runtime

| Measurement | 0.4.0 | 0.5.0 local build |
| --- | ---: | ---: |
| AppImage bytes | 77,535,736 | 4,430,328 |
| AppImage size, MiB | 73.94 | 4.23 |
| Bundled interpreter / GUI | Python + Qt | Native Rust + egui |
| Rust executable bytes, before AppImage compression | — | 7,602,688 |

The local Rust AppImage is **94.3% smaller**. Official CI packages can differ slightly with compiler/toolchain and build-host versions. Both versions use system FFmpeg and drivers; neither package-size comparison includes installing those system dependencies. There is no claim of measured memory/startup improvement here: those were not benchmarked comparably.

## Organizing skipped originals

A 128 MiB synthetic file was moved three times per version on the same filesystem, with a fresh journal/output per trial. Setup and final SHA-256 validation were outside the timer. Each result was checked against the expected full-file hash, and the original path was confirmed gone.

| Same-filesystem move | 0.4.0 | Rust |
| --- | ---: | ---: |
| Median elapsed seconds | 0.8198 | 0.0283 |
| Method | Verified full copy, source hold/removal | Atomic no-replacement rename |

This fixture was about **29.0× faster** in Rust. The benefit comes mainly from changing the algorithm: a same-filesystem rename preserves the file/inode without copying its bytes. It is not a general language-speed claim. Rust timings include CLI startup; the baseline calls its existing mover directly. Synchronization/storage latency can vary substantially. Cross-filesystem moves still copy and fully verify data and are not represented by this speedup.

## Discovery

Thirty one-second synthetic video files, two sequential scans per version. “Cold” means an empty app metadata cache, not a flushed OS/disk cache. Rust timings include CLI startup and JSON output; the baseline uses its discovery API and SQLite cache in one process.

| Scan seconds | 0.4.0 | Rust |
| --- | ---: | ---: |
| Empty metadata cache | 2.5543 | 0.7343 |
| Unchanged files, warm cache | 0.0087 | 0.0041 |

Both versions cache metadata and limit probe concurrency. Rust avoids tool invocations and cache rewrites on unchanged warm scans. This is a small local fixture, not a prediction for every 500-file collection. Earlier runs under build load varied, including a slower Rust warm scan. No statistical confidence or guaranteed speedup is claimed.

## Compression and safety

Both versions invoke FFmpeg VA-API for encoding. Changing the orchestration language does not accelerate the hardware encoder itself; no matched encode-throughput benchmark was performed. Rust still overlaps one encoder and one verifier, limits background work and uses a low-priority scene-analysis worker.

Actual Rust host tests passed H.265 8/10-bit checks in all three profiles and a three-file compression/full-verification/resume batch. Original SHA-256 hashes stayed unchanged. AV1 reproduced the host's decoded/container geometry mismatch and was rejected; the checked H.265 fallback succeeded. This is a driver/output compatibility observation, not a diagnosis of a driver defect.

Rust retains no-overwrite outputs, safe cancellation, recovery journals, relative folders, saved Jobs/Finished/report, advanced codec/audio/stream/resolution controls and scene-quality checks. The new report Trash action requires full verification plus unchanged source/output full-file hashes. Same-filesystem moves preserve the original inode; cross-filesystem moves retain verified-copy checks.

## Interface differences and migration

The UI is newly implemented in native egui, so its controls/layout differ from the old table. Rust uses cards, search/filter and right-click actions rather than configurable table columns. It has colored scene scores, but does not include the old automatic profile recommendation banner or sample SSIM summary. Profile sample files remain available for direct visual comparison. Longer-video scene positions are fixed early/middle/late windows rather than the old seeded jitter. These are explicit differences, not claimed feature parity.

Settings/history/completion and held-source recovery have Rust migration readers. Old state is retained; old UI results alone do not authorize Trash. A legacy result must pass current verification and hashing first. The maintained source tree, tests and packaging contain no old implementation or interpreter requirement. The 0.4.0 source/tag/release remain available for rollback.
