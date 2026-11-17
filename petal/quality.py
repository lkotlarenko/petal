"""Bounded, local VMAF spot checks. Scores are review aids, never integrity proofs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
from typing import Callable

from .color import software_scale_color
from .media import probe, target_size, decoded_sizes
from .models import Media
from .process import Runner

MODEL = "vmaf_v0.6.1neg"
MODEL_4K = "vmaf_4k_v0.6.1neg"
ANALYSIS_VERSION = 3


def needs_analysis(report: dict) -> bool:
    return (not report or report.get("status") in {"Unavailable", "Cancelled", "Queued", "Analyzing"}
            or (report.get("analysis_version", 0) < ANALYSIS_VERSION and bool(report.get("geometry_note"))))


def model_for_size(size: tuple[int, int] | list[int]) -> str:
    return MODEL_4K if size[0] * size[1] > 2560 * 1440 else MODEL


def decoder_geometry(media: Media, runner: Runner) -> list[int] | None:
    """Inspect actual decoded frames, which may differ from container declarations."""
    sizes = decoded_sizes(media, runner)
    return list(sizes[0]) if sizes else None


def sample_windows(duration: float, seed: str) -> list[tuple[float, float]]:
    """Reproducible jitter in early/middle/late strata; never duplicate short clips."""
    if not math.isfinite(duration) or duration < 2:
        return []
    if duration < 12:
        return [(round(max(0, duration / 2 - 1), 3), 2.0)]
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    first, last = duration * .07, duration * .93
    band = (last - first) / 3
    return [(round(first + i * band + rng.uniform(.1, .9) * (band - 2), 3), 2.0) for i in range(3)]


def relative_video_start(media: Media) -> float:
    def seconds(value, default):
        try:
            result = float(value)
            return result if math.isfinite(result) else default
        except (TypeError, ValueError):
            return default
    origin = seconds(media.format.get("start_time"), 0)
    return max(0, seconds(media.video.get("start_time"), origin) - origin)


def vmaf_command(source: Media, output: Path, start: float, length: float,
                 output_media: Media | None = None) -> list[str]:
    w, h = (int(output_media.video["width"]), int(output_media.video["height"])) if output_media else target_size(source)
    pix = "yuv420p10le" if source.bit_depth == 10 else "yuv420p"
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-filter_complex_threads", "2"]
    for path, offset in ((source.path, relative_video_start(source)),
                         (output, relative_video_start(output_media) if output_media else 0)):
        args += ["-ss", f"{start + offset:.6f}", "-t", str(length), "-threads:v:0", "2",
                 "-protocol_whitelist", "file,pipe", "-i", str(path)]
    graph = (
        f"[0:{source.video['index']}]scale={w}:{h}:flags=lanczos{software_scale_color(source)},"
        # Accurate input seeking rebases timestamps to the requested window. Keep
        # those small frame offsets: resetting each first frame to zero can shift
        # an entire comparison by one frame after container timebase rounding.
        f"format={pix},setsar=1,settb=AVTB[ref];"
        # Normalize BOTH decoded pictures to the declared output canvas. Codec
        # alignment/padding or decoder crop behavior can differ from probe headers.
        f"[1:{output_media.video['index'] if output_media else 'v:0'}]"
        f"scale={w}:{h}:flags=lanczos{software_scale_color(output_media or source)},"
        f"format={pix},setsar=1,settb=AVTB[dist];"
        f"[dist][ref]libvmaf=model=version={model_for_size((w, h))}:log_fmt=json:log_path=/dev/stdout:"
        "n_threads=2:n_subsample=1:shortest=1:repeatlast=0:ts_sync_mode=nearest[quality]")
    return args + ["-filter_complex", graph, "-map", "[quality]", "-an", "-sn", "-dn", "-f", "null", "/dev/null"]


def analyze(source: Media, output: Path, seed: str, runner: Runner,
            progress: Callable[[dict], None] = lambda _: None) -> dict:
    result = probe(output, runner)
    durations = [v for v in (source.duration, result.duration) if v]
    if len(durations) != 2 or source.hdr:
        return {"status": "Unavailable", "reason": "VMAF checks require SDR with known duration", "model": MODEL}
    windows = sample_windows(min(durations), seed)
    if not windows:
        return {"status": "Unavailable", "reason": "Video is too short for a useful temporal sample", "model": MODEL}
    canvas = [int(result.video["width"]), int(result.video["height"])]
    source_decoded, output_decoded = decoder_geometry(source, runner), decoder_geometry(result, runner)
    geometry_note = ""
    if output_decoded and output_decoded != canvas:
        geometry_note = (f"Decoded output is {output_decoded[0]}×{output_decoded[1]}, "
                         f"while the container declares {canvas[0]}×{canvas[1]}. "
                         "The encoded frame size is inconsistent. Earlier resized comparisons cannot reliably measure compression quality. "
                         "Keep the original and try a validated H.265 configuration in a separate output folder.")
        return {"status": "Geometry mismatch", "reason": geometry_note, "geometry_note": geometry_note,
                "resolution": canvas, "analysis_version": ANALYSIS_VERSION,
                "geometry": {"source_decoded": source_decoded, "output_decoded": output_decoded, "comparison": canvas}}
    if not output_decoded:
        return {"status": "Unavailable", "reason": "No decoded output frames were available", "analysis_version": ANALYSIS_VERSION}
    scenes, all_scores = [], []
    for index, (start, length) in enumerate(windows):
        progress({"status": "Analyzing", "scene": index + 1, "scenes": len(windows), "start": start})
        data = runner.run(vmaf_command(source, output, start, length, result), timeout=180, nice=10)
        parsed = json.loads(data.stdout)
        scores = [float(frame["metrics"]["vmaf"]) for frame in parsed.get("frames", [])]
        if len(scores) < 8 or not all(math.isfinite(score) for score in scores):
            raise ValueError("Too few aligned VMAF frames for a meaningful quality estimate")
        scenes.append({"start": start, "duration": length, "frames": len(scores), "mean": round(fmean(scores), 2),
                       "p10": round(sorted(scores)[int((len(scores) - 1) * .1)], 2)})
        all_scores.extend(scores)
    mean = fmean(all_scores)
    p10 = sorted(all_scores)[int((len(all_scores) - 1) * .1)]
    worst = min(s["mean"] for s in scenes)
    # Heuristics, not universal perceptual thresholds; results never discard a file.
    review = mean < 90 or worst < 85 or p10 < 80 or bool(geometry_note)
    return {"status": "Review suggested" if review else "No concern in sampled scenes", "model": model_for_size(canvas),
            "mean": round(mean, 2), "p10": round(p10, 2), "worst_scene": round(worst, 2),
            "scenes": scenes, "resolution": canvas, "analysis_version": ANALYSIS_VERSION,
            "geometry": {"source_decoded": source_decoded, "output_decoded": output_decoded,
                         "comparison": canvas}, "geometry_note": geometry_note,
            "scope": "Encoding differences at output resolution; reference is scaled/rotated to match. "
                     "Does not measure the separate loss from downscaling. Samples do not cover the whole file.",
            "interpretation": "Review heuristics: mean <90, scene mean <85, or 10th percentile <80. "
                              "Scores depend on content, alignment and model; visual review remains useful."}
