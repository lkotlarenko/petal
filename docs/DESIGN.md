# Implementation and tuning

The production pipeline is intentionally small:

```text
PySide6 view/model → background worker → discovery / capability checks / Batch
Batch → probe → stream plan → command generator → cancellable Runner
      → savings check → bounded CPU verification → no-clobber publication → state
                              ↘ optional VMAF worker (first 3 new successes)
```

`models.py` holds typed settings, jobs and structured probe metadata. `discovery.py` handles directory traversal and naming. `media.py` owns FFprobe and display geometry. `commands.py` is the only production encoder command generator. `process.py` drains both pipes with selectors and terminates process groups on cancellation or failure. A small fresh-interpreter `_child.py` trampoline establishes Linux `PR_SET_PDEATHSIG` before executing FFmpeg/FFprobe, so a hard application crash also stops its active subprocess without a threaded `preexec_fn`. `capabilities.py` collects runtime evidence. `safety.py` owns verification, locks, durable state and publication. `assets.py` extracts and cleans registered artwork temporaries; `color.py` carries frame/header color properties; `quality.py` performs VMAF sampling; `engine.py` schedules the bounded encoding/verification/quality stages. `gui.py` owns Qt views and signals, with no FFmpeg calls on the GUI thread. `__main__.py` provides CLI and desktop entry points.

Scan, hardware checks, comparisons, encode, timeline checks and full decode all run off the UI thread. The GUI receives Qt signals. Pausing waits on a condition variable after the current file; idle windows do not poll the GPU. Processes are drained without blocking on one pipe, diagnostic history is bounded, and generated argument arrays support spaces and Unicode.

## Evidence and tuning choices

Local FFmpeg reports `av1_vaapi`, `hevc_vaapi`, VA-API surface input, CQP and `scale_vaapi`. Local `vainfo` cannot open the requested render device because `/dev/dri` is absent in this execution environment. Encoder help is retained in `hardware-report.json`, but none of its advertised encoders are marked functional without a passing runtime encode, probe and full decode.

