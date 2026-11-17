from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from .models import Job, Status
from .scan_cache import ProbeCache, inspect_many
from .process import Cancelled, Runner

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mts",
                    ".m2ts", ".mpg", ".mpeg", ".vob", ".wmv", ".flv", ".ogv", ".3gp",
                    ".mxf", ".nut", ".m2v", ".y4m", ".divx", ".asf"}


def validate_roots(source: Path, destination: Path) -> tuple[Path, Path]:
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Source must be an existing directory")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and output must be separate, non-overlapping directory trees")
    if destination.exists() and not destination.is_dir():
        raise ValueError("Output must be a directory")
    # Resolve bind-mounted aliases too: realpath only resolves symbolic links.
    for parent in (destination, *destination.parents):
        if parent.exists() and parent.samefile(source):
            raise ValueError("Output is inside a filesystem alias of the source")
    if destination.exists():
        for parent in (source, *source.parents):
            if parent.samefile(destination):
                raise ValueError("Source is inside a filesystem alias of the output")
    return source, destination


def candidate_paths(source: Path, excluded: Path | None = None):
    def onerror(error):
        raise error
    for root, dirs, files in os.walk(source, followlinks=False, onerror=onerror):
        base = Path(root)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink()
                         and (excluded is None or not (base / d).resolve().is_relative_to(excluded.resolve())))
        for name in sorted(files):
            path = base / name
            if not path.is_symlink() and path.is_file() and not name.startswith(".petal-"):
                yield path


def destination_paths(paths: list[Path], source: Path, destination: Path,
                      preferred: dict[str, Path] | None = None) -> dict[Path, Path]:
    return allocate_paths({p: p.relative_to(source) for p in paths}, destination, preferred)


def allocate_paths(relatives: dict[Path, Path], destination: Path,
                   preferred: dict[str, Path] | None = None) -> dict[Path, Path]:
    simple = {p: rel.with_suffix(".mkv") for p, rel in relatives.items()}
    counts: dict[str, int] = {}
    for rel in simple.values():
        counts[str(rel).casefold()] = counts.get(str(rel).casefold(), 0) + 1
    reserved = {str(rel).casefold() for rel in simple.values()}
    result = {}
    claimed = set()
    for path, rel in relatives.items():
        old = (preferred or {}).get(str(rel))
        if old is not None:
            old = Path(old)
            if (old.is_relative_to(destination) and old.parent == (destination / rel).parent
                    and old.suffix.lower() == ".mkv" and str(old).casefold() not in claimed):
                result[path] = old
                claimed.add(str(old).casefold())
    reserved.update(str(p.relative_to(destination)).casefold() for p in result.values())
    for path in relatives:
        if path in result:
            continue
        rel = simple[path]
        if (counts[str(rel).casefold()] > 1 or len(rel.name.encode()) > 240
                or str(destination / rel).casefold() in claimed):
            digest = hashlib.sha256(str(path).encode()).hexdigest()
            for length in range(10, 65, 2):
                extension = path.suffix[1:].encode()[:16].decode(errors="ignore")
                tail = f".{extension}-{digest[:length]}.mkv"
                stem = path.stem.encode()[:240 - len(tail.encode())].decode(errors="ignore")
                alt = rel.with_name(stem + tail)
                if str(alt).casefold() not in reserved:
                    rel = alt
                    break
            else:
                raise ValueError("Could not allocate a collision-free output name")
            reserved.add(str(rel).casefold())
        result[path] = destination / rel
        claimed.add(str(result[path]).casefold())
    return result


def discover(source: Path, destination: Path, runner: Runner,
             on_job: Callable[[Job], None] = lambda _: None,
             preferred: dict[str, Path] | None = None, *, cache: ProbeCache | None = None) -> list[Job]:
    source, destination = validate_roots(source, destination)
    # Probe every regular file, including unknown extensions. Audio and non-media are ignored.
    paths = list(candidate_paths(source, destination))
    videos = []
    for path, info, error in inspect_many(paths, runner, cache):
        try:
            if error:
                raise error
            if info is None:
                continue
            demuxers = info.format.get("format_name", "").split(",")
            if any(name == "image2" or name.endswith("_pipe") for name in demuxers):
                continue
        except Cancelled:
            raise
        except Exception as error:
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                job = Job(path, path.relative_to(source), destination / path.relative_to(source).with_suffix(".mkv"),
                          size=path.stat().st_size, status=Status.FAILED,
                          message=f"Cannot scan video: {error}")
                videos.append(job)
                on_job(job)
            continue
        job = Job(path, path.relative_to(source), destination / path.relative_to(source).with_suffix(".mkv"),
                  media=info, size=path.stat().st_size)
        videos.append(job)
        on_job(job)
    names = destination_paths([j.source for j in videos], source, destination, preferred)
    for job in videos:
        job.destination = names[job.source]
    return videos


