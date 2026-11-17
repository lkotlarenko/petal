from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import re
import os
from pathlib import Path
import stat
from typing import Callable
import uuid
import threading
from functools import wraps

from .commands import decode_command, stream_plan
from .media import display_size, probe, target_size, decoded_sizes
from .models import Media, Settings
from .process import Runner


class VerificationError(RuntimeError):
    pass


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return locked


def packet_timeline(path: Path, runner: Runner, *,
                    empty_movtext: set[int] | None = None) -> dict[int, tuple[float, float]]:
    """Read actual packet endpoints, not duration tags left in a truncated container."""
    spans: dict[int, tuple[float, float]] = {}

    def consume(line: str):
        fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        try:
            index = int(fields["stream_index"])
            pts = float(fields["pts_time"])
            if empty_movtext and index in empty_movtext and int(fields.get("size", 0)) <= 2:
                # MOV text has empty clearing packets; ASS represents cue ends in event durations.
                return
        except (KeyError, ValueError):
            return
        try:
            duration = float(fields.get("duration_time", 0))
        except ValueError:
            duration = 0
        if not math.isfinite(pts + duration):
            return
        old = spans.get(index, (pts, pts + duration))
        spans[index] = (min(old[0], pts), max(old[1], pts + max(0, duration)))

    runner.run(["ffprobe", "-v", "warning", "-protocol_whitelist", "file,pipe", "-show_packets", "-show_entries",
                "packet=stream_index,pts_time,duration_time,size", "-of", "compact=p=0", str(path)],
               timeout=None, line_callback=consume, nice=5)
    return spans


def fingerprint(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Source or output is no longer a regular non-symlink file")
    info = path.stat()
    digest = hashlib.sha256()
    digest.update(f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}".encode())
    with path.open("rb") as file:
        digest.update(file.read(1024 * 1024))
        if info.st_size > 1024 * 1024:
            file.seek(max(0, info.st_size - 1024 * 1024))
            digest.update(file.read(1024 * 1024))
    return digest.hexdigest()


def safe_parent(destination: Path, root: Path) -> None:
    root = root.resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Destination escaped output root")
    current = root
    root.mkdir(parents=True, exist_ok=True)
    for part in destination.parent.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symlinked destination directory rejected: {current}")
        current.mkdir(exist_ok=True)
    if destination.parent.resolve() != destination.parent:
        raise ValueError("Destination parent changed through a symlink")


def new_temporary(destination: Path) -> Path:
    # FFmpeg creates this with -n. UUID prevents other jobs from claiming the same name.
    return destination.parent / f".petal-{uuid.uuid4().hex}.partial.mkv"


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(temporary: Path, destination: Path) -> None:
    """An atomic hard link never replaces a preexisting destination (including symlinks)."""
    if temporary.parent != destination.parent or not temporary.is_file() or temporary.is_symlink():
        raise ValueError("Publication requires a regular temporary file in the destination directory")
    with temporary.open("rb") as file:
        os.fsync(file.fileno())
    os.link(temporary, destination, follow_symlinks=False)
    sync_directory(destination.parent)
    temporary.unlink()
    sync_directory(destination.parent)


def enough_savings(original: int, output: int, percent: float) -> bool:
    return original > 0 and 0 < output < original and output <= original * (1 - percent / 100)


@contextmanager
def output_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".petal.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Petal process is using this output directory") from None
        yield
    finally:
        os.close(fd)


