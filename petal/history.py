"""Durable UI history; these snapshots never authenticate an encoded output."""

import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from .models import Job, Media, Status
from .safety import sync_directory

FINISHED = {Status.COMPLETED, Status.SKIPPED, Status.FAILED}


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def encode_job(job):
    data = asdict(job)
    for key in (
        "source",
        "relative",
        "destination",
        "input_root",
        "relocated_to",
        "output_root",
    ):
        data[key] = str(data[key]) if data[key] is not None else None
    if data["media"]:
        data["media"]["path"] = str(data["media"]["path"])
    return data


def decode_job(data):
    data = dict(data)
    for key in (
        "source",
        "relative",
        "destination",
        "input_root",
        "relocated_to",
        "output_root",
    ):
        if data.get(key) is not None:
            data[key] = Path(data[key])
    data["status"] = Status(data["status"])
    if data.get("media"):
        values = dict(data["media"])
        values["path"] = Path(values["path"])
        data["media"] = Media(**values)
    return Job(**data)


class History:
    def __init__(self, directory):
        self.path = directory / "history.json"
        self.jobs = {}
        self.report = []
        self.output = None
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("jobs", []), list)
            or not isinstance(data.get("report", []), list)
        ):
            return
        for row in data.get("jobs", []):
            try:
                job = decode_job(row)
                if job.status in (Status.ENCODING, Status.VERIFYING):
                    job.status = Status.CANCELLED
                    job.message = "Interrupted · ready to resume"
                self.jobs[job.source] = job
            except (TypeError, ValueError, KeyError):
                continue
        for row in data.get("report", []):
            try:
                self.report.append(decode_job(row))
            except (TypeError, ValueError, KeyError):
                continue
        if isinstance(data, dict) and data.get("output"):
            self.output = Path(data["output"])

    def save(self):
        atomic_json(
            self.path,
            {
                "version": 1,
                "jobs": [encode_job(j) for j in self.jobs.values()],
                "report": [encode_job(j) for j in self.report],
                "output": str(self.output) if self.output else None,
            },
        )

    def set_report(self, jobs, output):
        self.report = [decode_job(encode_job(j)) for j in jobs]
        self.output = output


def summary(jobs):
    completed = [j for j in jobs if j.status == Status.COMPLETED]
    original = sum(j.size for j in completed)
    saved = sum(max(0, j.size - j.final_size) for j in completed)
    return {
        "completed": len(completed),
        "skipped": sum(j.status == Status.SKIPPED for j in jobs),
        "failed": sum(j.status == Status.FAILED for j in jobs),
        "cancelled": sum(j.status == Status.CANCELLED for j in jobs),
        "saved": saved,
        "original": original,
        "percent": 100 * saved / original if original else 0,
    }
