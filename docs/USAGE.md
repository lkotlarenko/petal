# Using Petal

Choose a source folder and a separate destination folder. Neither can contain the other. Press **Scan folders** and review every row; invalid video candidates remain visible as scan failures. Still-image demuxers, audio-only files, symbolic links and non-media files are ignored. Files with unknown extensions are probed as well as common video containers.

Choose a quality profile. Open Settings if you want HEVC, Opus, a different render node, sampled verification or a savings threshold other than 5%. HDR and unsupported formats receive explicit skip messages. Maximum compatibility is the default: preserve cover art as MKV attachments, retain QuickTime timecode values in metadata, and convert MOV/plain-text subtitles to ASS. Each conversion is logged; subtitle styling can differ. Strict stream-copy mode remains available, as does explicit omission of otherwise unsupported streams.

Inspect a comparison sample before a large batch. Select exactly one playable row and open Settings → Compare selected video samples. The three outputs contain the same central excerpt. Compare motion and detail visually; sizes and SSIM appear in the results dialog and detailed log. The sample bytes are not extrapolated into promised full-file savings.

Press **Compress videos**. Double-clicking does not start concurrent workers. Use Pause after current to let the current output finish safely, or Stop safely to cancel immediately and clean its temporary file. Closing the window also cancels work and waits asynchronously for process shutdown and cleanup.

The table shows original size, source/target geometry, codec, status, output size and savings. Hover a row for full source/destination paths and the job explanation. The queue bar uses durations where known, otherwise file count. Verification is a separate stage. Unknown timing produces an unknown ETA instead of a fabricated prediction.

Restart, choose the same folders and scan again to reconstruct the queue. Start will validate matching recorded outputs instead of re-encoding them. Compression retains originals, including after success. Skipped files create no compressed output. Separately, the report can move skipped/failed originals after explicit confirmation; see [organizing files](ORGANIZING.md).

## Troubleshooting

**No render node / permission denied**

Run `ls -l /dev/dri` and `vainfo --display drm --device /dev/dri/renderD128` in your desktop session. Check the device's udev/group permissions and identify which render node belongs to the RX 7600. In Settings, select that node and rerun the hardware check. Do not run the GUI as root. Sandboxed development sessions may have no GPU access even when the host does.

**Encoder listed but no configurations pass**

Read `hardware-report.json` and the detailed log. The application requires actual hardware encode + decoded geometry tests, not just encoder-list presence. AV1 may fail while HEVC succeeds; select HEVC explicitly and run its samples. A missing CQP mode or unsupported format causes that configuration to remain unavailable. Petal never silently selects a CPU encoder.

**GPU decoding failed**

The log shows the failed command and software-decode retry. This retry still uses `av1_vaapi` or `hevc_vaapi`. Rotated video deliberately takes the software-autorotation path. If both pipelines fail, the job fails and its temporary output is removed.

**Skipped — no savings**

The completed file was too large or did not meet the configured percentage. This compares the entire output, including unchanged audio and container overhead. Already compressed videos or audio-heavy files may have little worthwhile saving. Choose a more aggressive profile only after reviewing samples, or retain the source.

**Destination already exists**

Petal never replaces an existing file. Matching saved state is checked against current source/output identities and reverified. If state is missing, output changed, or settings differ, the existing file is retained and the job is skipped. Use a different output directory if you want a new experiment.

**Verification failed**

Open the detailed log. Dimension, color, stream, timeline, truncated-container and decode errors reject the output. Strict mode is recommended for unattended batches. Sampled mode reduces decode work but does not provide the same corruption coverage.

**Disk full / read-only destination / no hard-link support**

The job fails without touching its source. Free space or choose a writable local filesystem such as ext4/Btrfs/XFS. Failed temporary cleanup remains in the state ledger for recovery. Hard-link publication has no unsafe replacement fallback.

**Incomplete files after a crash**