def verify(source: Media, output: Path, settings: Settings, runner: Runner, *,
           expected_duration: float | None = None,
           progress: Callable[[dict[str, str]], None] | None = None,
           metadata_only: bool = False) -> Media:
    result = probe(output, runner)
    plan = stream_plan(source, settings)
    expected = plan.streams + plan.artwork
    def stream_kind(stream):
        if (stream.get("disposition", {}).get("attached_pic")
                or (stream.get("codec_type") == "attachment"
                    and stream.get("tags", {}).get("mimetype", "").startswith("image/"))):
            return "artwork"
        return stream.get("codec_type")
    if len(result.videos) != 1 or result.video.get("codec_name") != {"av1_vaapi": "av1", "hevc_vaapi": "hevc"}[settings.codec]:
        raise VerificationError("Missing video or unexpected output codec")
    if (int(result.video["width"]), int(result.video["height"])) != target_size(source, settings):
        raise VerificationError("Unexpected coded output dimensions")
    actual = decoded_sizes(result, runner)
    expected_size = target_size(source, settings)
    if not actual or any(size != expected_size for size in actual):
        raise VerificationError(f"Decoded frame dimensions {actual or 'unavailable'} do not match {expected_size}. "
                                "The encoder/driver produced inconsistent geometry. Try a validated H.265 VA-API configuration; originals and existing outputs are retained.")
    a, b = display_size(source), display_size(result)
    if abs((a[0] / a[1]) / (b[0] / b[1]) - 1) > .002 or result.rotation:
        raise VerificationError("Output display aspect ratio or orientation changed")
    if result.bit_depth != source.bit_depth:
        raise VerificationError("Output bit depth changed")
    if Counter(stream_kind(s) for s in result.streams) != Counter(stream_kind(s) for s in expected):
        raise VerificationError("Expected stream counts were not preserved")
    for before, after in zip(expected, result.streams):
        kind = stream_kind(before)
        if kind != stream_kind(after):
            raise VerificationError("Stream ordering changed")
        if kind == "artwork":
            if after.get("codec_name") and before.get("codec_name") != after.get("codec_name"):
                raise VerificationError("Cover-art codec changed")
            continue
        if kind != "video" and not (kind == "audio" and settings.audio == "opus"):
            codec = plan.subtitle_codecs.get(before["index"], before.get("codec_name"))
            if codec != after.get("codec_name"):
                raise VerificationError("Copied stream codec changed")
        for tag in ("language", "title", "filename", "mimetype"):
            value = before.get("tags", {}).get(tag)
            if value and value not in ("und",) and after.get("tags", {}).get(tag) != value:
                raise VerificationError(f"Stream {tag} metadata changed")
        for flag in ("default", "forced", "hearing_impaired", "visual_impaired", "comment"):
            if before.get("disposition", {}).get(flag, 0) != after.get("disposition", {}).get(flag, 0):
                raise VerificationError(f"Stream {flag} disposition changed")
        if kind == "audio" and before.get("channels") != after.get("channels"):
            raise VerificationError("Audio channel count changed")
    for key in ("color_primaries", "color_transfer", "color_space", "color_range"):
        value = source.video.get(key)
        if value and value not in ("unknown", "unspecified") and result.video.get(key) != value:
            raise VerificationError(f"Video {key} changed: expected {value}, got {result.video.get(key, 'unknown')}")
    if expected_duration is None:
        if len(source.chapters) != len(result.chapters):
            raise VerificationError("Chapter count changed")
        for before, after in zip(source.chapters, result.chapters):
            for edge in ("start_time", "end_time"):
                if abs(float(before.get(edge, 0)) - float(after.get(edge, 0))) > .15:
                    raise VerificationError("Chapter timing changed")
            if before.get("tags", {}).get("title") != after.get("tags", {}).get("title"):
                raise VerificationError("Chapter title changed")
    source_tags = {key.casefold(): value for key, value in source.format.get("tags", {}).items()}
    output_tags = {key.casefold(): value for key, value in result.format.get("tags", {}).items()}
    for key, value in plan.metadata.items():
        if output_tags.get(key.casefold()) != value:
            raise VerificationError("Converted timecode metadata was not preserved")
    for tag in ("title", "artist", "comment", "description", "copyright"):
        if source_tags.get(tag) and output_tags.get(tag) != source_tags[tag]:
            raise VerificationError(f"Global {tag} metadata changed")
    duration = expected_duration if expected_duration is not None else source.duration
    if duration:
        tolerance = max(.35, min(2.0, duration * .005))
        if not result.duration or abs(result.duration - duration) > tolerance:
            raise VerificationError(f"Duration mismatch: expected {duration:.3f}s, got {result.duration}")
    if metadata_only:
        # Only used with an existing successful verification record AND matching
        # current source/output fingerprints, never for a newly encoded file.
        return result
    if expected_duration is None:
        empty_movtext = {s["index"] for s in plan.streams
                         if s.get("codec_name") == "mov_text" and s["index"] in plan.subtitle_codecs}
        before_spans = packet_timeline(source.path, runner, empty_movtext=empty_movtext)
        after_spans = packet_timeline(output, runner)
        source_video = before_spans.get(source.video["index"])
        output_video = after_spans.get(result.video["index"])
        if not source_video or not output_video:
            raise VerificationError("No timestamped video packets found")
        for before, after in zip(expected, result.streams):
            if stream_kind(before) == "artwork":
                continue
            if before.get("codec_type") not in ("video", "audio", "subtitle"):
                continue
            a, b = before_spans.get(before["index"]), after_spans.get(after["index"])
            if a and not b:
                raise VerificationError("An expected stream has no output packets")
            if a and b:
                # Preserve relative stream offsets, allowing encoder delay and timebase rounding.
                if abs((a[0] - source_video[0]) - (b[0] - output_video[0])) > .15:
                    raise VerificationError("Stream start offset changed; possible A/V sync error")
                if abs((a[1] - source_video[0]) - (b[1] - output_video[0])) > .15:
                    raise VerificationError("Stream endpoint changed; possible truncation or sync error")
    if settings.strict or not duration:
        runner.run(decode_command(output), timeout=None, progress=progress or (lambda _: None), nice=5)
    else:
        length = result.duration
        intervals = sorted((start, min(length, start + 8)) for start in
                           {0.0, max(0, length / 2 - 2), max(0, length - 8)})
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for start, end in merged:
            runner.run(decode_command(output, start=start, seconds=end - start), timeout=180,
                       progress=progress or (lambda _: None), nice=5)
    return result