def validate_input(path: Path, output: Path, *, explicit_file: bool = False) -> Path:
    """Folder inputs retain strict tree separation; explicit files cannot be in output."""
    if path.is_symlink():
        raise ValueError("Symbolic-link inputs are ignored")
    path, output = path.expanduser().resolve(), output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise ValueError("Output must be a directory")
    if explicit_file:
        if path.is_relative_to(output) or path.parent == output:
            raise ValueError("An input file cannot be inside the output directory")
    else:
        validate_roots(path, output)
    return path


def discover_selection(inputs: list[Path], destination: Path | None, runner: Runner,
                       namespaces: dict[str, str], on_job: Callable[[Job], None] = lambda _: None,
                       preferred: dict[str, Path] | None = None, *, cache: ProbeCache | None = None) -> list[Job]:
    """Add heterogeneous local inputs, retaining per-folder hierarchy and deduplicating."""
    destination = destination.expanduser().resolve() if destination else None
    allocation_root = destination or Path(".")  # Relative planned names until output is chosen.
    candidates: dict[Path, tuple[Path, bool, Path]] = {}
    # Folders first keep nested hierarchy when a file and its folder are both dropped.
    priority = {key: index for index, key in enumerate(namespaces)}
    def order(path):
        root = path.parent if path.is_file() else path
        return priority.get(str(root.expanduser().resolve()), len(priority)), not path.is_dir(), str(path)
    ordered = sorted(inputs, key=order)
    for entry in ordered:
        if runner.cancel.is_set():
            raise Cancelled()
        if not entry.exists():
            runner.log(f"Dropped input is unavailable: {entry}")
            continue
        file = entry.is_file()
        if entry.is_symlink():
            runner.log(f"Ignored symbolic-link input: {entry}")
            continue
        entry = validate_input(entry, destination, explicit_file=file) if destination else entry.expanduser().resolve()
        root = entry.parent if file else entry
        root_key = str(root)
        if root_key not in namespaces:
            name = root.name or "Videos"
            used = {v.casefold() for v in namespaces.values()}
            if name.casefold() in used:
                name += "-" + hashlib.sha256(root_key.encode()).hexdigest()[:8]
            namespaces[root_key] = name
        prefix = Path(namespaces[root_key])
        # Persisted namespace text must never be able to escape the output root.
        if prefix.is_absolute() or ".." in prefix.parts:
            raise ValueError("Unsafe source namespace")
        paths = [entry] if file else candidate_paths(entry, destination)
        for path in paths:
            if runner.cancel.is_set():
                raise Cancelled()
            candidates.setdefault(path, (root, file, prefix / path.relative_to(root)))
    jobs = []
    for path, info, error in inspect_many(candidates, runner, cache):
        root, explicit, relative = candidates[path]
        try:
            if error:
                raise error
            if info is None:
                continue
            if any(name == "image2" or name.endswith("_pipe") for name in info.format.get("format_name", "").split(",")):
                continue
            job = Job(path, relative, allocation_root / relative.with_suffix(".mkv"), media=info,
                      size=path.stat().st_size, input_root=root, explicit_file=explicit)
        except Cancelled:
            raise
        except Exception as error:
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            job = Job(path, relative, allocation_root / relative.with_suffix(".mkv"), status=Status.FAILED,
                      message=f"Cannot scan video: {error}", input_root=root, explicit_file=explicit)
        jobs.append(job)
        on_job(job)
    names = allocate_paths({j.source: j.relative for j in jobs}, allocation_root, preferred if destination else None)
    for job in jobs:
        job.destination = names[job.source]
    return jobs
