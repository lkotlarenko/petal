from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import replace
import logging
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from typing import Callable

from .capabilities import Capabilities
from .assets import prepare_artwork, cleanup_artwork
from .commands import Unsupported, encode_command, stream_plan
from .discovery import validate_roots, validate_input
from .media import probe
from .media import target_size
from .quality import analyze as analyze_quality, needs_analysis
from .models import Job, Settings, Status, xdg_path
from .process import Cancelled, ProcessError, Runner, progress_seconds
from .safety import (StateStore, enough_savings, fingerprint, new_temporary, output_lock,
                     publish, safe_parent, verify)


class Batch:
    """One encode at a time; control methods are safe to invoke from the UI thread."""
    def __init__(self, jobs: list[Job], source: Path, output: Path, settings: Settings,
                 capabilities: Capabilities, state_dir: Path,
                 event: Callable[[str, object], None] = lambda *_: None, *, selection: bool = False):
        self.jobs = jobs
        self.selection = selection
        if selection:
            self.source, self.output = source.expanduser().resolve(), output.expanduser().resolve()
            for job in jobs:
                if job.status == Status.FAILED and not job.media:
                    continue
                validate_input(job.source if job.explicit_file else job.input_root or self.source,
                               self.output, explicit_file=job.explicit_file)
        else:
            self.source, self.output = validate_roots(source, output)
        self.settings, self.capabilities, self.event = settings, capabilities, event
        self.store = StateStore(state_dir, self.source, self.output, namespace="selection" if selection else None)
        self.cancel = threading.Event()
        self.condition = threading.Condition()
        self.pause_requested = False
        self.skips: set[Path] = set()
        self.runner = Runner(self.cancel, self.log)
        self.verifier: ThreadPoolExecutor | None = None
        self.pending: Future | None = None
        self.failed_decoders: set[tuple] = set()
        self.quality_pool: ThreadPoolExecutor | None = None
        self.quality_futures: list[Future] = []
        self.quality_count = 0
        self.quality_unavailable = False
        self.pending_options: dict = {}

    def log(self, message: str):
        logging.getLogger("petal").info(message)
        self.event("log", message)

    def pause(self, requested: bool):
        with self.condition:
            self.pause_requested = requested
            self.condition.notify_all()

    def stop(self):
        self.cancel.set()
        self.pause(False)

    def request_options(self, *, profile: str | None = None, strict: bool | None = None):
        """Apply recommendation actions only after old verification is drained."""
        changes = {}
        if profile is not None:
            proposed = replace(self.settings, profile=profile)
            if not any(self.capabilities.supports(proposed, bits) for bits in (8, 10)):
                raise ValueError("The suggested profile needs a passing hardware check")
            changes["profile"] = profile
        if strict is not None:
            changes["strict"] = strict
        with self.condition:
            self.pending_options.update(changes)
            self.condition.notify_all()

    def skip(self, paths: list[Path]):
        with self.condition:
            self.skips.update(paths)

    def update(self, job: Job, status: Status, message: str = ""):
        job.status, job.message = status, message
        if status == Status.SKIPPED:
            self.log(f"Skipped: {job.relative}: {message}")
        self.event("job", job)

    def verification_progress(self, job: Job, fields: dict[str, str]):
        self.event("verification", {"job": job, "fields": fields,
                                    "seconds": progress_seconds(fields)})

    def remember(self, job: Job):
        with self.store.lock:
            self.store.data["jobs"][str(job.relative)] = {
                "source": job.source_key, "destination": str(job.destination),
                "output": fingerprint(job.destination), "settings": self.settings.encoding_key(job.media),
                "size": job.final_size, "status": "Completed",
                "resolution": self.settings.resolution, "dimensions": job.output_dimensions,
                "encoder_quality": job.encoder_quality,
                "profile": job.encoder_profile, "codec": job.encoder_codec,
                "encode_seconds": job.encode_seconds, "verify_seconds": job.verify_seconds,
                "verification": "full" if self.settings.strict or not job.media.duration else "sampled",
                "quality": job.quality,
            }
            self.store.save()

    def resume(self, job: Job) -> bool:
        with self.store.lock:
            record = dict(self.store.data["jobs"].get(str(job.relative), {}))
        settings_keys = {self.settings.encoding_key(job.media), self.settings.key,
                         self.settings.legacy_key, self.settings.legacy_identity_key}
        full_keys = {replace(self.settings, strict=True).legacy_key}
        settings_keys.update(replace(self.settings, strict=strict).legacy_key for strict in (True, False))
        if self.settings.incompatible == "compatible":
            previous = replace(self.settings, incompatible="skip")
            try:
                # A source accepted by the old strict policy needs no stream conversions.
                stream_plan(job.media, previous)
                settings_keys.update({previous.encoding_key(job.media), previous.key, previous.legacy_key,
                                      previous.legacy_identity_key,
                                      replace(previous, strict=not previous.strict).legacy_key})
                full_keys.add(replace(previous, strict=True).legacy_key)
            except Unsupported:
                pass
        if (record.get("status") != "Completed" or record.get("source") != job.source_key
                or record.get("destination") != str(job.destination)
                or record.get("settings") not in settings_keys
                or not job.destination.exists()):
            return False
        if fingerprint(job.destination) != record.get("output"):
            return False
        self.update(job, Status.VERIFYING, "Checking saved output against current source")
        proof = record.get("verification", "full" if record.get("settings") in full_keys else "sampled")
        reuse_proof = not self.settings.strict or proof == "full"
        verify(job.media, job.destination, self.settings, self.runner, metadata_only=reuse_proof,
               progress=lambda fields: self.verification_progress(job, fields))
        if fingerprint(job.source) != job.source_key:
            raise RuntimeError("Source changed while verifying saved output")
        job.final_size = job.destination.stat().st_size
        job.output_dimensions = target_size(job.media, self.settings)
        job.encoder_quality = record.get("encoder_quality")
        job.encoder_profile = record.get("profile", self.settings.profile)
        job.encoder_codec = record.get("codec", self.settings.codec)
        job.encode_seconds = record.get("encode_seconds")
        job.verify_seconds = record.get("verify_seconds")
        job.quality = record.get("quality", {})
        if not reuse_proof and self.settings.strict:
            self.remember(job)
        job.progress = 1
        self.update(job, Status.COMPLETED, "Reused verified output (unchanged fingerprints)" if reuse_proof else "Reverified existing output")
        if job.quality:
            self.event("quality", {"job": job, "report": job.quality})
        if needs_analysis(job.quality):
            self.schedule_quality(job)
        return True

    def drain_verifier(self):
        if self.pending is not None:
            self.pending.result()
            self.pending = None

    def apply_pending_options(self):
        if not self.pending_options:
            return
        self.drain_verifier()
        with self.condition:
            changes, self.pending_options = self.pending_options, {}
            self.settings = replace(self.settings, **changes)
        if "profile" in changes:
            self.quality_count = 0
        self.event("settings_applied", self.settings)

    def run(self):
        with output_lock(self.output):
            for message in self.store.recover(self.output):
                self.log(message)
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="PetalVerify") as verifier, \
                    ThreadPoolExecutor(max_workers=1, thread_name_prefix="PetalQuality") as quality_pool:
                self.verifier = verifier
                self.quality_pool = quality_pool
                try:
                    for job in self.jobs:
                        if self.pending_options:
                            self.apply_pending_options()
                        if self.pause_requested:
                            self.drain_verifier()
                        with self.condition:
                            while self.pause_requested and not self.cancel.is_set():
                                self.event("paused", True)
                                self.condition.wait()
                        if self.cancel.is_set():
                            if job.status == Status.WAITING:
                                self.update(job, Status.CANCELLED, "Batch stopped")
                            continue
                        if job.source in self.skips:
                            self.update(job, Status.SKIPPED, "Skipped by user")
                            continue
                        if job.status == Status.SKIPPED:
                            self.log(f"Skipped: {job.relative}: {job.message}")
                            continue
                        if job.status == Status.FAILED and not job.media:
                            continue
                        self.process(job)
                finally:
                    self.drain_verifier()
                    self.apply_pending_options()
                    for future in self.quality_futures:
                        future.result()
                    self.apply_pending_options()
                    self.verifier = None
                    self.quality_pool = None
            self.event("finished", dict(Counter(j.status.value for j in self.jobs)))

    def process(self, job: Job):
        temporary = None
        artwork = {}
        try:
            if self.selection:
                validate_input(job.source if job.explicit_file else job.input_root or self.source,
                               self.output, explicit_file=job.explicit_file)
            job.source_key = fingerprint(job.source)
            # Refresh metadata; discovery may have happened hours ago.
            job.media = probe(job.source, self.runner)
            job.size = job.source.stat().st_size
            plan = stream_plan(job.media, self.settings)
            job.output_dimensions = target_size(job.media, self.settings)
            job.encoder_quality = self.settings.quality_for_size(job.output_dimensions)
            job.encoder_profile = self.settings.profile
            job.encoder_codec = self.settings.codec
            for warning in plan.warnings:
                self.log(f"{job.relative}: {warning}")
            safe_parent(job.destination, self.output)
            if self.resume(job):
                return
            if job.destination.exists() or job.destination.is_symlink():
                raise Unsupported("Destination already exists and does not match verified Petal state; preserved untouched")
            job.quality = {}  # A fresh encode must never inherit a previous file's score.
            if not self.capabilities.supports(self.settings, job.media.bit_depth):
                raise Unsupported(f"{self.settings.codec} / {self.settings.profile} / {job.media.bit_depth}-bit has no passing hardware test. Run Hardware check; no CPU encoding fallback")
            if shutil.disk_usage(job.destination.parent).free < 64 * 1024 * 1024:
                raise RuntimeError("Less than 64 MiB free at destination")
            temporary = new_temporary(job.destination)
            self.store.track(temporary)
            artwork = prepare_artwork(job.media, self.settings, job.destination.parent, self.store, self.runner)
            self.log(f"Target {job.output_dimensions[0]}×{job.output_dimensions[1]} · {self.settings.profile} · "
                     f"{self.settings.codec} CQP {job.encoder_quality}: {job.relative}")
            self.update(job, Status.ENCODING, "; ".join(plan.warnings))
            def progress(fields):
                seconds = progress_seconds(fields)
                job.progress = min(.999, seconds / job.media.duration) if seconds is not None and job.media.duration else 0
                self.event("progress", {"job": job, "fields": fields, "seconds": seconds})
            began = time.monotonic()
            decode_key = (job.media.video.get("codec_name"), job.media.video.get("pix_fmt"))
            use_gpu_decode = (not job.media.rotation and decode_key not in self.failed_decoders
                              and self.capabilities.can_decode(job.media, self.settings))
            if use_gpu_decode:
                try:
                    self.runner.run(encode_command(job.media, temporary, self.settings, artwork=artwork),
                                    timeout=None, progress=progress)
                except (ProcessError, Unsupported) as error:
                    if isinstance(error, ProcessError) and any(text in str(error).lower() for text in (
                            "unrecognized option", "option not found", "error splitting the argument list",
                            "no space left on device", "permission denied", "read-only file system")):
                        raise
                    self.failed_decoders.add(decode_key)
                    self.log(f"GPU decode/scaling failed for {job.relative}: {error}\n"
                             "This source format will use software decoding for the rest of this batch; encoding remains VA-API.")
                    temporary.unlink(missing_ok=True)
                    job.progress = 0
                    use_gpu_decode = False
            if not use_gpu_decode:
                self.log(f"Software decode/scaling → {self.settings.codec}: {job.relative}")
                self.runner.run(encode_command(job.media, temporary, self.settings, hardware_decode=False, artwork=artwork),
                                timeout=None, progress=progress)
            job.encode_seconds = time.monotonic() - began
            self.log(f"Encode finished in {job.encode_seconds:.2f}s: {job.relative}")
            final_size = temporary.stat().st_size
            if not enough_savings(job.size, final_size, self.settings.min_savings):
                self.update(job, Status.SKIPPED, f"No savings: {final_size:,} bytes; requires {self.settings.min_savings:g}% reduction (decode verification avoided)")
                return
            self.update(job, Status.VERIFYING, "Queued for verification")
            if self.verifier is not None:
                self.drain_verifier()  # Backpressure: at most one verifier + one encoder.
                if self.cancel.is_set():
                    raise Cancelled()
                self.pending = self.verifier.submit(self.finish, job, temporary, plan.warnings)
            else:
                self.finish(job, temporary, plan.warnings)
            temporary = None  # finish() now owns cleanup/publication of the MKV.
        except Cancelled:
            self.update(job, Status.CANCELLED, "Stopped safely; original untouched")
        except Unsupported as error:
            self.update(job, Status.SKIPPED, str(error))
        except Exception as error:
            self.log(f"{job.relative}: {type(error).__name__}: {error}")
            self.update(job, Status.FAILED, str(error))
        finally:
            cleanup_artwork(artwork, self.store, self.runner)
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                    self.store.untrack(temporary)
                except OSError as error:
                    self.log(f"Temporary cleanup deferred to recovery: {error}")

    def finish(self, job: Job, temporary: Path, warnings: list[str]):
        began = time.monotonic()
        try:
            if self.cancel.is_set():
                raise Cancelled()
            self.update(job, Status.VERIFYING,
                        "Full CPU decode (4 threads); next GPU encode can run concurrently" if self.settings.strict
                        else "Fast verification: metadata, packet timelines and sampled CPU decode")
            verify(job.media, temporary, self.settings, self.runner,
                   progress=lambda fields: self.verification_progress(job, fields))
            if fingerprint(job.source) != job.source_key:
                raise RuntimeError("Source changed during encoding/verification; output rejected")
            if self.cancel.is_set():
                raise Cancelled()
            job.verify_seconds = time.monotonic() - began
            safe_parent(job.destination, self.output)
            publish(temporary, job.destination)
            job.final_size = job.destination.stat().st_size
            job.progress = 1
            self.remember(job)
            self.update(job, Status.COMPLETED, "; ".join(warnings) or "Verified and safely published")
            self.log(f"Verification/publication finished in {time.monotonic() - began:.2f}s: {job.relative}")
            self.schedule_quality(job)
        except Cancelled:
            self.update(job, Status.CANCELLED, "Verification cancelled; original untouched")
        except Exception as error:
            self.log(f"{job.relative}: {type(error).__name__}: {error}")
            self.update(job, Status.FAILED, str(error))
        finally:
            try:
                temporary.unlink(missing_ok=True)
                self.store.untrack(temporary)
            except OSError as error:
                self.log(f"Temporary cleanup deferred to recovery: {error}")

    def schedule_quality(self, job: Job):
        # Called from both the scheduler (resume) and verifier (new output).
        with self.store.lock:
            if (not self.settings.auto_quality or self.quality_pool is None or self.quality_count >= 3
                    or not job.media or not job.media.duration or job.media.duration < 2):
                return
            self.quality_count += 1
            job.quality = {"status": "Queued", "slot": self.quality_count, "limit": 3}
            self.event("quality", {"job": job, "report": job.quality})
            self.quality_futures.append(self.quality_pool.submit(self.quality_check, job))

    def audit_quality(self, jobs: list[Job]):
        """Recheck matching saved outputs without launching a video encoder."""
        with output_lock(self.output):
            for job in jobs:
                if self.cancel.is_set():
                    break
                try:
                    with self.store.lock:
                        record = dict(self.store.data["jobs"].get(str(job.relative), {}))
                    job.source_key = fingerprint(job.source)
                    if (record.get("status") != "Completed" or record.get("source") != job.source_key
                            or record.get("destination") != str(job.destination)
                            or record.get("output") != fingerprint(job.destination)):
                        raise RuntimeError("No unchanged, previously verified output matches this source. Scan and compress it first.")
                    job.media = probe(job.source, self.runner)
                    job.final_size = job.destination.stat().st_size
                    job.output_dimensions = tuple(record["dimensions"]) if record.get("dimensions") else None
                    job.encoder_profile, job.encoder_codec = record.get("profile"), record.get("codec")
                    job.encoder_quality = record.get("encoder_quality")
                    job.encode_seconds, job.verify_seconds = record.get("encode_seconds"), record.get("verify_seconds")
                    job.progress = 1
                    self.update(job, Status.COMPLETED, "Unchanged saved verified output · Rechecking sampled quality")
                    self.quality_check(job)
                except Cancelled:
                    break
                except Exception as error:
                    job.quality = {"status": "Unavailable", "reason": str(error)}
                    self.event("quality", {"job": job, "report": job.quality})

    def quality_check(self, job: Job):
        try:
            if self.cancel.is_set():
                raise Cancelled()
            if self.quality_unavailable:
                report = {"status": "Unavailable", "reason": "VMAF was unavailable in the earlier automatic check"}
            else:
                original_output = fingerprint(job.destination)
                with self.store.lock:
                    expected = self.store.data["jobs"].get(str(job.relative), {}).get("output")
                if expected != original_output:
                    raise RuntimeError("Output changed before quality analysis")
                if fingerprint(job.source) != job.source_key:
                    raise RuntimeError("Source changed before quality analysis")
                def progress(report):
                    self.event("quality", {"job": job, "report": report})
                report = analyze_quality(job.media, job.destination, job.source_key, self.runner, progress)
                if fingerprint(job.source) != job.source_key or fingerprint(job.destination) != original_output:
                    raise RuntimeError("Source/output changed during quality analysis")
        except Cancelled:
            report = {"status": "Cancelled", "reason": "Quality check cancelled; verified output retained"}
        except Exception as error:
            report = {"status": "Unavailable", "reason": str(error)}
            text = str(error).lower()
            if (("no such filter" in text and "libvmaf" in text)
                    or "could not load libvmaf model" in text or "could not load vmaf model" in text):
                self.quality_unavailable = True
        job.quality = report
        try:
            with self.store.lock:
                record = self.store.data["jobs"].get(str(job.relative))
                if record:
                    record["quality"] = report
                    self.store.save()
        except OSError as error:
            self.log(f"Could not persist quality report: {error}")
        self.log(f"Quality check for {job.relative}: {report.get('status')}; "
                 f"mean={report.get('mean', 'unavailable')}, p10={report.get('p10', 'unavailable')}")
        self.event("quality", {"job": job, "report": report})


