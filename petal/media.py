from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path

from .models import Media, Settings, RESOLUTIONS
from .process import Runner


def probe(path: Path, runner: Runner) -> Media:
    result = runner.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format",
                         "-show_chapters", "-of", "json", str(path)])
    data = json.loads(result.stdout)
    media = Media(path, data.get("streams", []), data.get("format", {}), data.get("chapters", []))
    if not media.videos:
        raise ValueError("No playable video stream")
    return media


def display_size(media: Media) -> tuple[float, float]:
    v = media.video
    try:
        sar = Fraction(v.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        if sar <= 0:
            sar = Fraction(1)
    except (ValueError, ZeroDivisionError):
        sar = Fraction(1)
    width, height = int(v["width"]) * float(sar), float(v["height"])
    angle = media.rotation
    if not any(abs(angle - a) < 0.01 for a in (0, 90, 180, 270, 360)):
        raise ValueError("Non-right-angle rotation requires manual review")
    if abs(angle - 90) < .01 or abs(angle - 270) < .01:
        width, height = height, width
    if width <= 0 or height <= 0 or not math.isfinite(width + height):
        raise ValueError("Invalid video dimensions")
    return width, height


def target_size(media: Media, settings: Settings | None = None) -> tuple[int, int]:
    width, height = display_size(media)
    ceiling = RESOLUTIONS[settings.resolution if settings else "1080p"]
    if ceiling is None:
        max_w, max_h = int(width), int(height)
    else:
        long, short = ceiling
        max_w, max_h = (long, short) if width > height else ((short, long) if height > width else (short, short))
    factor = min(1.0, max_w / width, max_h / height)
    # Nearest even dimensions, bounded by the source display size and ceilings.
    # Keeping SAR below encodes the exact DAR even for odd-sized sources.
    w = max(2, min(int(width * factor / 2 + .5) * 2, int(width / 2) * 2, max_w))
    h = max(2, min(int(height * factor / 2 + .5) * 2, int(height / 2) * 2, max_h))
    if width < 2 or height < 2:
        raise ValueError("Video is too small for even-dimension encoding")
    return w, h


def target_sar(media: Media, settings: Settings | None = None) -> Fraction:
    width, height = display_size(media)
    w, h = target_size(media, settings)
    return Fraction(width / height * h / w).limit_denominator(10000)


def decoded_sizes(media: Media, runner: Runner) -> list[tuple[int, int]]:
    """Check decoded pictures independently of container/extradata dimensions."""
    result = runner.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                         "-select_streams", str(media.video["index"]), "-read_intervals", "%+#8",
                         "-show_frames", "-show_entries", "frame=width,height", "-of", "json",
                         str(media.path)], timeout=60, nice=5)
    return sorted({(int(f["width"]), int(f["height"])) for f in json.loads(result.stdout).get("frames", [])
                   if f.get("width", 0) > 0 and f.get("height", 0) > 0})
