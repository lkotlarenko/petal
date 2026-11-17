# Changelog

## 0.3.0

- Introduce readable picture-quality details with colored scores, per-scene bars, exact timestamps, player shortcuts and expandable diagnostics.
- Detect inconsistent decoded/container frame sizes before scoring; hide older misleading scores and reject new mismatched outputs before publication.
- Accelerate discovery with two bounded metadata readers and a persistent SQLite cache invalidated by file identity and FFprobe changes. Cache failures fall back to direct probing.
- Add AppImage packaging, packaged CLI/Qt smoke checks and GitHub Actions releases with automatic patch versions and checksums.
- Document installation, media policy, architecture, maintenance, release procedures and measured verification limits; include actual interface screenshots using demonstration data.

Earlier development included safe output publication, stream preservation, configurable resolution ceilings, drag-and-drop queues, hardware-check caching, overlapping encode/verification and automatic VMAF spot checks.
