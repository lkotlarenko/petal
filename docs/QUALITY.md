# Automatic quality guard

Petal uses Netflix's open-source [VMAF](https://github.com/Netflix/vmaf) through FFmpeg's `libvmaf` filter. No Python machine-learning package, cloud processing or model download is needed when the installed FFmpeg/libvmaf includes the built-in model. The chosen model is `vmaf_v0.6.1neg`, or `vmaf_4k_v0.6.1neg` above 2560×1440 pixels; its name is retained in each report. Model availability is tested by real analysis, with failures shown explicitly.

The first three **newly encoded, successfully verified** SDR files with known duration of at least two seconds are eligible in each batch. Reused completed outputs display successful persisted reports without repeating analysis; missing/failed/cancelled reports are eligible for retry. Recheck selected can audit matching saved outputs without encoding. You can disable automatic analysis in Settings or with `--no-auto-quality`.

For videos at least twelve seconds long, Petal selects one two-second window within each of three non-overlapping early/middle/late bands, avoiding the outer 7%. Positions are jittered with a source-fingerprint seed: varied across videos, reproducible for the same unchanged source. Short clips use one central window rather than several duplicate windows. Low-framerate/too-short samples that cannot yield enough aligned frames are reported as unavailable.

Before scoring, Petal checks decoded output frames against the declared output canvas. A mismatch (such as 1920×1082 decoded versus 1920×1080 declared) produces **Frame size needs attention**, with no similarity score. Resizing the mismatched encoded picture could manufacture a misleading low score. New encodes with this mismatch fail integrity verification before publication. Previously saved reports containing a geometry warning hide the old score and can be rechecked. Both streams are scaled to the common output canvas only after this geometry check. Reference frames are autorotated before scaling. They retain input range/matrix, and both streams are compared at common pixel precision. Accurate seek offsets and nearest-timestamp synchronization retain frame timing; there is no arbitrary movie framerate conversion. This follows the need for temporal alignment described in [Netflix's FFmpeg integration guide](https://github.com/Netflix/vmaf/blob/master/resource/doc/ffmpeg.md).

The result includes:

- Frame-weighted mean VMAF across sampled windows.
- The frame-score 10th percentile, to expose weaker portions hidden by a mean.
- The lowest scene mean and each scene's start/duration, frame count and score.
- Model, comparison resolution, measurement scope and heuristic interpretation.

The UI suggests review when mean is below 90, a scene mean below 85, or the frame-score 10th percentile below 80. These are conservative **application heuristics**, not Netflix-endorsed universal pass/fail thresholds. Scores depend on content, alignment, model and viewing conditions; see [Netflix's model documentation](https://github.com/Netflix/vmaf/blob/master/resource/doc/models_v0.md). A later good result does not hide an earlier review suggestion.

A score describes the analyzed frames, not the whole video. The reference is scaled to the chosen output resolution, so the score measures encoding differences at that resolution and does not assess the separate information loss from 4K→1080p resizing. VMAF does not replace color/precision, stream-preservation, synchronization or decode checks. A low score asks for visual inspection of those scenes; it does not automatically delete an output, change your profile or select a CPU encoder. Try High Quality through sample comparison or a separate output folder if you want to compare a new profile.

# Resource use

Encoding, integrity verification and optional quality analysis have separate roles:

| Stage | Resources | Bound |
| --- | --- | --- |
| Video encode | AMD VA-API; CPU decoding/scaling if needed | One encoder |
| Integrity verifier | CPU video decoding, up to 4 threads; reduced process priority | One verifier |
| VMAF guard | CPU; 2 metric threads, limited input/filter threads; still lower priority | One worker, at most 3 files per batch |

The encoder can work on the next file while the previous output is verified. Only one additional encoded file can wait behind the active verifier. Verification remains ahead of publication: a partial output is never presented as complete. Pause finishes/drains current encode/verification work; existing quality analyses may finish. Stop/close cancels all active processes and removes unpublished partials. A quality analysis cancelled after publication leaves the already verified file intact.

Full decode remains the default integrity mode. Uncheck **Full CPU decode** for metadata, full packet-timeline and sampled decode checks. Overlapping sample intervals merge, which avoids decoding short clips three times. Unknown durations escalate to full decoding. A new output below the savings threshold skips expensive verification immediately, because it will not be published.

Previously verified, unchanged outputs reuse their saved proof after fresh identity and metadata checks. A sampled proof upgrades once when strict verification is requested. Settings and quality reports use XDG storage and atomic, synchronized state updates. Logs include separate encoding and verification wall times; they do not claim a GPU speedup without host measurement.

Recommendation thresholds are application heuristics: at least two checks with mean ≥97, p10 ≥93 and worst scene ≥95 plus average savings below 25% may suggest the next smaller-file profile. Weak-quality advice takes priority; geometry mismatches prompt review. Verification advice requires at least two timed completions and measured verification time exceeding 1.25× encode time. Current-profile advice excludes known different codecs, actual output dimensions or quantizers. Nothing changes without clicking the suggestion.

## Reading Quality details

Select a checked video on the left. The large score is a VMAF similarity measure out of 100, not a percentage of original quality. Mint indicates no concern in the checked scenes; amber or rose asks for comparison. Scene bars show both the score and the exact time to find in your player. Open original / compressed launches your default player. Recheck runs against unchanged saved outputs without encoding again. Technical details expands the full report.

A framing warning takes priority over every score. Keep originals and existing outputs; compare framing, and use the sample comparison with a validated H.265 configuration and a separate output folder. Petal cannot diagnose a specific GPU/driver defect solely from a geometry mismatch.
