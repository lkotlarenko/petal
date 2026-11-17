"""Verified, journaled relocation of explicitly selected skipped/failed originals.

Never overwrite. A durable copy precedes source removal. Recovery only restores
an interrupted source hold; it never completes deletion automatically.
"""

import ctypes
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .discovery import validate_input
from .history import atomic_json
from .models import Status
from .process import Cancelled
from .safety import fingerprint, output_lock

BLOCK = 4 * 1024 * 1024


@contextmanager
def parent_fd(path: Path, create=False):
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("An absolute, non-traversing path is required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parent.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o755, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def rename_new(source, destination, fd):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(fd, os.fsencode(source), fd, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)
    os.fsync(fd)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def digest_fd(fd, cancel, progress=lambda _: None):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Verification requires a regular file")
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        if cancel.is_set():
            raise Cancelled()
        block = os.read(fd, BLOCK)
        if not block:
            break
        digest.update(block)
        total += len(block)
        progress(total)
    after = os.fstat(fd)
    if (identity(before), before.st_ctime_ns) != (
        identity(after),
        after.st_ctime_ns,
    ) or total != before.st_size:
        raise ValueError("File changed during verification; original retained")
    return digest.hexdigest()


class Relocator:
    def __init__(
        self, output: Path, state_dir: Path, cancel=None, event=lambda *_: None
    ):
        self.output = output.expanduser().resolve()
        self.cancel = cancel if cancel is not None else threading.Event()
        self.event = event
        key = hashlib.sha256(str(self.output).encode()).hexdigest()[:24]
        self.path = state_dir / f"relocations-{key}.json"
        try:
            self.records = json.loads(self.path.read_text())
            if not isinstance(self.records, dict):
                raise ValueError("Invalid relocation journal")
        except FileNotFoundError:
            self.records = {}
        # Other read/parse errors must not silently discard recovery evidence.

    def save(self):
        atomic_json(self.path, self.records)

    def progress(self, job, phase):
        last = [0.0]

        def notify(amount):
            now = time.monotonic()
            if now - last[0] >= 0.15 or amount >= job.size:
                last[0] = now
                self.event(
                    "relocation_detail", (str(job.relative), phase, amount, job.size)
                )

        return notify

    def clean_partial(self, job):
        record = self.records.get(str(job.source))
        if not record or record.get("phase") != "prepared":
            return
        destination = Path(record["destination"])
        temporary = record["temporary"]
        if not destination.is_relative_to(self.output) or not re.fullmatch(
            r"\.petal-[0-9a-f]{32}\.move-copy", temporary
        ):
            raise ValueError("Unsafe unfinished copy record")
        try:
            with parent_fd(destination) as fd:
                info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Unsafe unfinished copy")
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:
            pass

    def recover(self):
        """Called under output_lock. Restore held originals without replacement."""
        for record in self.records.values():
            if record.get("phase") == "moved":
                continue
            source = Path(record["source"])
            hold = record["hold"]
            if not re.fullmatch(r"\.petal-[0-9a-f]{32}\.move-source", hold):
                raise ValueError("Unsafe source recovery name")
            with parent_fd(source) as fd:
                try:
                    info = os.stat(hold, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    if record.get("phase") == "held" and not source.exists():
                        destination = Path(record["destination"])
                        if not destination.is_relative_to(
                            self.output
                        ) or destination.relative_to(self.output).parts[0] not in (
                            "skipped",
                            "failed",
                        ):
                            raise ValueError("Unsafe recovery destination")
                        with parent_fd(destination) as ddir:
                            check = os.open(
                                destination.name,
                                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=ddir,
                            )
                            try:
                                if digest_fd(check, self.cancel) != record.get(
                                    "sha256"
                                ):
                                    raise ValueError(
                                        "Recovery copy could not be verified"
                                    )
                            finally:
                                os.close(check)
                        record["phase"] = "moved"
                        self.save()
                    continue
                if (
                    not stat.S_ISREG(info.st_mode)
                    or list(identity(info)) != record["identity"]
                ):
                    raise ValueError(
                        f"Changed recovery source retained: {source.parent / hold}"
                    )
                rename_new(hold, source.name, fd)
                self.event("log", f"Restored interrupted move source: {source}")

    def move(self, jobs, category):
        expected = {"skipped": Status.SKIPPED, "failed": Status.FAILED}.get(category)
        if expected is None:
            raise ValueError("Only skipped and failed originals may be moved")
        results = []
        with output_lock(self.output):
            self.recover()
            for index, job in enumerate(jobs):
                if self.cancel.is_set():
                    break
                if job.status != expected or job.relocated_to:
                    continue
                try:
                    target = self.move_one(job, category)
                    job.relocated_to = target
                    job.relocation_error = ""
                    results.append((job, True, str(target)))
                except Cancelled:
                    try:
                        self.clean_partial(job)
                    except (OSError, ValueError) as error:
                        self.event("log", f"Copy cleanup deferred: {error}")
                    break
                except Exception as error:
                    job.relocation_error = str(error)
                    try:
                        self.clean_partial(job)
                    except (OSError, ValueError) as cleanup_error:
                        job.relocation_error += (
                            f"; copy cleanup deferred: {cleanup_error}"
                        )
                    results.append((job, False, str(error)))
                self.event("relocated", job)
                self.event("relocation_progress", (index + 1, len(jobs)))
        return results

    def move_one(self, job, category):
        relative = job.relative
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError("Unsafe relative filename")
        source = job.source.absolute()
        prior = self.records.get(str(source))
        if prior and prior.get("phase") == "moved" and not source.exists():
            destination = self.output / category / relative
            if prior.get("destination") != str(destination):
                raise ValueError("Recovery destination differs")
            with parent_fd(destination) as fd:
                check = os.open(
                    destination.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=fd,
                )
                try:
                    if digest_fd(check, self.cancel) != prior.get("sha256"):
                        raise ValueError(
                            "Moved file changed; manual inspection required"
                        )
                finally:
                    os.close(check)
            return destination
        validate_input(
            source if job.explicit_file else job.input_root or source.parent,
            self.output,
            explicit_file=job.explicit_file,
        )
        if source.is_relative_to(self.output):
            raise ValueError("Cannot move an input already inside the output tree")
        if job.source_key and fingerprint(source) != job.source_key:
            raise ValueError("Original changed since processing; move refused")
        destination = self.output / category / relative
        key = str(source)
        record = self.records.get(key)
        if record and record.get("destination") != str(destination):
            raise ValueError(
                "An earlier move used a different destination; inspect the recovery journal"
            )
        with parent_fd(source) as sdir, parent_fd(destination, create=True) as ddir:
            source_fd = os.open(
                source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=sdir
            )
            try:
                fcntl.flock(source_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                initial = os.fstat(source_fd)
                if not stat.S_ISREG(initial.st_mode):
                    raise ValueError("Input must be a regular file")
                job.size = initial.st_size
                if record and record["identity"] != list(identity(initial)):
                    raise ValueError("Original identity changed since interrupted move")
                if record is None:
                    token = uuid.uuid4().hex
                    record = {
                        "source": str(source),
                        "destination": str(destination),
                        "identity": list(identity(initial)),
                        "temporary": f".petal-{token}.move-copy",
                        "hold": f".petal-{token}.move-source",
                        "phase": "prepared",
                    }
                    self.records[key] = record
                    self.save()
                temporary = record["temporary"]
                if not re.fullmatch(r"\.petal-[0-9a-f]{32}\.move-copy", temporary):
                    raise ValueError("Unsafe copy recovery name")
                # A retry may reuse only a previously journaled, published copy.
                try:
                    check = os.open(
                        destination.name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=ddir,
                    )
                except FileNotFoundError:
                    check = None
                if check is not None:
                    try:
                        if record["phase"] not in ("copied", "held") or not record.get(
                            "sha256"
                        ):
                            raise FileExistsError(
                                f"Destination exists; original retained: {destination}"
                            )
                        if digest_fd(check, self.cancel) != record["sha256"]:
                            raise ValueError(
                                "Existing recovery copy changed; original retained"
                            )
                    finally:
                        os.close(check)
                    if digest_fd(source_fd, self.cancel) != record["sha256"]:
                        raise ValueError("Original no longer matches recovery copy")
                else:
                    # Remove only a journal-owned unfinished copy, never a final file.
                    try:
                        stale = os.stat(temporary, dir_fd=ddir, follow_symlinks=False)
                        if not stat.S_ISREG(stale.st_mode):
                            raise ValueError("Unsafe unfinished copy")
                        os.unlink(temporary, dir_fd=ddir)
                        os.fsync(ddir)
                    except FileNotFoundError:
                        pass
                    copy_fd = os.open(
                        temporary,
                        os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=ddir,
                    )
                    try:
                        sha = hashlib.sha256()
                        copied = 0
                        progress = self.progress(job, "Copying")
                        while True:
                            if self.cancel.is_set():
                                raise Cancelled()
                            block = os.read(source_fd, BLOCK)
                            if not block:
                                break
                            sha.update(block)
                            copied += len(block)
                            progress(copied)
                            view = memoryview(block)
                            while view:
                                written = os.write(copy_fd, view)
                                if written <= 0:
                                    raise OSError("Copy made no progress")
                                view = view[written:]
                        if identity(os.fstat(source_fd)) != identity(initial):
                            raise ValueError("Original changed while copying")
                        os.fchmod(copy_fd, stat.S_IMODE(initial.st_mode) & 0o777)
                        os.utime(copy_fd, ns=(initial.st_atime_ns, initial.st_mtime_ns))
                        os.fsync(copy_fd)
                        expected_hash = sha.hexdigest()
                        if (
                            digest_fd(
                                copy_fd,
                                self.cancel,
                                self.progress(job, "Checking copy"),
                            )
                            != expected_hash
                            or os.fstat(copy_fd).st_size != initial.st_size
                        ):
                            raise ValueError(
                                "Full copy verification failed; original retained"
                            )
                        if (
                            digest_fd(
                                source_fd,
                                self.cancel,
                                self.progress(job, "Checking original"),
                            )
                            != expected_hash
                        ):
                            raise ValueError(
                                "Original and copy differ; original retained"
                            )
                        # Persist the hash before publication, but not permission to
                        # remove the source until the directory entry is durable.
                        record["sha256"] = expected_hash
                        self.save()
                        os.link(
                            temporary,
                            destination.name,
                            src_dir_fd=ddir,
                            dst_dir_fd=ddir,
                            follow_symlinks=False,
                        )
                        os.fsync(ddir)
                        record["phase"] = "copied"
                        self.save()
                    finally:
                        os.close(copy_fd)
                if self.cancel.is_set():
                    raise Cancelled()
                if identity(
                    os.stat(source.name, dir_fd=sdir, follow_symlinks=False)
                ) != identity(initial):
                    raise ValueError(
                        "Original path changed; verified copy and source retained"
                    )
                # Journal the hold name before renaming; crash recovery restores it.
                record["phase"] = "held"
                self.save()
                try:
                    rename_new(source.name, record["hold"], sdir)
                except Exception:
                    try:
                        held = os.stat(
                            record["hold"], dir_fd=sdir, follow_symlinks=False
                        )
                        if identity(held) == identity(initial):
                            rename_new(record["hold"], source.name, sdir)
                    except FileNotFoundError:
                        pass
                    raise
                try:
                    held = os.stat(record["hold"], dir_fd=sdir, follow_symlinks=False)
                    if (
                        identity(held) != identity(initial)
                        or digest_fd(
                            source_fd,
                            self.cancel,
                            self.progress(job, "Confirming original"),
                        )
                        != record["sha256"]
                    ):
                        raise ValueError("Original changed before removal; move halted")
                    checked_source = os.fstat(source_fd)
                    # Re-read the published copy immediately before source removal.
                    final_fd = os.open(
                        destination.name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=ddir,
                    )
                    try:
                        if (
                            digest_fd(
                                final_fd,
                                self.cancel,
                                self.progress(job, "Final copy check"),
                            )
                            != record["sha256"]
                        ):
                            raise ValueError("Published copy failed final verification")
                        checked_copy = os.fstat(final_fd)
                    finally:
                        os.close(final_fd)
                    current_copy = os.stat(
                        destination.name, dir_fd=ddir, follow_symlinks=False
                    )
                    if (identity(current_copy), current_copy.st_ctime_ns) != (
                        identity(checked_copy),
                        checked_copy.st_ctime_ns,
                    ):
                        raise ValueError("Published copy changed before source removal")
                    current = os.stat(
                        record["hold"], dir_fd=sdir, follow_symlinks=False
                    )
                    if (identity(current), current.st_ctime_ns) != (
                        identity(checked_source),
                        checked_source.st_ctime_ns,
                    ):
                        raise ValueError(
                            "Original changed during final verification; original restored"
                        )
                    os.unlink(record["hold"], dir_fd=sdir)
                    os.fsync(sdir)
                except BaseException:
                    rename_new(record["hold"], source.name, sdir)
                    raise
                record["phase"] = "moved"
                self.save()
                try:
                    os.unlink(temporary, dir_fd=ddir)
                    os.fsync(ddir)
                except FileNotFoundError:
                    pass
                return destination
            finally:
                os.close(source_fd)