def benchmark(media, directory: Path, settings: Settings, capabilities: Capabilities,
              runner: Runner, seconds: float = 8) -> list[dict]:
    """Encode identical middle excerpts; retained samples are never full batch outputs."""
    if not 1 <= seconds <= 60:
        raise ValueError("Sample length must be 1–60 seconds")
    if not media.duration or media.duration < 1:
        raise ValueError("Sample comparison requires a known duration of at least one second")
    directory = directory.expanduser().resolve()
    if directory == media.path.resolve().parent:
        raise ValueError("Comparison samples need a separate directory from the source video")
    directory.mkdir(parents=True, exist_ok=True)
    with output_lock(directory):
        store = StateStore(xdg_path("state"), media.path.resolve(), directory)
        for message in store.recover(directory):
            runner.log(message)
        for warning in stream_plan(media, settings).warnings:
            runner.log(warning)
        return _benchmark_locked(media, directory, settings, capabilities, runner, seconds, store)


def _benchmark_locked(media, directory, settings, capabilities, runner, seconds, store):
    artwork = prepare_artwork(media, settings, directory, store, runner)
    try:
        return _benchmark_samples(media, directory, settings, capabilities, runner, seconds, store, artwork)
    finally:
        cleanup_artwork(artwork, store, runner)


def _benchmark_samples(media, directory, settings, capabilities, runner, seconds, store, artwork):
    duration = min(seconds, media.duration)
    start = max(0, (media.duration - duration) / 2)
    results = []
    for profile in ("High Quality", "Balanced", "Maximum Savings"):
        config = replace(settings, profile=profile, strict=True)
        if not capabilities.supports(config, media.bit_depth):
            results.append({"profile": profile, "error": "No passing hardware test"})
            continue
        destination = directory / f"sample-{profile.lower().replace(' ', '-')}-{uuid.uuid4().hex[:10]}.mkv"
        temporary = new_temporary(destination)
        store.track(temporary)
        began = time.monotonic()
        try:
            runner.run(encode_command(media, temporary, config, hardware_decode=False,
                                      sample_seconds=duration, sample_start=start, artwork=artwork), timeout=180)
            encode_seconds = time.monotonic() - began
            verify(media, temporary, config, runner, expected_duration=duration)
            ssim = None
            metric_error = None
            try:
                w, h = target_size(media, config)
                pix = "yuv420p10le" if media.bit_depth == 10 else "yuv420p"
                comparison = runner.run([
                    "ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-ss", str(start),
                    "-protocol_whitelist", "file,pipe", "-i", str(media.path),
                    "-protocol_whitelist", "file,pipe", "-i", str(temporary),
                    "-filter_complex",
                    f"[0:{media.video['index']}]scale={w}:{h}:flags=lanczos,format={pix},setsar=1,setpts=PTS-STARTPTS[ref];"
                    f"[1:v:0]scale={w}:{h}:flags=lanczos,format={pix},setsar=1,setpts=PTS-STARTPTS[dist];[dist][ref]ssim=shortest=1",
                    "-t", str(duration), "-an", "-sn", "-f", "null", "-"], timeout=180)
                match = re.search(r"All:([0-9.]+)", comparison.stderr)
                if match:
                    ssim = float(match[1])
                else:
                    metric_error = "SSIM summary was unavailable"
            except Cancelled:
                raise
            except Exception as error:
                metric_error = str(error)
            publish(temporary, destination)
            results.append({"profile": profile, "quality": config.quality_for_size(target_size(media, config)),
                            "dimensions": list(target_size(media, config)), "bytes": destination.stat().st_size,
                            "elapsed": round(time.monotonic() - began, 3), "path": str(destination),
                            "encode_seconds": round(encode_seconds, 3), "ssim": ssim,
                            "metric_error": metric_error, "source_start": start, "duration": duration})
        except Cancelled:
            raise
        except Exception as error:
            results.append({"profile": profile, "error": str(error)})
        finally:
            temporary.unlink(missing_ok=True)
            store.untrack(temporary)
    return results
