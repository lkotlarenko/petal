# Usability and QA pass · 0.4

The normal path is add/choose → select output/profile → compress → review → optionally organize. Codecs, GPU device, verification, audio and stream policy remain configurable in Settings. Technical logs and full quality diagnostics remain available.

Native Qt checks cover 900×740 compact layout, usable table space, primary controls within the window, Jobs/Finished filtering, correct selection after filtering a 550-row library, search reset from report links, remove-without-delete behavior, returning a selected historical result without queuing unselected files, report presentation after the worker stops, persistent queued/history/report data, independent report snapshots, explicit move confirmation and restored moved locations.

Actual offscreen widgets are rendered in [desktop](screenshots/petal.png), [compact layout](screenshots/compact.png), [batch report](screenshots/report.png) and [quality details](screenshots/quality.png) screenshots with demonstration data. Idle encode/verifier bars are hidden. Empty lists explain the next action. Main controls use plain language; optional columns and file details retain technical information.

Safety tests inject corruption, destination collisions, disk-full errors, changed sources, cancellation during final checks, an ordinary error immediately after source rename, simulated crashes after hold/removal, symlink/path traversal attempts, destination replacement during final verification and recovery conflicts. A local test exercises different source/output filesystems when available. The existing compression/timeline/publication, VMAF and pipeline tests also run.

Packaged checks exercise real media discovery, GUI rendering and shutdown, raster icon resources, `.DirIcon`, standard icon sizes, isolated desktop-entry installation and extract-and-run without FUSE. Real desktop compositors, screen readers, every scaling factor and actual GPU throughput are not established by offscreen tests; hardware checks remain required on the target host.
