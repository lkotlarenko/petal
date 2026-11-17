"""Preserve embedded artwork as MKV attachments without recompressing its pixels."""
from pathlib import Path
import uuid

from .commands import stream_plan
from .models import Media, Settings
from .process import Runner
from .safety import StateStore


def cleanup_artwork(assets: dict[int, Path], store: StateStore, runner: Runner):
    for path in assets.values():
        try:
            path.unlink(missing_ok=True)
            store.untrack(path)
        except OSError as error:
            runner.log(f"Artwork cleanup deferred to recovery: {error}")


def prepare_artwork(media: Media, settings: Settings, directory: Path,
                    store: StateStore, runner: Runner) -> dict[int, Path]:
    assets: dict[int, Path] = {}
    try:
        for stream in stream_plan(media, settings).artwork:
            extension = "jpg" if stream["codec_name"] == "mjpeg" else "png"
            path = directory / f".petal-{uuid.uuid4().hex}.asset.{extension}"
            store.track(path)
            assets[stream["index"]] = path
            runner.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "warning", "-xerror", "-n",
                        "-protocol_whitelist", "file,pipe", "-i", str(media.path),
                        "-map", f"0:{stream['index']}", "-c:v", "copy", "-frames:v", "1",
                        "-f", "image2", "-update", "1", str(path)])
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError("Cover-art extraction produced no image")
        return assets
    except BaseException:
        cleanup_artwork(assets, store, runner)
        raise
