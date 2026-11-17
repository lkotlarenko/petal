"""All production encodes are VA-API. CPU encoders are never substituted."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .media import target_sar, target_size
from .models import Media, Settings
from .color import frame_color, software_scale_color, bitstream_color


class Unsupported(ValueError):
    pass


SUBTITLE_COPY = {"subrip", "ass", "ssa", "webvtt", "hdmv_pgs_subtitle", "dvd_subtitle"}
AUDIO_COPY = {"aac", "ac3", "eac3", "dts", "truehd", "flac", "alac", "mp2", "mp3", "opus",
              "vorbis", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le",
              "pcm_s16be", "pcm_s24be", "mlp", "wavpack", "tta"}


@dataclass
class Plan:
    streams: list[dict]
    warnings: list[str]
    artwork: list[dict] = field(default_factory=list)
    subtitle_codecs: dict[int, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


def stream_plan(media: Media, settings: Settings) -> Plan:
    if len(media.videos) != 1:
        raise Unsupported("Multiple video angles/streams require manual review; file skipped")
    if media.hdr:
        raise Unsupported("HDR / wide-gamut / Dolby Vision preservation is not validated; file skipped")
    if media.bit_depth > 10:
        raise Unsupported("Input precision above 10 bits is not supported; file skipped")
    if media.video.get("field_order") not in (None, "unknown", "progressive"):
        raise Unsupported("Interlaced video requires an explicit deinterlacing decision; file skipped")
    # Avoid unrequested conversion of alpha or RGB/4:4:4/4:2:2 chroma.
    pix = media.video.get("pix_fmt", "")
    if pix and pix not in {"yuv420p", "yuvj420p", "nv12", "yuv420p10le", "p010le"}:
        raise Unsupported(f"Pixel format {pix} requires an unvalidated chroma/alpha conversion")
    selected, warnings = [], []
    artwork, subtitle_codecs, metadata = [], {}, {}
    for stream in media.streams:
        kind, codec = stream.get("codec_type"), stream.get("codec_name", "unknown")
        issue = None
        if settings.incompatible == "compatible":
            if kind == "video" and stream != media.video and codec in {"mjpeg", "png"}:
                artwork.append(stream)
                warnings.append(f"Preserved cover art from stream {stream['index']} as a Matroska attachment")
                continue
            if kind == "data" and stream.get("codec_tag_string") == "tmcd":
                timecode = stream.get("tags", {}).get("timecode")
                if timecode:
                    metadata[f"petal_timecode_stream_{stream['index']}"] = timecode
                    warnings.append(f"Converted auxiliary timecode stream {stream['index']} to metadata ({timecode})")
                    continue
            if kind == "subtitle" and codec in {"mov_text", "text"}:
                subtitle_codecs[stream["index"]] = "ass"
                warnings.append(f"Converted subtitle stream {stream['index']} from {codec} to ASS; text/timing retained, styling may differ")
        if kind == "video" and stream != media.video:
            issue = "cover-art video stream"
        elif kind == "audio" and settings.audio == "copy" and codec not in AUDIO_COPY:
            issue = f"audio codec {codec} is not in the validated MKV copy list"
        elif kind == "audio" and settings.audio == "opus":
            layout = stream.get("channel_layout", "")
            if layout not in {"mono", "stereo", "3.0", "quad", "5.0", "5.0(side)", "5.1", "5.1(side)", "6.1", "7.1"}:
                raise Unsupported(f"Opus conversion needs a supported known channel layout; found {layout!r}")
        elif kind == "subtitle" and codec not in SUBTITLE_COPY and stream["index"] not in subtitle_codecs:
            issue = f"subtitle codec {codec} cannot be safely copied to MKV"
        elif kind == "data" and stream.get("codec_tag_string") == "tmcd":
            issue = "auxiliary timecode track (tmcd) cannot be stored as a Matroska track; the video/audio are separate streams"
        elif kind not in {"video", "audio", "subtitle", "attachment"}:
            issue = f"{kind or 'unknown'} stream ({codec}) cannot be safely copied to MKV"
        if issue:
            message = f"Stream {stream['index']}: {issue}"
            if settings.incompatible != "omit":
                raise Unsupported(message + "; select maximum compatibility for supported conversions, or explicit omission in Settings")
            warnings.append("Explicitly omitted " + message)
        else:
            selected.append(stream)
    return Plan(selected, warnings, artwork, subtitle_codecs, metadata)


def device_args(settings: Settings) -> list[str]:
    return ["-init_hw_device", f"vaapi=petal:{settings.device}", "-filter_hw_device", "petal"]


def quality_args(settings: Settings, size: tuple[int, int] | None = None) -> list[str]:
    # Driver defaults handle references, tiles, compression_level and AQ. No guessed AMF flags.
    option = "-global_quality:v:0" if settings.codec == "av1_vaapi" else "-qp:v:0"
    quality = settings.quality_for_size(size) if size else settings.quality
    return ["-c:v:0", settings.codec, "-rc_mode:v:0", "CQP", option, str(quality)]


def encode_command(media: Media, temporary: Path, settings: Settings, *,
                   hardware_decode: bool = True, sample_seconds: float | None = None,
                   sample_start: float = 0, artwork: dict[int, Path] | None = None) -> list[str]:
    plan = stream_plan(media, settings)
    w, h = target_size(media, settings)
    sar = target_sar(media, settings)
    fmt = "p010le" if media.bit_depth == 10 else "nv12"
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "warning", "-xerror", "-n"] + device_args(settings)
    args += ["-filter_threads", "2"]
    if hardware_decode:
        if media.rotation:
            raise Unsupported("Rotated inputs use software autorotation and VA-API encoding")
        args += ["-hwaccel", "vaapi", "-hwaccel_device", "petal", "-hwaccel_output_format", "vaapi"]
    else:
        args += ["-threads:v:0", "4"]
    if sample_start:
        args += ["-ss", str(sample_start)]
    args += ["-protocol_whitelist", "file,pipe", "-i", str(media.path)]
    if sample_seconds:
        args += ["-t", str(sample_seconds)]
    for stream in plan.streams:
        args += ["-map", f"0:{stream['index']}"]
    args += ["-map_metadata", "0", "-map_chapters", "0"]
    for index, stream in enumerate(plan.streams):
        kind = stream.get("codec_type")
        if stream["index"] in plan.subtitle_codecs:
            args += [f"-c:{index}", plan.subtitle_codecs[stream["index"]]]
        elif kind != "video" and not (kind == "audio" and settings.audio == "opus"):
            args += [f"-c:{index}", "copy"]
    for key, value in plan.metadata.items():
        args += ["-metadata", f"{key}={value}"]
    attachment_index = sum(s.get("codec_type") == "attachment" for s in plan.streams)
    for cover in plan.artwork:
        path = (artwork or {}).get(cover["index"])
        if path is None:
            raise Unsupported("Cover-art attachment must be prepared before encoding")
        mime = "image/jpeg" if cover["codec_name"] == "mjpeg" else "image/png"
        extension = "jpg" if cover["codec_name"] == "mjpeg" else "png"
        args += ["-attach", str(path), f"-metadata:s:t:{attachment_index}", f"mimetype={mime}",
                 f"-metadata:s:t:{attachment_index}", f"filename=cover-{cover['index']}.{extension}"]
        attachment_index += 1
    if hardware_decode:
        filters = f"scale_vaapi=w={w}:h={h}:format={fmt},setsar={sar.numerator}/{sar.denominator}:max=10000"
    else:
        filters = (f"scale={w}:{h}:flags=lanczos{software_scale_color(media)},setsar={sar.numerator}/{sar.denominator}:max=10000,"
                   f"format={fmt},hwupload")
    color = frame_color(media)
    if color:
        # Header-only filter, valid on hardware frames too. Restore properties lost
        # by some VA-API decoders/upload paths without changing pixel values.
        filters = color + "," + filters + "," + color
    args += ["-filter:v:0", filters] + quality_args(settings, (w, h))
    args += ["-noautoscale:v:0"] + bitstream_color(media, settings)
    try:
        fps = float(Fraction(media.video.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = 0
    # A GOP limit does not change the frame rate or introduce duplicate frames.
    args += ["-g:v:0", str(max(12, min(1200, round(fps * 4))) if fps > 0 else 240)]
    args += ["-fps_mode:v:0", "passthrough", "-enc_time_base:v:0", "filter",
             "-metadata:s:v:0", "rotate=0"]
    if settings.audio == "opus":
        for i, stream in enumerate(s for s in plan.streams if s.get("codec_type") == "audio"):
            channels = int(stream.get("channels", 2))
            bitrate = 96 if channels == 1 else 160 if channels == 2 else min(512, channels * 64)
            args += [f"-c:a:{i}", "libopus", f"-b:a:{i}", f"{bitrate}k", f"-vbr:a:{i}", "on"]
    args += ["-max_muxing_queue_size", "4096", "-progress", "pipe:1", "-nostats", "-f", "matroska", str(temporary)]
    return args


def decode_command(path: Path, *, seconds: float | None = None, start: float = 0) -> list[str]:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "warning", "-xerror", "-err_detect", "explode",
            "-threads:v:0", "4", "-threads:a", "2", "-filter_threads", "1"]
    if start:
        args += ["-ss", str(start)]
    args += ["-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:V", "-map", "0:a?", "-sn", "-dn"]
    if seconds:
        args += ["-t", str(seconds)]
    return args + ["-progress", "pipe:1", "-nostats", "-f", "null", "-"]