Reopen Petal with the saved folders, or scan the same source/output pair. Registered partials are removed under the output lock. Unregistered `.petal-*.partial.mkv` paths are logged and left untouched, because Petal cannot safely infer their ownership from a filename alone. Completed outputs are never cleanup targets.

**GUI will not launch**

On CachyOS, install `pyside6`, `qt6-wayland` and `qt6-svg`, then run `./launch.sh` from a terminal to see errors. Prefer native packages on KDE. The core `doctor`, `scan`, `sample` and `compress` commands do not require Qt. `QT_QPA_PLATFORM=offscreen ./scripts/test.sh` exercises Qt offscreen when PySide6 is available; it does not establish compositor-specific behavior.

## After the compatibility update

Restart Petal so the corrected FFmpeg commands are loaded, then scan the same folders. The old default strict stream policy upgrades once to Maximum compatibility. Check Settings → Incompatible streams if you previously selected a different policy. Start again; completed outputs with matching identity and unchanged effective stream handling are verified and reused. Files previously failed/skipped are reconstructed as pending by a fresh scan.

The repeated `Unrecognized option color_transfer:v:0` error was a command-generation bug, fixed by translating FFprobe’s `color_transfer` field to FFmpeg’s `color_trc` option. It was unrelated to GPU decoding. Auxiliary QuickTime timecode (`tmcd`) and embedded cover art caused many of the earlier safe-copy skips; maximum compatibility now handles those explicitly. Remaining skips include a specific reason in both the queue and log.

## Verification speed and automatic VMAF

Full verification decodes the complete output on the CPU. It now runs alongside the next GPU encode, with its own progress bar, up to four video-decoder threads, reduced scheduling priority, and a bounded temporary-file backlog. The GPU encoder remains VA-API even if a source needs software decoding. When verification is slower than encoding, the queue applies backpressure rather than filling the disk with unchecked outputs.

For a faster option, uncheck **Settings → Thorough verification**. Fast mode still checks metadata and actual packet endpoints, plus beginning/middle/end decoded samples. It has lower full-file corruption coverage; unchanged files that were already verified reuse their saved proof. Files without enough byte savings are discarded before expensive verification.

The default quality guard analyzes the first three eligible new successes with Netflix VMAF, using a separate low-priority CPU worker. Open **Quality details** for mean, low-percentile and worst-scene scores and scene positions. A review suggestion remains visible even if a later file scores well. These checks compare a scaled/rotated reference at output resolution, so they measure encoding differences separately from your requested resolution reduction. Missing libvmaf/models are reported as unavailable and do not fail an otherwise verified conversion. See [quality details](QUALITY.md).


## Add files or folders by dropping them

Drop local videos, folders, or a mixture into the window. The operation is copy-only: source paths remain untouched. Duplicates and symlinks are ignored. You can drop before choosing an output folder; videos appear immediately with planned relative paths, and Start waits for a destination. Selecting the destination through Choose rescans automatically. If typing the destination directly, press Scan folders.

Each dropped folder keeps its nested hierarchy beneath a stable folder name. Roots with the same name receive distinct suffixes. Existing folder-only queues keep their original relative paths when more locations are dropped. Adding a parent of an already-added folder retains the earlier folder's namespace, including after restart. Colliding basenames get distinct filenames; earlier allocated filenames stay stable. Folder inputs must not overlap the output tree. An explicitly selected file may use an output subfolder beside it, but cannot itself be inside output.

Drops during an active operation are staged and scanned afterward, avoiding mutation of an active batch. Dropped locations and naming prefixes are saved with settings. Restart, then Scan folders to rebuild the list and reuse matching completion records.

## Target resolution

Use Target beside Compression: 480p, 720p, 1080p (default), 1440p, 4K (2160p), or Original. Landscape/portrait/square ceilings adapt to orientation; source display aspect, rotation, and odd dimensions retain their existing safety rules. Nothing is upscaled. Original removes the ceiling, but each file can still fail if the hardware cannot handle its dimensions; its cached startup test is a 1080p baseline.

