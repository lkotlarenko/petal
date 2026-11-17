"""Stage ordering/ownership tests with controlled encoder and verifier barriers."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from petal.capabilities import Capabilities
from petal.engine import Batch
from petal.models import Job, Settings, Status
from petal.process import Cancelled, ProcessError, Result, Runner
from petal.safety import fingerprint
from tests.test_core import media


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output = self.root / "source", self.root / "output"
        self.source.mkdir()
        self.jobs = []
        for i in range(5):
            path = self.source / f"file-{i}.mkv"
            path.write_bytes(b"original" * 1000)
            m = media()
            m.path = path
            self.jobs.append(Job(path, Path(path.name), self.output / path.name, media=m))
        self.metadata = {j.source: j.media for j in self.jobs}
        self.keys = {j.source: fingerprint(j.source) for j in self.jobs}
        self.caps = Capabilities(Settings().device, tested={"av1_vaapi:Balanced:8": True},
                                 decode_tested={"h264:8:av1_vaapi": False})

    def batch(self, count=3, auto_quality=False):
        return Batch(self.jobs[:count], self.source, self.output,
                     Settings(auto_quality=auto_quality), self.caps, self.root / "state")

    def encoder(self, callback=lambda *_: None):
        class FixtureRunner(Runner):
            def run(inner, args, **kwargs):
                assert args[0] == "ffmpeg" and "-init_hw_device" in args, args
                path = Path(args[-1])
                source = Path(args[args.index("-i") + 1])
                path.write_bytes(b"encoded")
                callback(source, path)
                return Result("", "", 0)
        return FixtureRunner()

    def assert_clean(self, batch):
        self.assertEqual(batch.store.data["temporary"], [])
        self.assertEqual(list(self.output.glob(".petal-*.partial.mkv")), [])
        for source, key in self.keys.items():
            self.assertEqual(fingerprint(source), key)

    def test_encode_two_overlaps_verify_one_without_publishing_unverified(self):
        batch = self.batch(count=3)
        first_verifying, second_encoding = threading.Event(), threading.Event()
        partial_counts = []
        def encode(source, path):
            partial_counts.append(len(list(self.output.glob(".petal-*.partial.mkv"))))
            if source == self.jobs[1].source:
                self.assertTrue(first_verifying.wait(3), "Verifier did not run concurrently")
                self.assertFalse(self.jobs[0].destination.exists(), "Unverified result was published")
                second_encoding.set()
        def verify(source, output, *args, **kwargs):
            if source.path == self.jobs[0].source:
                first_verifying.set()
                self.assertTrue(second_encoding.wait(3), "Encoding waited for the entire verification")
        batch.runner = self.encoder(encode)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify", side_effect=verify):
            batch.run()
        self.assertTrue(all(j.status == Status.COMPLETED for j in batch.jobs), [j.message for j in batch.jobs])
        self.assertLessEqual(max(partial_counts), 2)
        self.assertEqual(len(json.loads(batch.store.path.read_text())["jobs"]), 3)
        self.assert_clean(batch)

    def test_stop_cancels_both_active_stages_and_cleans_their_temporaries(self):
        batch = self.batch()
        verifying = threading.Event()
        def verify(*args, **kwargs):
            verifying.set()
            self.assertTrue(batch.cancel.wait(3))
            raise Cancelled()
        def encode(source, path):
            if source == self.jobs[1].source:
                self.assertTrue(verifying.wait(3))
                batch.stop()
                raise Cancelled()
        batch.runner = self.encoder(encode)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify", side_effect=verify):
            batch.run()
        self.assertTrue(all(j.status == Status.CANCELLED for j in batch.jobs), [j.message for j in batch.jobs])
        self.assertFalse(any(j.destination.exists() for j in batch.jobs))
        self.assert_clean(batch)

    def test_pause_drains_current_work_then_resume_starts_next_file(self):
        paused = threading.Event()
        batch = self.batch()
        batch.event = lambda kind, value: paused.set() if kind == "paused" else None
        encoded = []
        def encode(source, path):
            encoded.append(source)
            if source == self.jobs[1].source:
                batch.pause(True)
        batch.runner = self.encoder(encode)
        errors = []
        def run():
            try:
                batch.run()
            except Exception as error:
                errors.append(error)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(paused.wait(4))
                self.assertEqual(len(encoded), 2)
                self.assertTrue(all(j.status == Status.COMPLETED for j in batch.jobs[:2]))
            finally:
                batch.pause(False)
                thread.join(5)
                if thread.is_alive():
                    batch.stop()
                    thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(encoded), 3)
        self.assert_clean(batch)

    def test_no_savings_never_starts_expensive_verifier(self):
        batch = self.batch(count=1)
        def encode(source, path):
            path.write_bytes(b"larger" * 10000)
        batch.runner = self.encoder(encode)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify") as verify:
            batch.run()
            verify.assert_not_called()
        self.assertEqual(batch.jobs[0].status, Status.SKIPPED)
        self.assert_clean(batch)

    def test_first_three_new_successes_get_quality_reports_without_reencoding(self):
        batch = self.batch(count=5, auto_quality=True)
        batch.runner = self.encoder()
        report = {"status": "No concern in sampled scenes", "mean": 98., "p10": 96., "worst_scene": 97., "scenes": []}
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"), \
                patch("petal.engine.analyze_quality", return_value=report) as quality:
            batch.run()
        self.assertEqual(quality.call_count, 3)
        self.assertEqual(sum(bool(j.quality) for j in batch.jobs), 3)
        self.assertTrue(all(j.status == Status.COMPLETED for j in batch.jobs))
        stored = json.loads(batch.store.path.read_text())
        self.assertEqual(sum("mean" in j.get("quality", {}) for j in stored["jobs"].values()), 3)
        self.assert_clean(batch)

    def test_missing_vmaf_does_not_fail_verified_conversions(self):
        batch = self.batch(auto_quality=True)
        batch.runner = self.encoder()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"), \
                patch("petal.engine.analyze_quality", side_effect=ProcessError("No such filter: libvmaf")):
            batch.run()
        self.assertTrue(all(j.status == Status.COMPLETED for j in batch.jobs))
        self.assertTrue(all(j.quality.get("status") == "Unavailable" for j in batch.jobs))
        self.assert_clean(batch)

    def test_unavailable_quality_retries_on_resume_without_encoding(self):
        batch = self.batch(count=1, auto_quality=True)
        batch.runner = self.encoder()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"), \
                patch("petal.engine.analyze_quality", side_effect=ProcessError("input height must match")):
            batch.run()
        original_key = fingerprint(batch.jobs[0].destination)
        resumed = self.batch(count=1, auto_quality=True)
        resumed.runner = self.encoder(lambda *_: self.fail("Saved output must not be encoded again"))
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"), \
                patch("petal.engine.analyze_quality", return_value={"status": "No concern in sampled scenes", "mean": 98}) as quality:
            resumed.run()
        self.assertEqual(quality.call_count, 1)
        self.assertEqual(fingerprint(resumed.jobs[0].destination), original_key)
        self.assertEqual(resumed.jobs[0].quality["mean"], 98)
        self.assert_clean(resumed)

    def test_manual_quality_recheck_requires_unchanged_saved_output(self):
        batch = self.batch(count=1)
        batch.runner = self.encoder()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"):
            batch.run()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), \
                patch("petal.engine.analyze_quality", return_value={"status": "No concern in sampled scenes", "mean": 97}) as quality:
            batch.audit_quality(batch.jobs)
            self.assertEqual(quality.call_count, 1)
            batch.jobs[0].destination.write_bytes(b"changed externally")
            batch.audit_quality(batch.jobs)
            self.assertEqual(quality.call_count, 1)
        self.assertIn("No unchanged", batch.jobs[0].quality["reason"])

    def test_unchanged_resume_uses_recorded_proof_and_metadata_only(self):
        batch = self.batch(count=1)
        batch.runner = self.encoder()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"):
            batch.run()
        # Operational settings can change without re-encoding or repeating full decode.
        resumed = self.batch(count=1, auto_quality=True)
        resumed.settings = replace(resumed.settings, strict=False)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify") as verify:
            resumed.run()
        self.assertTrue(verify.call_args.kwargs["metadata_only"])
        self.assertEqual(resumed.jobs[0].status, Status.COMPLETED)
        self.assert_clean(resumed)

    def test_sampled_proof_is_upgraded_to_strict_once(self):
        batch = self.batch(count=1)
        batch.settings = replace(batch.settings, strict=False)
        batch.runner = self.encoder()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"):
            batch.run()
        upgraded = self.batch(count=1)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify") as verify:
            upgraded.run()
        self.assertFalse(verify.call_args.kwargs["metadata_only"])
        again = self.batch(count=1)
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify") as verify:
            again.run()
        self.assertTrue(verify.call_args.kwargs["metadata_only"])
        self.assert_clean(again)

    def test_cancel_quality_does_not_remove_published_verified_file(self):
        batch = self.batch(count=1, auto_quality=True)
        batch.runner = self.encoder()
        analyzing = threading.Event()
        def analysis(*args):
            analyzing.set()
            self.assertTrue(batch.cancel.wait(4))
            raise Cancelled()
        with patch("petal.engine.probe", side_effect=lambda path, _: self.metadata[path]), patch("petal.engine.verify"), \
                patch("petal.engine.analyze_quality", side_effect=analysis):
            thread = threading.Thread(target=batch.run)
            thread.start()
            try:
                self.assertTrue(analyzing.wait(4))
                self.assertTrue(batch.jobs[0].destination.exists())
            finally:
                batch.stop()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(batch.jobs[0].status, Status.COMPLETED)
        self.assertEqual(batch.jobs[0].quality["status"], "Cancelled")
        self.assertTrue(batch.jobs[0].destination.exists())
        self.assert_clean(batch)

    def test_recommendation_waits_for_old_verification_before_new_profile(self):
        batch=self.batch(count=2)
        batch.capabilities.tested['av1_vaapi:High Quality:8']=True
        observations=[]
        def encode(source,path):
            if source==self.jobs[0].source:
                batch.request_options(profile='High Quality',strict=False)
        def verify(source,output,config,*args,**kwargs):
            observations.append((source.path,config.profile,config.strict))
        batch.runner=self.encoder(encode)
        with patch('petal.engine.probe',side_effect=lambda path,_:self.metadata[path]), patch('petal.engine.verify',side_effect=verify):
            batch.run()
        self.assertEqual(observations,[(self.jobs[0].source,'Balanced',True),(self.jobs[1].source,'High Quality',False)])
        records=batch.store.data['jobs']
        self.assertEqual(records[str(self.jobs[0].relative)]['profile'],'Balanced')
        self.assertEqual(records[str(self.jobs[1].relative)]['profile'],'High Quality')
        self.assertEqual(records[str(self.jobs[0].relative)]['verification'],'full')
        self.assertEqual(records[str(self.jobs[1].relative)]['verification'],'sampled')
        self.assert_clean(batch)
