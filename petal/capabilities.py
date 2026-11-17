"""Runtime evidence, not encoder-list optimism."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import os
from pathlib import Path
import shutil
import tempfile
import time

from .commands import encode_command
from .media import probe
from .models import PROFILES, Settings, Media, RESOLUTIONS
from .process import Cancelled, Runner
from .safety import verify


@dataclass
class Capabilities:
    device: str
    ffmpeg: str = ""
    ffprobe: str = ""
    driver: str = ""
    pci_device: str = ""
    options: dict[str, str] = field(default_factory=dict)
    formats: str = ""
    tested: dict[str, bool] = field(default_factory=dict)
    decode_tested: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    measured_at: float = field(default_factory=time.time)
    resolution: str = "1080p"

    def supports(self, settings: Settings, bits: int = 8) -> bool:
        return (self.device == settings.device and self.resolution == settings.resolution
                and self.tested.get(f"{settings.codec}:{settings.profile}:{bits}", False))

    def to_dict(self):
        return asdict(self)

    def can_decode(self, media: Media, settings: Settings) -> bool:
        key = f"{media.video.get('codec_name')}:{media.bit_depth}:{settings.codec}"
        return self.decode_tested.get(key, True)


def detect(settings: Settings, runner: Runner) -> Capabilities:
    report = Capabilities(settings.device, resolution=settings.resolution)
    sys_device = Path("/sys/class/drm") / Path(settings.device).name / "device"
    if sys_device.exists():
        report.pci_device = sys_device.resolve().name
        if report.pci_device != "0000:03:00.0":
            report.errors.append(f"Selected render node belongs to PCI {report.pci_device}; expected RX 7600 at 0000:03:00.0. Check the selected device.")
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            report.errors.append(f"{name} is missing; install FFmpeg")
            return report
        setattr(report, name, runner.run([name, "-version"]).stdout.splitlines()[0])
    for codec in ("av1_vaapi", "hevc_vaapi"):
        result = runner.run(["ffmpeg", "-hide_banner", "-h", f"encoder={codec}"], check=False)
        report.options[codec] = result.stdout + result.stderr
    report.formats = runner.run(["ffmpeg", "-hide_banner", "-h", "filter=scale_vaapi"], check=False).stdout
    if shutil.which("vainfo"):
        result = runner.run(["vainfo", "--display", "drm", "--device", settings.device], check=False)
        report.driver = result.stdout + result.stderr
    else:
        report.errors.append("vainfo is missing; driver query unavailable (install libva-utils)")
    if not Path(settings.device).exists() or not os.access(settings.device, os.R_OK | os.W_OK):
        report.errors.append(f"Render device {settings.device} is missing or inaccessible; hardware encoding is disabled")
        return report
    with tempfile.TemporaryDirectory(prefix="petal-hardware-") as directory:
        root = Path(directory)
        width, height = RESOLUTIONS[settings.resolution] or RESOLUTIONS["1080p"]
        for bits in (8, 10):
            source = root / f"source-{bits}.mkv"
            runner.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-f", "lavfi",
                        "-i", f"testsrc2=size={width}x{height}:rate=24", "-t", "0.75", "-pix_fmt",
                        "yuv420p10le" if bits == 10 else "yuv420p", "-c:v", "libx264",
                        "-preset", "ultrafast", "-crf", "18",
                        "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                        "-color_primaries", "bt709", "-color_trc", "bt709",
                        "-colorspace", "bt709", "-color_range", "tv", str(source)])
            media = probe(source, runner)
            for codec in ("av1_vaapi", "hevc_vaapi"):
                for profile in PROFILES:
                    key = f"{codec}:{profile}:{bits}"
                    output = root / f"test-{codec}-{profile}-{bits}.mkv"
                    config = replace(settings, codec=codec, profile=profile, audio="copy", strict=True)
                    try:
                        if "CQP" not in report.options[codec]:
                            raise RuntimeError("Encoder help does not advertise CQP")
                        runner.run(encode_command(media, output, config, hardware_decode=False), timeout=90)
                        verify(media, output, config, runner)
                        report.tested[key] = True
                    except Cancelled:
                        raise
                    except Exception as error:
                        report.tested[key] = False
                        report.errors.append(f"{key}: {error}")
                    finally:
                        output.unlink(missing_ok=True)
            # Encoder initialization alone does not validate decoded hardware frames,
            # filter negotiation, or HEVC decoder color-property propagation.
            hevc = root / f"hevc-{bits}.mkv"
            try:
                runner.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n",
                            "-i", str(source), "-c:v", "libx265", "-preset", "ultrafast",
                            "-x265-params", "pools=2:frame-threads=2:log-level=error",
                            "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                            str(hevc)], timeout=90)
            except Cancelled:
                raise
            except Exception as error:
                report.errors.append(f"HEVC decoder test fixture could not be generated: {error}")
            for fixture in (source, hevc):
                if not fixture.exists():
                    continue
                decoded = probe(fixture, runner)
                for codec in ("av1_vaapi", "hevc_vaapi"):
                    config = replace(settings, codec=codec, profile="Balanced", audio="copy", strict=True)
                    key = f"{decoded.video['codec_name']}:{bits}:{codec}"
                    if not report.supports(config, bits):
                        continue
                    output = root / f"decode-{key.replace(':', '-')}.mkv"
                    try:
                        runner.run(encode_command(decoded, output, config, hardware_decode=True), timeout=90)
                        verify(decoded, output, config, runner)
                        report.decode_tested[key] = True
                    except Cancelled:
                        raise
                    except Exception as error:
                        report.decode_tested[key] = False
                        report.errors.append(f"GPU decode/scaling {key}: {error}; software decode remains available")
                    finally:
                        output.unlink(missing_ok=True)
    return report
