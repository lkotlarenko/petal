"""Disposable metadata acceleration. Encoding always probes its input afresh."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading

from .media import probe
from .models import Media
from .process import Cancelled, Runner

MISS = object()


class ProbeCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (path TEXT PRIMARY KEY, identity TEXT, payload TEXT)')
        executable = Path(shutil.which('ffprobe') or '/missing/ffprobe')
        try:
            stat = executable.stat()
            self.tool = f'{executable.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'
        except OSError:
            self.tool = 'missing'
        self.hits = self.misses = 0

    def identity(self, path: Path):
        stat = path.stat()
        return hashlib.sha256(f'1:{self.tool}:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}'.encode()).hexdigest()

    def get(self, path, identity):
        with self.lock:
            row = self.db.execute('SELECT payload FROM metadata WHERE path=? AND identity=?', (str(path), identity)).fetchone()
            if row:
                try:
                    payload = json.loads(row[0])
                    result = Media(path, payload['streams'], payload['format'], payload['chapters']) if payload is not None else None
                    self.hits += 1
                    return result
                except (ValueError, KeyError, TypeError):
                    pass
            self.misses += 1
        return MISS

    def put(self, path, identity, media):
        # Ignore files modified while probing; stale cache can never authenticate an encode.
        if identity != self.identity(path):
            return
        payload = {'streams': media.streams, 'format': media.format, 'chapters': media.chapters} if media else None
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?,?)', (str(path), identity, json.dumps(payload)))
            self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def inspect(path: Path, runner: Runner, cache: ProbeCache | None):
    try:
        if runner.cancel.is_set():
            raise Cancelled()
        identity = cache.identity(path) if cache else None
        try:
            info = cache.get(path, identity) if cache else MISS
        except sqlite3.Error as error:
            runner.log(f'Discovery cache read failed; probing directly: {error}')
            info = MISS
        if info is MISS:
            try:
                info = probe(path, runner)
            except ValueError as error:
                if str(error) != 'No playable video stream':
                    raise
                info = None
            if cache:
                try:
                    cache.put(path, identity, info)
                except (OSError, sqlite3.Error) as error:
                    runner.log(f'Discovery cache write failed; metadata is still usable: {error}')
        return path, info, None
    except Cancelled:
        raise
    except Exception as error:
        return path, None, error


def inspect_many(paths, runner: Runner, cache: ProbeCache | None = None):
    """Two readers, four pending tasks, deterministic order and shared cancellation."""
    iterator = iter(paths)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='PetalScan') as pool:
        pending = deque()
        try:
            for _ in range(4):
                path = next(iterator, None)
                if path is not None:
                    pending.append(pool.submit(inspect, path, runner, cache))
            while pending:
                if runner.cancel.is_set():
                    raise Cancelled()
                yield pending.popleft().result()
                path = next(iterator, None)
                if path is not None:
                    pending.append(pool.submit(inspect, path, runner, cache))
        finally:
            for future in pending:
                future.cancel()
