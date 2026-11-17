"""Real FFmpeg fixtures. CPU AV1 is a TEST ADAPTER, never a product fallback."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from petal.capabilities import detect, Capabilities
from petal.commands import encode_command, quality_args, device_args
from petal.discovery import discover
from petal.engine import Batch, benchmark
from petal.media import probe, target_size
from petal.models import Job, Settings, Status
from petal.process import Runner, ProcessError
from petal.safety import VerificationError, fingerprint, verify


class CpuFixtureRunner(Runner):
    """Exercise real filters/mapping/verification when this sandbox has no render node."""
    def run(self, args, **kwargs):
        if args[0] == "ffmpeg" and "-init_hw_device" in args:
            if "-hwaccel" in args:
                raise ProcessError("Test intentionally exercises software-decode retry")
            args = list(args)
            pos = args.index("-init_hw_device")
            del args[pos:pos + 4]
            args = [arg.replace(",hwupload", "") for arg in args]
            args.remove("-noautoscale:v:0")  # CPU fixture encoder needs software-format negotiation.
            pos = args.index("-c:v:0")
            codec = args[pos + 1]
            del args[pos:pos + 6]
            if "av1_vaapi" in codec:
                args[pos:pos] = ["-c:v:0", "libaom-av1", "-cpu-used", "8", "-crf", "32", "-b:v", "0", "-threads", "4", "-row-mt", "1"]
            else:
                args[pos:pos] = ["-c:v:0", "libx265", "-preset", "ultrafast", "-crf", "24",
                                 "-x265-params", "pools=2:frame-threads=2:log-level=error"]
        return super().run(args, **kwargs)


def generate(path: Path, w: int, h: int, *, rate="24", vf: str | None = None, seconds="0.5"):
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={rate}"]
    if vf:
        args += ["-vf", vf, "-fps_mode", "vfr"]
    Runner().run(args + ["-t", seconds, "-c:v", "ffv1", str(path)])


def timestamps(path: Path) -> list[float]:
    data = Runner().run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)])
    return [float(f["best_effort_timestamp_time"]) for f in json.loads(data.stdout)["frames"]]


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe not installed")
class FFmpegIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="petal-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "Originals"
        self.source.mkdir()
        self.output = self.root / "Compressed"
        self.runner = CpuFixtureRunner()

    def encode_fixture(self, path):
        m = probe(path, self.runner)
        output = self.root / (path.stem + "-encoded.mkv")
        self.runner.run(encode_command(m, output, Settings(), hardware_decode=False))
        verify(m, output, Settings(), self.runner)
        return m, output

    def test_shapes_and_decode(self):
        for w, h in ((640, 360), (240, 426), (360, 360), (160, 90), (640, 266), (3840, 2160)):
            with self.subTest(shape=(w, h)):
                path = self.source / f"{w}x{h}.mkv"
                generate(path, w, h, seconds="0.2" if w > 1920 else "0.5")
                m, output = self.encode_fixture(path)
                result = probe(output, self.runner)
                self.assertEqual((result.video["width"], result.video["height"]), target_size(m))
                before, after = timestamps(path), timestamps(output)
                self.assertEqual(len(before), len(after))
                for a, b in zip(before, after):
                    self.assertAlmostEqual(a, b, delta=.003)

    def test_bt709_tagged_source_encodes_and_preserves_color(self):
        source = self.source / "tagged-sdr.mp4"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-f", "lavfi",
                      "-i", "testsrc2=size=320x180:rate=24", "-t", "0.5", "-c:v", "libx264",
                      "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                      "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                      "-color_range", "tv", "-chroma_sample_location", "left", str(source)])
        original, output = self.encode_fixture(source)
        encoded = probe(output, self.runner)
        for key in ("color_primaries", "color_transfer", "color_space", "color_range"):
            self.assertEqual(original.video[key], encoded.video[key])

    def test_hevc_sources_8_and_10_bit_to_both_encoders_preserve_color(self):
        for depth in (8, 10):
            source = self.source / f"hevc-{depth}.mkv"
            Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-f", "lavfi",
                          "-i", "testsrc2=size=320x180:rate=24", "-t", "0.5", "-pix_fmt",
                          "yuv420p10le" if depth == 10 else "yuv420p", "-c:v", "libx265", "-preset", "ultrafast",
                          "-x265-params", "pools=2:frame-threads=2:log-level=error",
                          "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv", str(source)])
            original = probe(source, self.runner)
            self.assertEqual(original.bit_depth, depth)
            for codec in ("av1_vaapi", "hevc_vaapi"):
                with self.subTest(depth=depth, codec=codec):
                    output = self.root / f"{codec}-{depth}.mkv"
                    settings = Settings(codec=codec)
                    self.runner.run(encode_command(original, output, settings, hardware_decode=False))
                    verify(original, output, settings, self.runner)

    def test_encoded_header_color_loss_is_corrected_and_still_verified(self):
        source = self.source / "tagged-source.mp4"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-f", "lavfi",
                      "-i", "testsrc2=size=320x180:rate=24", "-t", "0.5", "-c:v", "libx264", "-vf",
                      "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv", str(source)])
        original = probe(source, self.runner)
        class DropHeaderProperties(CpuFixtureRunner):
            def run(self, args, **kwargs):
                if "-init_hw_device" in args:
                    args = list(args)
                    position = args.index("-filter:v:0") + 1
                    args[position] += ",setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown"
                return super().run(args, **kwargs)
        runner = DropHeaderProperties()
        correct = self.root / "corrected.mkv"
        runner.run(encode_command(original, correct, Settings(), hardware_decode=False))
        verify(original, correct, Settings(), runner)
        uncorrected = self.root / "uncorrected.mkv"
        args = encode_command(original, uncorrected, Settings(), hardware_decode=False)
        position = args.index("-bsf:v:0")
        del args[position:position+2]
        runner.run(args)
        with self.assertRaises(VerificationError):
            verify(original, uncorrected, Settings(), runner)

    def test_compatibility_preserves_cover_timecode_and_subtitle_track(self):
        from petal.assets import prepare_artwork, cleanup_artwork
        from petal.safety import StateStore
        cover = self.root / "cover.jpg"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi",
                      "-i", "color=c=pink:size=64x64", "-frames:v", "1", "-update", "1", str(cover)])
        subtitles = self.root / "subtitle.srt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:00,900\nHello world\n")
        source = self.source / "with-extra-streams.mp4"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n",
                      "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                      "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                      "-i", str(subtitles), "-i", str(cover),
                      "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map", "3:v",
                      "-c:v:0", "libx264", "-crf:v:0", "0", "-c:v:1", "copy",
                      "-disposition:v:1", "attached_pic", "-c:a", "aac", "-c:s", "mov_text",
                      "-timecode", "01:02:03:04", "-t", "1.5", str(source)])
        m = probe(source, self.runner)
        self.assertTrue(any(s.get("codec_tag_string") == "tmcd" for s in m.streams))
        self.assertTrue(any(s.get("disposition", {}).get("attached_pic") for s in m.streams))
        jobs = discover(self.source, self.output, self.runner)
        settings = Settings()
        caps = Capabilities(settings.device, tested={"av1_vaapi:Balanced:8": True})
        batch = Batch(jobs, self.source, self.output, settings, caps, self.root / "state")
        batch.runner = self.runner
        batch.run()
        self.assertEqual(jobs[0].status, Status.COMPLETED, jobs[0].message)
        result = probe(jobs[0].destination, self.runner)
        self.assertEqual(len([s for s in result.streams if s.get("codec_type") == "audio"]), 1)
        self.assertEqual([s["codec_name"] for s in result.streams if s.get("codec_type") == "subtitle"], ["ass"])
        tags = {k.casefold(): v for k, v in result.format.get("tags", {}).items()}
        self.assertTrue(any(k.startswith("petal_timecode_stream_") and v == "01:02:03:04" for k, v in tags.items()))
        extracted = prepare_artwork(result, settings, self.output, batch.store, self.runner)
        try:
            self.assertEqual(len(extracted), 1)
            self.assertEqual(next(iter(extracted.values())).read_bytes(), cover.read_bytes())
        finally:
            cleanup_artwork(extracted, batch.store, self.runner)
        self.assertEqual(batch.store.data["temporary"], [])
        self.assertEqual(list(self.output.glob(".petal-*.asset.*")), [])

    def test_anamorphic_and_odd(self):
        for name, w, h, vf in (("anamorphic", 320, 240, "setsar=4/3"),
                                ("odd", 320, 240, "pad=321:241:0:0")):
            path = self.source / f"{name}.mkv"
            # FFV1 with 4:2:0 supports odd dimensions; pad rounds chroma, force yuv444p then convert back.
            if name == "odd":
                vf = "format=yuv444p,pad=321:241:0:0,format=yuv420p"
            generate(path, w, h, vf=vf)
            m, output = self.encode_fixture(path)
            self.assertEqual((probe(output, self.runner).video["width"], probe(output, self.runner).video["height"]), target_size(m))

    def test_variable_frame_rate_preserved(self):
        path = self.source / "vfr.mkv"
        generate(path, 320, 180, rate="60", seconds="1.0", vf="select='not(mod(n,3))+eq(mod(n,10),1)'")
        _, output = self.encode_fixture(path)
        before, after = timestamps(path), timestamps(output)
        self.assertEqual(len(before), len(after))
        self.assertGreater(len({round(b-a, 3) for a, b in zip(before, before[1:])}), 1)
        for a, b in zip(before, after):
            self.assertAlmostEqual(a, b, delta=.003)

    def test_rotation(self):
        original = self.source / "phone.mp4"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "0.5", "-c:v", "libx264", str(original)])
        rotated = self.source / "rotated.mov"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-display_rotation", "90", "-i", str(original), "-c", "copy", str(rotated)])
        self.assertEqual(probe(rotated, self.runner).rotation, 90)
        m, output = self.encode_fixture(rotated)
        out = probe(output, self.runner)
        self.assertEqual((out.video["width"], out.video["height"]), (180, 320))
        self.assertEqual(out.rotation, 0)

    def test_scan_nested_unknown_extension_and_bad_media(self):
        nested = self.source / "Anime" / "猫"
        nested.mkdir(parents=True)
        first = nested / "Épisode one.mp4"
        generate(first.with_suffix(".mkv"), 160, 90)
        first.with_suffix(".mkv").rename(first)
        second = nested / "Épisode one.mkv"
        shutil.copyfile(first, second)
        odd_ext = self.source / "valid.unknown"
        shutil.copyfile(first, odd_ext)
        (self.source / "bad.mp4").write_bytes(b"malformed")
        (self.source / "notes.txt").write_text("not a video")
        (nested / "loop").symlink_to(self.source, target_is_directory=True)
        jobs = discover(self.source, self.output, self.runner)
        self.assertEqual(len(jobs), 4)
        self.assertEqual(sum(j.status == Status.FAILED for j in jobs), 1)
        self.assertEqual(len(set(j.destination for j in jobs)), 4)
        self.assertTrue(any(j.destination.parent == self.output / "Anime/猫" for j in jobs))

    def test_batch_publish_resume_no_savings_and_original_hash(self):
        original = self.source / "猫 sample.mkv"
        generate(original, 320, 180, seconds="1")
        original_key = fingerprint(original)
        caps = Capabilities("/dev/dri/renderD128", tested={"av1_vaapi:Balanced:8": True})
        jobs = discover(self.source, self.output, self.runner)
        batch = Batch(jobs, self.source, self.output, Settings(), caps, self.root / "state")
        batch.runner = self.runner
        batch.run()
        self.assertEqual(jobs[0].status, Status.COMPLETED, jobs[0].message)
        self.assertTrue(jobs[0].destination.exists())
        self.assertEqual(fingerprint(original), original_key)
        self.assertEqual(list(self.output.glob(".petal-*.partial.mkv")), [])
        encoded_key = fingerprint(jobs[0].destination)
        # A second pass must verify and reuse the recorded file without invoking the encoder.
        class ResumeRunner(CpuFixtureRunner):
            def run(self, args, **kwargs):
                if "-init_hw_device" in args:
                    raise AssertionError("Resume tried encoding again")
                return super().run(args, **kwargs)
        resumed = Batch(discover(self.source, self.output, self.runner), self.source, self.output,
                        Settings(), caps, self.root / "state")
        resumed.runner = ResumeRunner()
        resumed.run()
        self.assertEqual(resumed.jobs[0].status, Status.COMPLETED, resumed.jobs[0].message)
        self.assertEqual(fingerprint(resumed.jobs[0].destination), encoded_key)
        other_output = self.root / "NoSavings"
        rejected = Batch(discover(self.source, other_output, self.runner), self.source, other_output,
                         Settings(min_savings=99), caps, self.root / "state")
        rejected.runner = self.runner
        rejected.run()
        self.assertEqual(rejected.jobs[0].status, Status.SKIPPED)
        self.assertIn("No savings", rejected.jobs[0].message)
        self.assertFalse(rejected.jobs[0].destination.exists())
        self.assertEqual(fingerprint(original), original_key)

    def test_audio_subtitles_chapters_metadata(self):
        subtitles = self.root / "sub.srt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:00,400\nHello 猫\n", encoding="utf-8")
        chapters = self.root / "chapters.txt"
        chapters.write_text(";FFMETADATA1\ntitle=Test collection\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=Opening\n")
        source = self.source / "tracks.mkv"
        Runner().run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                      "-f", "lavfi", "-i", "anullsrc=r=48000:cl=5.1", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                      "-i", str(subtitles), "-i", str(chapters), "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
                      "-map_metadata", "4", "-map_chapters", "4", "-t", "0.5", "-c:v", "ffv1", "-c:a", "flac", "-c:s", "srt",
                      "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "title=Commentary", "-metadata:s:s:0", "language=eng", str(source)])
        m, out = self.encode_fixture(source)
        result = probe(out, self.runner)
        self.assertEqual(len(result.streams), 4)
        self.assertEqual(len(result.chapters), 1)
        self.assertEqual(result.streams[1]["channels"], 6)
        opus = self.root / "opus.mkv"
        self.runner.run(encode_command(m, opus, Settings(audio="opus"), hardware_decode=False))
        verify(m, opus, Settings(audio="opus"), self.runner)
        self.assertEqual(probe(opus, self.runner).streams[1]["channels"], 6)

    def test_malformed_output_rejected(self):
        source = self.source / "source.mkv"
        generate(source, 320, 180, seconds="1")
        m, output = self.encode_fixture(source)
        data = output.read_bytes()
        output.write_bytes(data[:len(data) // 2])
        with self.assertRaises((VerificationError, ProcessError, ValueError)):
            verify(m, output, Settings(), self.runner)

    def test_sample_comparison_keeps_verified_samples_and_real_metrics(self):
        source = self.source / "sample.mkv"
        generate(source, 160, 90, seconds="1.5")
        original = fingerprint(source)
        settings = Settings()
        caps = Capabilities(settings.device, tested={f"av1_vaapi:{p}:8": True for p in
                                                   ("High Quality", "Balanced", "Maximum Savings")})
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")}):
            samples = benchmark(probe(source, self.runner), self.root / "samples", settings, caps,
                                self.runner, seconds=1)
        self.assertEqual(len(samples), 3)
        for result in samples:
            self.assertNotIn("error", result, result)
            self.assertTrue(Path(result["path"]).exists())
            self.assertGreater(result["bytes"], 0)
            self.assertIsNotNone(result["ssim"], result)
            self.assertGreater(result["ssim"], .5)
        self.assertEqual(fingerprint(source), original)
        self.assertEqual(list((self.root / "samples").glob(".petal-*.partial.mkv")), [])

    def test_target_presets_encode_and_verify_actual_output_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir()
            generate(source/'landscape.mkv',3840,2160,rate='2',seconds='0.5')
            for preset,expected in [('720p',(1280,720)),('1440p',(2560,1440)),('2160p',(3840,2160))]:
                config=Settings(resolution=preset,auto_quality=False)
                runner=CpuFixtureRunner()
                jobs=discover(source,root/preset,runner)
                caps=Capabilities(config.device,resolution=preset,tested={'av1_vaapi:Balanced:8':True})
                batch=Batch(jobs,source,root/preset,config,caps,root/'state')
                batch.runner=runner
                batch.run()
                self.assertEqual(jobs[0].status,Status.COMPLETED,jobs[0].message)
                result=probe(jobs[0].destination,Runner())
                self.assertEqual((result.video['width'],result.video['height']),expected)
                self.assertEqual(jobs[0].encoder_quality,config.quality_for_size(expected))

    def test_mixed_input_batch_publishes_and_resumes_under_stable_selection_ledger(self):
        from petal.discovery import discover_selection
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            inputs=[root/'first'/'collection',root/'second'/'collection']
            for source in inputs:
                source.mkdir(parents=True)
                generate(source/'猫.mkv',320,180)
            output=root/'output'; namespaces={};runner=CpuFixtureRunner()
            config=Settings(auto_quality=False)
            caps=Capabilities(config.device,tested={'av1_vaapi:Balanced:8':True})
            jobs=discover_selection(inputs,output,runner,namespaces)
            original={job.source:fingerprint(job.source) for job in jobs}
            batch=Batch(jobs,inputs[0],output,config,caps,root/'state',selection=True)
            batch.runner=runner;batch.run()
            self.assertEqual(len(jobs),2)
            self.assertTrue(all(j.status==Status.COMPLETED for j in jobs),[j.message for j in jobs])
            self.assertEqual(len({j.destination for j in jobs}),2)
            again=discover_selection(list(reversed(inputs)),output,runner,dict(namespaces))
            resumed=Batch(again,inputs[1],output,config,caps,root/'state',selection=True)
            resumed.runner=runner
            with patch('petal.engine.encode_command',side_effect=AssertionError('Completed mixed inputs must resume')):
                resumed.run()
            self.assertTrue(all(j.status==Status.COMPLETED and 'Reused' in j.message for j in again),[j.message for j in again])
            for source,expected in original.items():
                self.assertEqual(fingerprint(source),expected)


@unittest.skipUnless(os.environ.get("PETAL_TEST_GPU") == "1", "Set PETAL_TEST_GPU=1 on the GPU host")
class ActualGPU(unittest.TestCase):
    def test_1080p_encode_decode_dimensions(self):
        report = detect(Settings(device=os.environ.get("PETAL_DEVICE", "/dev/dri/renderD128")), Runner())
        self.assertTrue(report.supports(Settings(device=report.device)), json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    unittest.main()