The [FFmpeg VA-API encoder documentation](https://ffmpeg.org/ffmpeg-codecs.html#VAAPI-encoders) describes hardware surfaces, CQP options, global quality, GOP/reference options and the driver-dependent speed/quality tradeoff. The [AV1 VA-API implementation](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/vaapi_encode_av1.c) uses AV1 quantization semantics; AV1 and HEVC values must not be treated as the same scale. [FFmpeg timestamp and rotation documentation](https://ffmpeg.org/ffmpeg.html) describes `fps_mode=passthrough`, input autorotation and filter time bases.

The AV1 candidates are 80 / 105 / 135, centered on the requested 105. HEVC candidates use its distinct QP scale, 20 / 24 / 29. They are provisional; no RX 7600 subjective quality, SSIM or throughput results are claimed. Real profile tests validate operability, not visual excellence. Use the sample comparison on representative content before trusting defaults for a library.

The hardware check tests each profile rather than assuming driver support from an encoder list. It checks 1920×1080 directly to catch padded/cropped AV1 paths that report 1920×1088 or otherwise expose incorrect display dimensions. Ten-bit tests are independent; a passing eight-bit test does not enable ten-bit encoding.

Reference structures, compression level, block-level rate control, low-power mode, tiles and asynchronous depth remain driver defaults. Not all advertised options are operational on all Mesa/hardware combinations, and there was no runtime evidence here for adding overrides. No AMF-only flags are used. A GOP limit of about four seconds based on average input FPS balances seeking and compression without changing timestamps or capping high-framerate content.

Sample comparison encodes the same central excerpt through all three profiles, verifies every sample, records complete bytes and encoding wall time, and computes FFmpeg SSIM against the scaled/rotated source reference when available. Samples remain available for visual inspection. SSIM is not a calibrated perceptual-quality target and downscaling is outside its comparison because both inputs are measured at output size. Metric failure is reported separately; it does not misrepresent a valid sample as measured.

## Deliberate limits

- HDR and wide-gamut preservation are refused until a tested metadata/precision pipeline is available. SDR 10-bit is enabled only by its own passing hardware test.
- Interlaced, alpha, unvalidated chroma formats, multiple real video streams and arbitrary-angle rotations require manual handling.
- Maximum compatibility preserves JPEG/PNG cover payloads as MKV attachments, represents `tmcd` starting timecodes in metadata, and converts MOV/plain-text subtitle tracks to ASS. The latter retains text/timing but may change styling. Strict copying and explicit omission remain selectable; all transformations are reported. Unhandled streams are never implicitly discarded.
- Strict full decode uses up to four video-decoder threads at reduced process priority, overlapping the next encode. One lower-priority VMAF worker uses two metric threads and bounded decoder/filter threads for the first three eligible new successes. Encoding speed and total wall time still differ; no host throughput increase is claimed without a measured GPU run.
- One GPU encode at a time. There can be one verifier and one additional encoded file awaiting that verifier; backpressure bounds temporary storage. Pause drains pending verification; stop cancels all active subprocess groups. State mutations and durable writes share a reentrant lock.
- Resume hashes are fast identity checks combined with metadata and current revalidation, not an adversarial whole-file authenticity scheme.
- Atomic no-clobber publication requires hard-link support in the destination filesystem. Unsupported filesystems fail safely.
- No AppImage, cloud service, media playback engine or background daemon is included.

## Compatibility regression coverage

FFprobe’s `color_transfer` field maps to FFmpeg’s `color_trc` option. BT.709 source fixtures set frame properties with `setparams` as well as encoder metadata, because encoder-context flags alone did not reliably populate primaries/transfer tags in this FFmpeg build. The hardware check uses tagged lossless H.264 inputs to exercise this path at both bit depths. Stream codecs are selected once per output stream, avoiding redundant global-copy/video-encode warnings.

Converted subtitle timing checks ignore empty MOV-text clearing packets and compare actual cue packet endpoints. Cover-art packets are excluded from the movie timeline and full movie decode. Attachment presence/codec is checked, and the integration fixture verifies that re-extracting the stored JPEG gives exactly the original bytes. Artwork temporaries use the same ledger, lock and recovery boundaries as partial video outputs. Existing strict completion records remain accepted under compatibility mode only when the source needed no stream conversions under the old policy.

## Hardware-surface and color regression fix

The reported HEVC failures involved FFmpeg inserting a software format conversion after a VA-API surface, then losing primary-color metadata on the software-decoding fallback. Output color/range/chroma options can constrain the automatically inserted output format filter. The command generator now avoids those output constraints, disables automatic output scaling, sets known source frame properties explicitly, and writes only those same known SDR properties into encoded sequence headers through `av1_metadata` or `hevc_metadata`. Software scaling explicitly preserves known range/matrix. This is metadata preservation alongside the existing SDR pipeline, not HDR conversion or permission to ignore failed verification. Unknown properties remain unknown.

The relevant primary references are [FFmpeg output format negotiation](https://github.com/FFmpeg/FFmpeg/blob/master/fftools/ffmpeg_filter.c), the metadata-only [setparams filter](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/vf_setparams.c), and [AV1/HEVC bitstream filters](https://ffmpeg.org/ffmpeg-bitstream-filters.html). Runtime hardware checks now exercise H.264 and HEVC inputs at both bit depths through the actual GPU decode/scaling graph. Unsupported decoder combinations use software decoding directly; encoder availability stays independently validated. A failed decoder format is not retried for every file in the same batch.

Resume records store their verification strength and optional quality report. Strict/sampled and automatic-quality settings do not change encoded-byte identity. A matching completed record reuses proof after fingerprint and metadata checks; sampled proof is upgraded once if strict verification is requested. Legacy settings hashes are recognized. No-savings results bypass expensive decoding because they cannot be published.


## Resolution, cached capability evidence and additive inputs (0.3)

`Settings.resolution` centralizes output ceilings. Geometry and command generation use the same settings in encoding and verification. CQP offsets are calculated from actual output area on each codec's own scale; the encoding identity contains effective dimensions/quantizer. Changing ceilings without changing the resulting output can reuse matching proof. Legacy default-1080p records remain recognized. Quality comparison uses the output's declared canvas, symmetric scaling, explicit stream indices, and separate decoded-geometry diagnostics.

`hardware_cache.py` stores atomic capability reports keyed by device/resolution and a signature of executable/driver/device identity, access, kernel and processing code. No subprocess runs on a valid cache hit. Explicit doctor/manual checks refresh evidence. New ceilings require a real smoke check; Original tests a 1080p baseline and individual files still undergo normal validation.

Mixed local input discovery deduplicates canonical paths, reserves output names, retains stable per-root namespaces, rejects unsafe output relationships, and ignores symlinks. Namespace insertion order keeps earlier root ownership when a parent is added later. A destination-specific selection ledger allows additive scans and resumed mixed queues; a normal folder's records migrate when it becomes a mixed queue. Qt drop operations force CopyAction. Busy drops are staged until the worker shuts down.

`recommend.py` derives explainable suggestions from measured job quality, savings and encode/verify times. Completed rows retain actual encoding settings. A clicked profile/verification action enters a synchronized scheduler request; pending verifications drain under their original settings before the replacement settings become active. Integrity proof, publication and optional analysis remain separate. Early corruption errors are retained even after the bounded stderr tail fills.

## Discovery and quality presentation

`scan_cache.py` owns a disposable SQLite metadata cache with a serialized connection. Cache identity includes device/inode/size/mtime/ctime and FFprobe executable identity. Two readers overlap media probing with at most four pending tasks; ordered results preserve stable naming. Cancellation is shared with every subprocess. Source encoding and output verification always probe afresh; cached discovery metadata is never an integrity proof. Cache initialization/read/write failures leave direct probing available.

`quality_ui.py` presents quality reports independently of the encoder and measurement code. Geometry warnings take priority over scores, including old persisted reports. New output verification decodes initial frames independently of container metadata and rejects inconsistent sizes. This bounds detection to inspected frames; it does not prove constant dimensions throughout an arbitrary stream.

## Packaging boundaries

`packaging/` holds the PyInstaller specification, launcher, desktop entry, dependency pins and license texts. `scripts/` holds local build, screenshot, version-selection and packaged-smoke tools. `.github/workflows/` orchestrates test and release jobs. Source version metadata has one authority in `petal/__init__.py`.

Frozen media subprocesses re-enter Petal through a private child switch, establish parent-death/cancellation behavior and restore the original system library search path before executing host FFmpeg/FFprobe. This avoids treating the packaged executable as a Python interpreter and avoids leaking bundled Qt libraries into system tools. The hardware evidence signature includes source files shipped as data alongside the frozen modules.
