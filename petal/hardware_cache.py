"""Persistent hardware evidence, invalidated by software/device/config changes."""
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .capabilities import Capabilities
from .models import Settings


def _stat(path: Path) -> dict | None:
    try:
        stat = path.stat()
        return {"path": str(path.resolve()), "size": stat.st_size, "mtime": stat.st_mtime_ns}
    except OSError:
        return None


def signature(settings: Settings) -> str:
    programs = {name: _stat(Path(path)) if (path := shutil.which(name)) else None
                for name in ("ffmpeg", "ffprobe", "vainfo")}
    device = Path(settings.device)
    try:
        stat = device.stat()
        render = (stat.st_rdev, stat.st_mode, stat.st_uid, stat.st_gid)
    except OSError:
        render = None
    pci = Path("/sys/class/drm") / device.name / "device"
    pci_info = {}
    for name in ("vendor", "device", "revision"):
        try:
            pci_info[name] = (pci / name).read_text().strip()
        except OSError:
            pass
    graph = hashlib.sha256()
    for name in ("commands.py", "color.py", "media.py", "models.py", "capabilities.py", "safety.py"):
        graph.update(Path(__file__).with_name(name).read_bytes())
    driver = Path(os.environ.get("LIBVA_DRIVERS_PATH", "/usr/lib/dri")) / "radeonsi_drv_video.so"
    payload = {"schema": 1, "programs": programs, "render": render,
               "access": os.access(device, os.R_OK | os.W_OK), "pci": str(pci.resolve()),
               "pci_info": pci_info, "driver": _stat(driver), "kernel": os.uname().release,
               "driver_name": os.environ.get("LIBVA_DRIVER_NAME", "radeonsi"),
               "graph": graph.hexdigest(), "device": settings.device, "resolution": settings.resolution}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cache_path(directory: Path, settings: Settings) -> Path:
    key = hashlib.sha256(f"{settings.device}\0{settings.resolution}".encode()).hexdigest()[:20]
    return directory / f"hardware-{key}.json"


def load(directory: Path, settings: Settings) -> Capabilities | None:
    try:
        data = json.loads(cache_path(directory, settings).read_text())
        if data.get("signature") != signature(settings):
            return None
        report = data["report"]
        if not isinstance(report.get("tested"), dict) or not isinstance(report.get("decode_tested", {}), dict):
            return None
        allowed = {field.name for field in fields(Capabilities)}
        result = Capabilities(**{key: value for key, value in report.items() if key in allowed})
        if result.device != settings.device or result.resolution != settings.resolution:
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(directory: Path, settings: Settings, report: Capabilities):
    directory.mkdir(parents=True, exist_ok=True)
    destination = cache_path(directory, settings)
    temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump({"signature": signature(settings), "report": report.to_dict()}, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