class StateStore:
    def __init__(self, directory: Path, source: Path, output: Path, *, namespace: str | None = None):
        self.lock = threading.RLock()
        directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(f"{namespace or source}\0{output}".encode()).hexdigest()[:24]
        self.path = directory / f"queue-{key}.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {"version": 1, "jobs": {}, "temporary": []}
        if (not isinstance(self.data, dict) or self.data.get("version") != 1
                or not isinstance(self.data.get("jobs"), dict)
                or not isinstance(self.data.get("temporary"), list)
                or not all(isinstance(s, str) for s in self.data.get("temporary", []))):
            self.data = {"version": 1, "jobs": {}, "temporary": []}

    @synchronized
    def save(self):
        temp = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as file:
                json.dump(self.data, file, indent=2, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
            sync_directory(self.path.parent)
        finally:
            temp.unlink(missing_ok=True)

    @synchronized
    def track(self, path: Path):
        self.data["temporary"].append(str(path))
        self.save()

    @synchronized
    def untrack(self, path: Path):
        self.data["temporary"] = [s for s in self.data["temporary"] if s != str(path)]
        self.save()

    @synchronized
    def recover(self, output: Path) -> list[str]:
        """Delete only exact temporary paths recorded by this queue, under a held output lock."""
        messages = []
        remaining = []
        for raw in self.data.get("temporary", []):
            path = Path(raw)
            try:
                if (not path.is_relative_to(output) or path.parent.resolve() != path.parent
                        or not re.fullmatch(r"\.petal-[0-9a-f]{32}\.(?:partial\.mkv|asset\.(?:jpg|png))", path.name)):
                    raise ValueError("unsafe recorded temporary path")
                if path.exists() or path.is_symlink():
                    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                        raise ValueError("recorded temporary is not a regular file")
                    path.unlink()
                    messages.append(f"Removed interrupted temporary: {path}")
            except (OSError, ValueError) as error:
                remaining.append(raw)
                messages.append(f"Left recovery file untouched: {path}: {error}")
        self.data["temporary"] = remaining
        self.save()
        for directory, dirs, files in os.walk(output, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            for name in files:
                if re.fullmatch(r"\.petal-[0-9a-f]{32}\.(?:partial\.mkv|asset\.(?:jpg|png))", name):
                    path = Path(directory) / name
                    if str(path) not in remaining:
                        messages.append(f"Unregistered temporary left untouched for manual inspection: {path}")
        return messages
