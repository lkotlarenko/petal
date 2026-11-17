# Changelog

## 0.4.0

- Add a persistent batch report with saved space, colored result counts and links to filtered results.
- Add explicitly confirmed, fully verified moves of skipped/failed originals into output subfolders, with no overwrites, interruption recovery and safe cancellation.
- Separate Jobs and Finished, retain queued work and results across restarts, and add search, filters, list management, right-click file actions and optional columns.
- Recognize unchanged recorded completions when scanning older collections without encoding again.
- Refine the compact layout and empty states; show active-stage progress only when needed while retaining the full engine settings.
- Embed PNG icons and standard AppImage/icon-theme assets; add explicit desktop-menu integration.
- Fix failed-cache initialization leaking SQLite connections and recognize hardware configurations validated only at 10-bit.

## 0.3.1

- Use system Fontconfig in the AppImage to support newer Arch/CachyOS font configuration and avoid bundling an incompatible older configuration reader. Packaged GUI checks now reject Fontconfig warnings.

## 0.3.0

- Introduce readable picture-quality details with colored scores, per-scene bars, exact timestamps, player shortcuts and expandable diagnostics.
- Detect inconsistent decoded/container frame sizes before scoring; hide older misleading scores and reject new mismatched outputs before publication.
- Accelerate discovery with two bounded metadata readers and a persistent SQLite cache invalidated by file identity and FFprobe changes. Cache failures fall back to direct probing.
- Add AppImage packaging, packaged CLI/Qt smoke checks and GitHub Actions releases with automatic patch versions and checksums.
- Document installation, media policy, architecture, maintenance, release procedures and measured verification limits; include actual interface screenshots using demonstration data.

Earlier development included safe output publication, stream preservation, configurable resolution ceilings, drag-and-drop queues, hardware-check caching, overlapping encode/verification and automatic VMAF spot checks.
