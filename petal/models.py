from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from enum import StrEnum
import hashlib
import json
import os


class Status(StrEnum):
    WAITING = "Waiting"
    ENCODING = "Encoding"
    VERIFYING = "Verifying"
    COMPLETED = "Completed"
    SKIPPED = "Skipped"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


PROFILES = {
    "High Quality": {"av1_vaapi": 80, "hevc_vaapi": 20},
    "Balanced": {"av1_vaapi": 105, "hevc_vaapi": 24},
    "Maximum Savings": {"av1_vaapi": 135, "hevc_vaapi": 29},
}
PROFILE_HELP = {
    "High Quality": "Retains more fine detail and texture; larger outputs.",
    "Balanced": "Quality-oriented CQP; compare a sample before a large batch.",
    "Maximum Savings": "More compression; fine texture and gradients may visibly degrade.",
}

RESOLUTIONS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2160p": (3840, 2160),
    "original": None,
}
RESOLUTION_LABELS = {key: ("4K (2160p)" if key == "2160p" else "Original (no limit)" if key == "original" else key)
                     for key in RESOLUTIONS}


@dataclass(frozen=True)
class Settings:
    codec: str = "av1_vaapi"
    profile: str = "Balanced"
    device: str = "/dev/dri/renderD128"
    strict: bool = True
    min_savings: float = 5.0
    audio: str = "copy"
    incompatible: str = "compatible"
    auto_quality: bool = True
    resolution: str = "1080p"

    def __post_init__(self):
        if self.codec not in ("av1_vaapi", "hevc_vaapi") or self.profile not in PROFILES:
            raise ValueError("Select a supported VA-API codec and profile")
        if not 0 <= self.min_savings <= 99:
            raise ValueError("Minimum savings must be between 0 and 99 percent")
        if self.audio not in ("copy", "opus") or self.incompatible not in ("compatible", "skip", "omit"):
            raise ValueError("Invalid audio or incompatible-stream policy")
        if not Path(self.device).is_absolute():
            raise ValueError("The render device must be an absolute path")
        if self.resolution not in RESOLUTIONS:
            raise ValueError("Select a supported target resolution")

    def quality_for_size(self, size: tuple[int, int]) -> int:
        # Provisional, modest offsets on each encoder's own scale. Smaller
        # pictures receive more detail protection; larger pictures trade a
        # little quantization for savings. Actual output size prevents a 4K
        # ceiling from degrading an unchanged 720p source.
        pixels = size[0] * size[1]
        if pixels <= 854 * 480:
            av1, hevc = -12, -2
        elif pixels <= 1280 * 720:
            av1, hevc = -6, -1
        elif pixels <= 1920 * 1080:
            av1, hevc = 0, 0
        elif pixels <= 2560 * 1440:
            av1, hevc = 4, 1
        else:
            av1, hevc = 8, 2
        return PROFILES[self.profile][self.codec] + (av1 if self.codec == "av1_vaapi" else hevc)

    @property
    def quality(self) -> int:
        return self.quality_for_size(RESOLUTIONS[self.resolution] or RESOLUTIONS["1080p"])

    def encoding_key(self, media: Media) -> str:
        from .media import target_size
        fields = asdict(self)
        for key in ("strict", "auto_quality", "resolution", "profile"):
            fields.pop(key)
        fields["dimensions"] = target_size(media, self)
        fields["quantizer"] = self.quality_for_size(fields["dimensions"])
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    @property
    def key(self) -> str:
        fields = asdict(self)
        fields.pop("strict")  # Verification strength does not alter encoded bytes.
        fields.pop("auto_quality")
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    @property
    def legacy_key(self) -> str:
        fields = asdict(self)
        fields.pop("auto_quality")
        if self.resolution == "1080p":
            fields.pop("resolution")
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    @property
    def legacy_identity_key(self) -> str:
        fields = asdict(self)
        fields.pop("auto_quality")
        fields.pop("strict")
        if self.resolution == "1080p":
            fields.pop("resolution")
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


@dataclass
class Media:
    path: Path
    streams: list[dict]
    format: dict
    chapters: list[dict] = field(default_factory=list)

    @property
    def videos(self) -> list[dict]:
        return [s for s in self.streams if s.get("codec_type") == "video"
                and not s.get("disposition", {}).get("attached_pic")]

    @property
    def video(self) -> dict:
        if not self.videos:
            raise ValueError("No video stream")
        return self.videos[0]

    @property
    def duration(self) -> float | None:
        for raw in (self.format.get("duration"), self.video.get("duration")):
            try:
                value = float(raw)
                if 0 < value < 1e10:
                    return value
            except (ValueError, TypeError):
                pass
        return None

    @property
    def rotation(self) -> float:
        for item in self.video.get("side_data_list", []):
            if "rotation" in item:
                return float(item["rotation"]) % 360
        return float(self.video.get("tags", {}).get("rotate", 0)) % 360

    @property
    def bit_depth(self) -> int:
        import re
        pix = self.video.get("pix_fmt", "")
        bits = self.video.get("bits_per_raw_sample")
        if bits and str(bits).isdigit() and int(bits):
            return int(bits)
        match = re.search(r"(?:p|yuv\d+p|gbrp)(\d+)(?:le|be)?$", pix)
        if match:
            return int(match[1])
        if pix.startswith("p010"):
            return 10
        return 8

    @property
    def hdr(self) -> bool:
        v = self.video
        return (v.get("color_transfer") in ("smpte2084", "arib-std-b67")
                or v.get("color_primaries") == "bt2020"
                or any(any(term in s.get("side_data_type", "").lower()
                           for term in ("mastering", "content light", "dovi", "hdr"))
                       for s in v.get("side_data_list", [])))


@dataclass
class Job:
    source: Path
    relative: Path
    destination: Path
    media: Media | None = None
    size: int = 0
    status: Status = Status.WAITING
    message: str = ""
    final_size: int = 0
    progress: float = 0.0
    source_key: str = ""
    quality: dict = field(default_factory=dict)
    output_dimensions: tuple[int, int] | None = None
    encoder_quality: int | None = None
    input_root: Path | None = None
    explicit_file: bool = False
    encode_seconds: float | None = None
    verify_seconds: float | None = None
    encoder_profile: str | None = None
    encoder_codec: str | None = None


def xdg_path(kind: str) -> Path:
    defaults = {"config": ".config", "state": ".local/state", "cache": ".cache"}
    base = Path(os.environ.get(f"XDG_{kind.upper()}_HOME", str(Path.home() / defaults[kind])))
    if not base.is_absolute():
        base = Path.home() / defaults[kind]
    path = base / "petal"
    path.mkdir(parents=True, exist_ok=True)
    return path
