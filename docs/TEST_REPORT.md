# Rust 0.5 validation

Checked locally on 2026-11-17. Results describe this tested build/host, not every graphics driver or Linux desktop.

- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked --all-targets`: **26 passed**. Synthetic fixtures cover real AV1/H.265 encode/verify through test-only CPU adapters, truncated output rejection, geometry, audio/subtitle/timecode/color retention, optional real VMAF identity, cache/naming, 550 saved rows, no-overwrite, symlinks/traversal, cancellation/process-group termination, locks, atomic move recovery, checked cross-filesystem copy, interrupted-copy retry, recovery collision preservation and Trash proof/hash checks.
- Host GPU checks: H.265 High Quality, Balanced and Maximum Savings each passed 8/10-bit real encode/output checks. AV1 8/10-bit outputs disagreed with declared geometry and were rejected.
- Real GPU batch: three synthetic 640×360 videos with audio encoded using checked H.265 fallback, fully verified and saved. A second run reused all three results. All original SHA-256 hashes stayed unchanged.
- Rust AppImage: extraction, version, native move/Trash self-test, synthetic discovery, six icon sizes, menu integration, real desktop GUI startup/shutdown and extract-and-run passed in an isolated temporary home.
- Native demo screenshots reviewed for folder readability, list/report layout and original-versus-copy wording. Screenshots contain demonstration values.
- GitHub workflows checked with actionlint.

The test-only CPU encoder adapters let CI validate media verification without hardware; production video encoding remains VA-API. The actual cross-filesystem path was exercised locally because the workspace and temporary directory are on different devices. On CI hosts where those paths share a filesystem, that fixture exercises the same-filesystem route; simulated interrupted-copy recovery tests still cover journal handling.

GIO tests trash only synthetic files with an isolated temporary data directory. No user media or real user Trash is a test target. Scene scores require libvmaf; the test is conditional when that optional filter is absent. GUI smoke startup does not establish every compositor, portal or accessibility behavior. See [UI review](UI_QA.md) and [measured comparison](COMPARISON.md).

Recorded evidence: [Rust checks](validation/test-output.txt), [synthetic GPU batch](validation/gpu-smoke.txt), [comparison measurements](validation/comparison-results.json).
