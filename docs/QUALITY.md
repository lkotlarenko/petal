# Understanding picture checks

Picture checks help you decide which scenes to watch closely. They run locally through your installed FFmpeg/libvmaf. Missing models or filters are reported as unavailable; ordinary integrity verification still runs.

The first three eligible new successful SDR videos in a batch can be analyzed. Videos need known duration and enough matching frames. Longer clips use two-second windows at 15%, 48% and 81% of the shared duration. Short clips use one central window. These sampled positions do not cover the whole video.

The quality window shows a colored overall VMAF score, low-percentile score, weakest scene and scene bars. It links to the original/encoded copy and exposes technical details. Scores are similarity measurements, not a percentage of original quality.

Review is suggested when the mean is below 90, a scene mean below 85 or the frame-score tenth percentile below 80. These are Petal heuristics, not universal pass/fail thresholds. The model is `vmaf_v0.6.1neg`, or its 4K variant for larger frames.

Decoded output geometry is checked before scoring. A disagreement such as decoded 1920×1082 versus declared 1920×1080 makes the score unavailable; a new encode is rejected by verification. Resizing a malformed picture must not conceal its geometry problem. Input changes during scoring discard the result.

The reference is rotated/scaled to the output canvas with timing-aware seeks and nearest-frame synchronization. Scores therefore describe encoding differences at that resolution. They do not measure the separate detail loss from choosing a smaller picture size. Low scores call for watching those scenes; they do not automatically remove the output or change your settings.

## Integrity versus visual quality

Integrity checks cover output structure, tracks, geometry, color/precision, duration, packet timing and decoded content. Full decoding is the default. Sampled mode checks decoded sections and full packet timing with less corruption coverage. A good VMAF result cannot replace these checks.

One low-priority analysis worker runs separately from the graphics encoder and integrity verifier. Cancelling analysis after a copy was verified leaves that copy intact. The current Rust sample tool produces three profile clips for visual comparison; it does not display the old sample SSIM summary or automatic profile recommendations. See [migration comparison](COMPARISON.md) for remaining interface differences.
