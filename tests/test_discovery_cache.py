"""Cache correctness, cancellation and the bounded discovery reader pool."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from petal.discovery import discover
from petal.process import Cancelled, Runner
from petal.scan_cache import ProbeCache, inspect_many
from tests.test_integration import generate


class DiscoveryCacheTests(unittest.TestCase):
    def test_real_cache_reuses_metadata_and_invalidates_changed_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source'; source.mkdir()
            video=source/'clip.unknown'; generate(source/'clip.mkv',160,90)
            (source/'clip.mkv').rename(video)
            with ProbeCache(root/'metadata.sqlite3') as cache:
                first=discover(source,root/'output',Runner(),cache=cache)
                self.assertEqual(len(first),1)
                with patch('petal.scan_cache.probe',side_effect=AssertionError('Cache should avoid probing')):
                    second=discover(source,root/'output',Runner(),cache=cache)
                self.assertEqual(first[0].media.streams,second[0].media.streams)
                self.assertEqual(cache.hits,1)
                video.write_bytes(b'changed file')
                failed=discover(source,root/'output',Runner(),cache=cache)
                self.assertEqual(failed,[])
                self.assertEqual(cache.misses,2)

    def test_cache_write_failure_does_not_turn_valid_video_into_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir();generate(source/'clip.mkv',160,90)
            with ProbeCache(root/'cache.sqlite3') as cache, patch.object(cache,'put',side_effect=sqlite3.OperationalError('full')):
                jobs=discover(source,root/'output',Runner(),cache=cache)
            self.assertEqual(len(jobs),1)
            self.assertIsNotNone(jobs[0].media)

    def test_pool_has_two_readers_preserves_order_and_cancels(self):
        entered=threading.Barrier(2);lock=threading.Lock();counts=[0,0]
        def fake(path,runner,cache):
            with lock:
                counts[0]+=1;counts[1]=max(counts)
            entered.wait(timeout=5)
            with lock:
                counts[0]-=1
            return path,None,None
        paths=[Path(str(i)) for i in range(8)]
        with patch('petal.scan_cache.inspect',side_effect=fake):
            self.assertEqual([p for p,_,_ in inspect_many(paths,Runner())],paths)
        self.assertEqual(counts[1],2)
        runner=Runner();runner.cancel.set()
        with self.assertRaises(Cancelled):
            list(inspect_many(paths,runner))
