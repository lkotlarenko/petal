"""Cancellable subprocesses. No shells; both output pipes are always drained."""
from __future__ import annotations

import collections
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable


class Cancelled(Exception):
    pass


class ProcessError(RuntimeError):
    pass


@dataclass
class Result:
    stdout: str
    stderr: str
    returncode: int


class Runner:
    def __init__(self, cancel: threading.Event | None = None, log: Callable[[str], None] = lambda _: None):
        self.cancel = cancel or threading.Event()
        self.log = log

    def run(self, args: list[str], *, timeout: float | None = 120,
            progress: Callable[[dict[str, str]], None] | None = None,
            check: bool = True, env: dict | None = None,
            line_callback: Callable[[str], None] | None = None, nice: int = 0) -> Result:
        if self.cancel.is_set():
            raise Cancelled()
        self.log("$ " + repr(args))
        if not 0 <= nice <= 19:
            raise ValueError("Background priority must be between 0 and 19")
        child = "--petal-child" if getattr(sys, "frozen", False) else str(Path(__file__).with_name("_child.py"))
        wrapped = [sys.executable, child, str(os.getpid()), str(nice), *args]
        proc = subprocess.Popen(wrapped, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True,
                                env=env or dict(os.environ, LIBVA_DRIVER_NAME="radeonsi"), bufsize=0)
        started = time.monotonic()
        output: list[bytes] = []
        output_size = 0
        errors = collections.deque(maxlen=150)
        corruption = []
        def record_error(line):
            errors.append(line)
            if not corruption and any(term in line.lower() for term in (
                    "file ended prematurely", "packet corrupt", "corrupt decoded frame", "error during demuxing")):
                corruption.append(line)
            self.log(line)
        buffers = {"out": b"", "err": b""}
        fields: dict[str, str] = {}
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, "out")
        selector.register(proc.stderr, selectors.EVENT_READ, "err")
        try:
            while selector.get_map():
                if self.cancel.is_set():
                    raise Cancelled()
                if timeout and time.monotonic() - started > timeout:
                    raise ProcessError(f"Process exceeded {timeout:g}s timeout")
                for key, _ in selector.select(0.15):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    kind = key.data
                    if kind == "out" and not progress and not line_callback:
                        output_size += len(block)
                        if output_size > 32 * 1024 * 1024:
                            raise ProcessError("Subprocess metadata exceeded 32 MiB")
                        output.append(block)
                    buffers[kind] += block
                    lines = buffers[kind].split(b"\n")
                    buffers[kind] = lines.pop()[-65536:]
                    for raw in lines:
                        line = raw.decode("utf-8", "replace").rstrip()
                        if kind == "err":
                            record_error(line)
                        elif line_callback:
                            line_callback(line)
                        elif progress and "=" in line:
                            name, value = line.split("=", 1)
                            fields[name] = value
                            if name == "progress":
                                progress(dict(fields))
                                fields.clear()
            # wait with cancellation even if a child closed its pipes early
            while proc.poll() is None:
                if self.cancel.wait(0.1):
                    raise Cancelled()
                if timeout and time.monotonic() - started > timeout:
                    raise ProcessError("Process timed out after closing output pipes")
            if buffers["err"]:
                tail = buffers["err"].decode("utf-8", "replace")
                record_error(tail)
            result = Result(b"".join(output).decode("utf-8", "replace"),
                            "\n".join(errors), proc.returncode)
            if check and (result.returncode or corruption):
                reason = (corruption[0] + "\n") if corruption else ""
                raise ProcessError(f"Process exited {result.returncode}:\n{reason}{result.stderr[-6000:]}")
            return result
        finally:
            # A child can outlive a parent that closed its pipes. Always clean the group.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            selector.close()
            proc.stdout.close()
            proc.stderr.close()


def progress_seconds(fields: dict[str, str]) -> float | None:
    try:
        return max(0.0, int(fields["out_time_us"]) / 1_000_000)
    except (KeyError, ValueError):
        try:
            h, m, s = fields["out_time"].split(":")
            return max(0.0, int(h) * 3600 + int(m) * 60 + float(s))
        except (KeyError, ValueError):
            return None