The profile's encoder-specific CQP value adjusts modestly to actual output pixel count. Smaller pictures get more detail protection; larger ones use a small savings-oriented offset. This is a provisional policy, not a promise that a particular resolution will save a given amount. The log records actual dimensions and quantizer, and sample comparison uses the selected target. Choose a separate output folder to compare settings for already completed files; Petal never overwrites them.

## Saved hardware validation

First setup runs real encoding/decoding tests. Later launches load their saved results without spawning FFmpeg tests. Evidence is invalidated when FFmpeg/FFprobe, driver, kernel, render-device identity/access, the processing graph, or target resolution changes. A new resolution is tested once; returning to a cached resolution reuses its matching evidence. Settings → Hardware check forces a fresh test. Interrupted registered temporary outputs in the selected destination are still recovered when cached checks are reused.

## Quality reports and recommendations

If an earlier quality report says Unavailable or Cancelled, Start retries eligible audits when reusing matching completed outputs. To request an audit directly, select rows and press Recheck selected. This verifies stored source/output fingerprints and analyzes the saved output without launching a video encoder. Both decoded inputs are normalized to the declared output canvas, eliminating mismatched VMAF input dimensions. Unexpected decoded/container geometry is reported for visual framing review.

Recommended for you appears only when measurements support a useful suggestion. Weak sampled quality can suggest High Quality; consistently strong samples with modest savings can suggest a smaller-file profile. Measured slow verification can suggest fast verification, with its reduced corruption coverage stated explicitly. Actions are optional and never re-encode existing outputs. During a batch they apply to future files after pending verification finishes. A profile change enables a fresh first-three audit budget. Samples remain estimates, not a quality guarantee for the entire collection.

## Frame size needs attention / decoded 1920×1082

If the encoded frames decode at 1920×1082 while the container declares 1920×1080, do not treat the earlier VMAF value as a trustworthy compression-quality score. Scaling that discrepancy during comparison can lower the score. Quality details now gives the framing warning priority and hides the score. New encodes with inconsistent decoded dimensions are rejected before they are published.

Keep your originals. Use **Open original** and **Open compressed** to inspect older outputs. A recheck audits an unchanged recorded output without encoding; it does not repair it. To compare another configuration, run the hardware check and try H.265 samples in a separate output folder. Petal will not overwrite old outputs. A specific driver cause cannot be established from the reported dimensions alone.

## Discovery cache

Folder scans use two metadata readers and cache unchanged metadata in `$XDG_CACHE_HOME/petal/discovery.sqlite3` (default `~/.cache/petal/discovery.sqlite3`). The detailed log reports elapsed time and cache hits/misses. Changed files or a changed FFprobe invalidate entries. Encoding and verification always read current media metadata independently of this cache. Cache errors are logged and direct probing continues.

For a cold scan, close Petal and remove only its discovery cache directory or database and SQLite sidecars. This disposable cache is separate from settings and resume/verification state. Do not remove the state directory if you want recorded completion proof retained.

## Batch reports and skipped/failed originals

After a batch, use its report to review saved space and outcome counts. Jobs and Finished are separate persistent lists. Right-click rows for file actions or use **Remove selected** to remove entries without deleting media. **Move skipped originals** and **Move failed originals** are explicit, confirmed operations that fully verify copies before removing originals. See [organizing and recovery](ORGANIZING.md) and [usability QA](UI_QA.md).

For an older collection, scan the original folders to recognize unchanged saved completions. Older skipped/error labels were not stored. Remaining Jobs may be explicitly skipped and organized through the report without encoding; do not assume every missing output was a failure.

## AppImage icon and application menu

Keep the AppImage in a permanent location and choose **Settings → Add AppImage to applications**, or run it with `--install-desktop`. This installs a user-level desktop entry and PNG icon; it does not require root. Repeat after moving or replacing the AppImage at a different path. AppImage file thumbnails depend on your file manager's thumbnail support, while menu/dock identification uses the installed `petal.desktop` entry.
