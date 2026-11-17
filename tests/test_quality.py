from pathlib import Path
import json
import tempfile
import unittest

from petal.models import Settings
from petal.process import Runner
from petal.media import probe
from petal.quality import analyze, sample_windows, vmaf_command
from tests.test_integration import generate


class QualityWindowTests(unittest.TestCase):
    def test_windows_are_reproducible_stratified_and_non_overlapping(self):
        for duration in (12, 60, 3600):
            scenes = sample_windows(duration, "same-source")
            self.assertEqual(scenes, sample_windows(duration, "same-source"))
            self.assertNotEqual(scenes, sample_windows(duration, "different-source"))
            self.assertEqual(len(scenes), 3)
            for start, length in scenes:
                self.assertGreater(start, 0)
                self.assertLessEqual(start + length, duration)
            for (start, length), (following, _) in zip(scenes, scenes[1:]):
                self.assertLessEqual(start + length, following)
        self.assertEqual(sample_windows(1, "x"), [])
        self.assertEqual(len(sample_windows(3, "x")), 1)

    def test_operational_settings_do_not_invalidate_output_identity(self):
        self.assertEqual(Settings(strict=True, auto_quality=True).key,
                         Settings(strict=False, auto_quality=False).key)


class VMAFIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        help_text = Runner().run(["ffmpeg", "-hide_banner", "-h", "filter=libvmaf"], check=False)
        if "libvmaf AVOptions" not in help_text.stdout:
            raise unittest.SkipTest("FFmpeg was built without libvmaf")

    def test_real_vmaf_distinguishes_identical_and_blurred_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, blurred = root / "original.mkv", root / "blurred.mkv"
            generate(source, 320, 180, seconds="3")
            original = probe(source, Runner())
            same = analyze(original, source, "fixture", Runner())
            Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-i", str(source),
                          "-vf", "boxblur=8:2", "-c:v", "libx264", "-crf", "45", str(blurred)])
            damaged = analyze(original, blurred, "fixture", Runner())
            self.assertGreater(same["mean"], 95, same)
            self.assertGreater(same["mean"] - damaged["mean"], 10, (same, damaged))
            self.assertEqual(damaged["status"], "Review suggested")
            self.assertEqual(len(same["scenes"]), 1)
            self.assertGreater(same["scenes"][0]["frames"], 8)
            args = vmaf_command(original, blurred, .23, 2)
            self.assertNotIn("-r", args)
            self.assertIn("-threads:v:0", args)

    def test_real_batch_persists_automatic_quality_and_reuses_it(self):
        from petal.capabilities import Capabilities
        from petal.discovery import discover
        from petal.engine import Batch
        from petal.models import Status
        from tests.test_integration import CpuFixtureRunner
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            source.mkdir()
            generate(source / "scene.mkv", 320, 180, seconds="3")
            runner = CpuFixtureRunner()
            jobs = discover(source, output, runner)
            config = Settings(strict=False)
            caps = Capabilities(config.device, tested={"av1_vaapi:Balanced:8": True})
            batch = Batch(jobs, source, output, config, caps, root / "state")
            batch.runner = runner
            batch.run()
            self.assertEqual(jobs[0].status, Status.COMPLETED, jobs[0].message)
            self.assertIn("mean", jobs[0].quality, jobs[0].quality)
            record = json.loads(batch.store.path.read_text())["jobs"]["scene.mkv"]
            self.assertEqual(record["verification"], "sampled")
            self.assertEqual(record["quality"]["mean"], jobs[0].quality["mean"])
            resumed = Batch(discover(source, output, runner), source, output, config, caps, root / "state")
            resumed.runner = runner
            with patch("petal.engine.analyze_quality", side_effect=AssertionError("Quality should not repeat on resume")):
                resumed.run()
            self.assertEqual(resumed.jobs[0].quality["mean"], jobs[0].quality["mean"])

    def test_different_decoded_and_declared_geometry_is_normalized_and_reported(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "original.mkv", root / "padded.mkv"
            generate(source, 320, 180, seconds="3")
            Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-i", str(source),
                          "-vf", "pad=336:192:0:0", "-c:v", "ffv1", str(output)])
            original, declared = probe(source, Runner()), probe(output, Runner())
            # Simulate a container exposing display dimensions while decoded
            # pictures still carry an aligned width/height, as seen in hardware paths.
            declared.video.update(width=320, height=180)
            with patch("petal.quality.probe", return_value=declared):
                result = analyze(original, output, "padded", Runner())
            self.assertNotIn("mean", result)
            self.assertEqual(result["geometry"]["output_decoded"], [336, 192])
            self.assertEqual(result["resolution"], [320, 180])
            self.assertEqual(result["status"], "Geometry mismatch")
            self.assertIn("container declares", result["geometry_note"])

    def test_downscaled_video_compares_on_output_canvas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "large.mkv", root / "small.mkv"
            generate(source, 640, 360, seconds="3")
            Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-i", str(source),
                          "-vf", "scale=320:180", "-c:v", "ffv1", str(output)])
            result = analyze(probe(source, Runner()), output, "downscale", Runner())
            self.assertEqual(result["resolution"], [320, 180])
            self.assertGreater(result["mean"], 95)


    def test_builtin_4k_model_runs_on_real_4k_frames(self):
        result = Runner().run([
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=3840x2160:rate=1", "-filter_complex",
            "[0:v]split[ref][dist];[dist][ref]libvmaf=model=version=vmaf_4k_v0.6.1neg:n_threads=2",
            "-frames:v", "1", "-f", "null", "-"], timeout=30)
        self.assertEqual(result.returncode, 0)


class GeometryRegressionTests(unittest.TestCase):
    def test_verifier_rejects_1082_decoded_frames_before_publication(self):
        from unittest.mock import patch
        from petal.safety import verify, VerificationError
        from tests.test_core import media
        original=media(1920,1080)
        output=media(1920,1080)
        output.video['codec_name']='av1'
        with patch('petal.safety.probe',return_value=output), patch('petal.safety.decoded_sizes',return_value=[(1920,1082)]):
            with self.assertRaisesRegex(VerificationError,'Decoded frame dimensions'):
                verify(original,Path('/output/video.mkv'),Settings(),Runner(),metadata_only=True)
