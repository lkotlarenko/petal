# Architecture

Petal is a native Rust Linux application. FFmpeg and FFprobe remain separate system executables; media is processed locally. `Cargo.toml` defines the version floor and `Cargo.lock` pins dependencies. The AppImage contains one native executable, icons and dependency notices.

## Source layout

| File | Responsibility |
| --- | --- |
| `src/main.rs` | CLI, signals and command dispatch |
| `src/model.rs` | Settings, media/jobs, saved library and worker events |
| `src/ui.rs` | egui desktop interface, report, confirmation dialogs and desktop integration |
| `src/engine.rs` | Discovery/cache, graphics checks, pipeline, completion proofs and samples |
| `src/media.rs` | FFmpeg arguments, track/geometry/color/timing verification and VMAF |
| `src/files.rs` | Locks, synchronized state, safe moves, recovery and desktop Trash |
| `src/process.rs` | Subprocess pipes, timeouts, pause/cancellation and process-group cleanup |
| `tests-rust/native.rs` | Synthetic media and real filesystem regression tests |
| `assets/` | App icon source and raster asset |
| `packaging/` | AppRun, desktop entry and release notes |
| `scripts/` | Shell build, validation, version and packaging tools |
| `.github/workflows/` | CI and tested AppImage releases |

## Work scheduling

The UI owns its saved library and receives events from background work. It displays only visible rows in large lists. Blocking media/file operations run off the UI thread. One graphics encoder overlaps one verifier with bounded queued work; optional scene analysis has one lower-priority worker and at most three eligible files per batch. Stop/close propagates cancellation and joins workers.

Subprocesses receive argument vectors, not shell command strings. Each gets its own process group and a Linux parent-death signal. Cancellation/timeout terminates the group, escalating if needed. Pipe readers drain both streams; output and retained diagnostics have limits. Software decoding may retry while video encoding remains VA-API.

## Discovery and saved state

Two workers probe uncached files. Metadata caching uses executable and file identities, including inode, size, modification and change times. A warm scan avoids FFprobe and avoids rewriting unchanged cache entries. Discovery cache entries are advisory and never substitute for output verification.

Mixed inputs retain relative folders and stable source namespaces. Output naming avoids collisions. Symlink inputs, unsafe roots and unsupported media receive explicit handling. Library/settings and result reports survive restart through atomic JSON writes. Default locations are described in [Reference](REFERENCE.md).

Existing 0.4 settings/history and completion records can be read for migration. Old records are not trusted for destructive actions: full verification and full-file hashes are required before Trash. Legacy source holds are restored without overwriting files. Compatibility readers are Rust code; no legacy runtime is required.

## Keeping outputs and originals

Encoding writes a uniquely named, journaled partial. Savings are checked before expensive decoding. Geometry, streams, color/precision, duration/timeline and decoded content are validated before the final output is kept with a no-replacement hard link. Native completion proofs include source/output fingerprints, full SHA-256 hashes, effective settings and verification mode. A UI result alone is not a proof.

Same-filesystem organization uses a synchronized no-replacement rename. Cross-filesystem organization verifies a complete destination copy before removing a held source. Trash is explicit, warning-confirmed and delegated to desktop GIO; there is no permanent-delete fallback. Details and recovery boundaries are in [Organizing](ORGANIZING.md).

Hardware tests check real 8/10-bit encoded outputs and decoded geometry for the requested profile/size. Positive evidence is cached using tool, device, driver and setting identity. Original-size checks use a 1080p baseline; individual outputs still undergo validation. An accepted device check is not a guarantee for every possible input.
