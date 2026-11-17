from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from petal.capabilities import Capabilities
from petal.commands import Unsupported, encode_command, quality_args, stream_plan
from petal.discovery import candidate_paths, destination_paths, validate_roots
from petal.engine import Batch
from petal.media import display_size, target_sar, target_size
from petal.models import Job, Media, Settings, Status
from petal.process import Cancelled, ProcessError, Runner, progress_seconds
from petal.safety import (StateStore, enough_savings, fingerprint, new_temporary, output_lock,
                          publish, safe_parent)


def media(width=1920, height=1080, sar="1:1", rotation=0, **extra):
    video = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": width,
             "height": height, "sample_aspect_ratio": sar, "pix_fmt": "yuv420p",
             "avg_frame_rate": "30000/1001", "side_data_list": [{"rotation": rotation}]}
    video.update(extra)
    return Media(Path("/collection/猫 video.mov"), [video], {"duration": "10"})


class GeometryTests(unittest.TestCase):
    def test_requested_examples(self):
        cases = [(3840, 2160, 1920, 1080), (1920, 1080, 1920, 1080), (1280, 720, 1280, 720),
                 (1080, 1920, 1080, 1920), (2160, 3840, 1080, 1920), (720, 1280, 720, 1280),
                 (1440, 1080, 1440, 1080), (1080, 1080, 1080, 1080), (1920, 800, 1920, 800)]
        for w, h, ow, oh in cases:
            with self.subTest(w=w, h=h):
                self.assertEqual(target_size(media(w, h)), (ow, oh))

    def test_rotation_and_anamorphic(self):
        m = media(1920, 1080, rotation=-90)
        self.assertEqual(target_size(m), (1080, 1920))
        self.assertEqual(target_size(media(1440, 1080, sar="4:3")), (1920, 1080))
        self.assertEqual(target_size(media(720, 576, sar="16:15", rotation=90)), (576, 768))

    def test_odd_dimensions_exact_dar(self):
        for w, h in ((853, 481), (1919, 1079), (4097, 2161), (1023, 1023), (101, 999)):
            m = media(w, h)
            tw, th = target_size(m)
            self.assertEqual((tw % 2, th % 2), (0, 0))
            self.assertLessEqual(tw, w)
            self.assertLessEqual(th, h)
            self.assertAlmostEqual(tw / th * float(target_sar(m)), w / h, places=5)

    def test_unusual_and_bad_sar(self):
        self.assertEqual(target_size(media(2560, 1080)), (1920, 810))
        self.assertEqual(target_size(media(720, 1280, sar="0:1")), (720, 1280))
        with self.assertRaises(ValueError):
            target_size(media(rotation=42))

    def test_depth_and_hdr(self):
        self.assertEqual(media(pix_fmt="yuv420p10le").bit_depth, 10)
        self.assertEqual(media(pix_fmt="p010le").bit_depth, 10)
        self.assertTrue(media(color_transfer="smpte2084").hdr)
        self.assertTrue(media(color_transfer="arib-std-b67").hdr)
        self.assertFalse(media().hdr)


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "output"

    def test_overlapping_roots(self):
        for output in (self.source, self.source / "nested", self.root):
            with self.assertRaises(ValueError):
                validate_roots(self.source, output)
        self.assertEqual(validate_roots(self.source, self.output), (self.source, self.output))
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            validate_roots(self.source, alias)

    def test_recursive_unicode_and_symlinks(self):
        nested = self.source / "Anime" / "猫 🐾"
        nested.mkdir(parents=True)
        movie = nested / "épisode 1.mp4"
        movie.write_bytes(b"test")
        (nested / "loop").symlink_to(self.source, target_is_directory=True)
        (nested / "linked.mp4").symlink_to(movie)
        excluded = self.source / "exclude"
        excluded.mkdir()
        (excluded / "no.mp4").touch()
        self.assertEqual(list(candidate_paths(self.source, excluded)), [movie])

    def test_collisions_and_reserved_names(self):
        files = [self.source / name for name in ("Film.mp4", "Film.mkv", "film.mov", "other/Film.ts", "猫.mkv")]
        result = destination_paths(files, self.source, self.output)
        self.assertEqual(len({str(v).casefold() for v in result.values()}), len(files))
        self.assertEqual(result[files[-2]], self.output / "other/Film.mkv")
        extra = self.source / result[files[0]].name
        result2 = destination_paths(files + [extra], self.source, self.output)
        self.assertEqual(len(set(result2.values())), len(files) + 1)

    def test_long_unicode_collision_names_fit_filesystem(self):
        paths = [self.source / ("猫" * 80 + suffix) for suffix in (".mp4", ".mkv")]
        names = destination_paths(paths, self.source, self.output)
        self.assertEqual(len(set(names.values())), 2)
        self.assertTrue(all(len(path.name.encode()) <= 240 for path in names.values()))

    def test_no_clobber_and_atomic_publication(self):
        self.output.mkdir()
        target = self.output / "猫.mkv"
        temporary = new_temporary(target)
        temporary.write_bytes(b"complete")
        publish(temporary, target)
        self.assertEqual(target.read_bytes(), b"complete")
        self.assertFalse(temporary.exists())
        next_temp = new_temporary(target)
        next_temp.write_bytes(b"replacement")
        with self.assertRaises(FileExistsError):
            publish(next_temp, target)
        self.assertEqual(target.read_bytes(), b"complete")
        alias = self.output / "alias.mkv"
        alias.symlink_to(self.root / "does-not-exist")
        with self.assertRaises(FileExistsError):
            publish(next_temp, alias)

    def test_destination_symlink_escape(self):
        self.output.mkdir()
        (self.output / "sub").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_parent(self.output / "sub/video.mkv", self.output)
        safe_parent(self.output / "real/nested/video.mkv", self.output)
        self.assertTrue((self.output / "real/nested").is_dir())

    def test_output_lock(self):
        with output_lock(self.output):
            with self.assertRaises(RuntimeError):
                with output_lock(self.output):
                    pass

    def test_recovery_only_owned_paths(self):
        self.output.mkdir()
        store = StateStore(self.root / "state", self.source, self.output)
        temporary = new_temporary(self.output / "video.mkv")
        temporary.write_bytes(b"incomplete")
        unrelated = new_temporary(self.output / "unrelated.mkv")
        unrelated.write_bytes(b"not recorded")
        outside = self.source / ".petal-00000000000000000000000000000000.partial.mkv"
        outside.write_bytes(b"original")
        store.track(temporary)
        asset = self.output / ".petal-11111111111111111111111111111111.asset.jpg"
        asset.write_bytes(b"interrupted cover extraction")
        store.track(asset)
        store.track(outside)
        loaded = StateStore(self.root / "state", self.source, self.output)
        loaded.recover(self.output)
        self.assertFalse(temporary.exists())
        self.assertFalse(asset.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(outside.exists())

    def test_fingerprint_detects_changes(self):
        path = self.source / "file"
        path.write_bytes(b"a")
        first = fingerprint(path)
        path.write_bytes(b"b")
        self.assertNotEqual(first, fingerprint(path))

    def test_resume_and_stale_record(self):
        self.output.mkdir()
        src, dst = self.source / "a.mp4", self.output / "a.mkv"
        src.write_bytes(b"source video")
        dst.write_bytes(b"smaller")
        m = media()
        m.path = src
        job = Job(src, Path("a.mp4"), dst, media=m, size=12, source_key=fingerprint(src), final_size=7)
        batch = Batch([job], self.source, self.output, Settings(), Capabilities("/dev/dri/renderD128"), self.root / "state")
        batch.remember(job)
        with patch("petal.engine.verify") as check:
            self.assertTrue(batch.resume(job))
            check.assert_called_once()
        dst.write_bytes(b"changed")
        with patch("petal.engine.verify") as check:
            self.assertFalse(batch.resume(job))
            check.assert_not_called()

    def test_interrupted_job_removes_temp_preserves_source(self):
        src = self.source / "a.mp4"
        src.write_bytes(b"source video")
        m = media()
        m.path = src
        job = Job(src, Path("a.mp4"), self.output / "a.mkv", media=m)
        caps = Capabilities("/dev/dri/renderD128", tested={"av1_vaapi:Balanced:8": True})
        batch = Batch([job], self.source, self.output, Settings(), caps, self.root / "state")
        before = fingerprint(src)
        def interrupted(args, **kwargs):
            Path(args[-1]).write_bytes(b"partial")
            raise Cancelled()
        with patch("petal.engine.probe", return_value=m), patch.object(batch.runner, "run", side_effect=interrupted):
            batch.run()
        self.assertEqual(job.status, Status.CANCELLED)
        self.assertFalse(job.destination.exists())
        self.assertEqual(list(self.output.glob("*.mkv")), [])
        self.assertEqual(fingerprint(src), before)
        self.assertEqual(batch.store.data["temporary"], [])

    def test_command_error_is_not_retried_as_a_decoder_error(self):
        src = self.source / "a.mp4"
        src.write_bytes(b"source")
        m = media()
        m.path = src
        job = Job(src, Path("a.mp4"), self.output / "a.mkv", media=m)
        caps = Capabilities(Settings().device, tested={"av1_vaapi:Balanced:8": True})
        batch = Batch([job], self.source, self.output, Settings(), caps, self.root / "state")
        with patch("petal.engine.probe", return_value=m), patch.object(batch.runner, "run", side_effect=ProcessError("Unrecognized option 'bad_option'")) as run:
            batch.run()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(job.status, Status.FAILED)
        self.assertFalse(job.destination.exists())

    def test_compatibility_upgrade_reuses_strict_completed_records(self):
        self.output.mkdir()
        src, dst = self.source / "a.mp4", self.output / "a.mkv"
        src.write_bytes(b"source")
        dst.write_bytes(b"valid")
        m = media()
        m.path = src
        job = Job(src, Path("a.mp4"), dst, media=m, source_key=fingerprint(src))
        old = Batch([job], self.source, self.output, Settings(incompatible="skip"), Capabilities(Settings().device), self.root / "state")
        old.remember(job)
        upgraded = Batch([job], self.source, self.output, Settings(), Capabilities(Settings().device), self.root / "state")
        with patch("petal.engine.verify"):
            self.assertTrue(upgraded.resume(job))

    def test_skip_reason_is_logged_and_survives_subsequent_start(self):
        reason = "Unvalidated media format requires review"
        job = Job(self.source / "a.mp4", Path("a.mp4"), self.output / "a.mkv", status=Status.SKIPPED, message=reason)
        events = []
        batch = Batch([job], self.source, self.output, Settings(), Capabilities(Settings().device), self.root / "state", lambda kind, value: events.append((kind, value)))
        batch.run()
        self.assertEqual(job.message, reason)
        self.assertTrue(any(kind == "log" and reason in value for kind, value in events))


class CommandTests(unittest.TestCase):
    def test_compatibility_plan_preserves_auxiliary_media(self):
        m = media()
        m.streams += [{"index": 1, "codec_type": "data", "codec_tag_string": "tmcd", "tags": {"timecode": "01:02:03:04"}},
                      {"index": 2, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                      {"index": 3, "codec_type": "subtitle", "codec_name": "mov_text"}]
        plan = stream_plan(m, Settings())
        self.assertEqual(plan.metadata["petal_timecode_stream_1"], "01:02:03:04")
        self.assertEqual([s["index"] for s in plan.artwork], [2])
        self.assertEqual(plan.subtitle_codecs, {3: "ass"})
        self.assertEqual([s["index"] for s in plan.streams], [0, 3])
        with self.assertRaises(Unsupported):
            stream_plan(m, Settings(incompatible="skip"))

    def test_color_metadata_avoids_software_output_negotiation_on_both_paths(self):
        m = media(color_primaries="bt709", color_transfer="bt709", color_space="bt709",
                  color_range="tv", chroma_location="left")
        for hardware in (True, False):
            args = encode_command(m, Path("/out/temp.mkv"), Settings(), hardware_decode=hardware)
            filters = args[args.index("-filter:v:0") + 1]
            self.assertIn("setparams=color_primaries=1:color_trc=1:colorspace=1:range=limited", filters)
            self.assertIn("av1_metadata=color_primaries=1:transfer_characteristics=1:matrix_coefficients=1:color_range=0", args)
            self.assertIn("-noautoscale:v:0", args)
            for name in ("color_primaries", "color_trc", "colorspace", "color_range", "chroma_sample_location"):
                self.assertNotIn(f"-{name}:v:0", args)
            self.assertNotIn("-color_transfer:v:0", args)
            self.assertNotIn("-c", args)

    def test_vaapi_only_and_timestamps(self):
        for codec in ("av1_vaapi", "hevc_vaapi"):
            args = encode_command(media(), Path("/out/temp.mkv"), Settings(codec=codec), hardware_decode=False)
            self.assertIn(codec, args)
            self.assertNotIn("libsvtav1", args)
            self.assertNotIn("-r", args)
            self.assertNotIn("-shortest", args)
            self.assertIn("passthrough", args)
            self.assertIn("filter", args)
            self.assertIn("-n", args)
            self.assertNotIn("-y", args)
            self.assertIn("hwupload", args[args.index("-filter:v:0") + 1])
            self.assertIn("/collection/猫 video.mov", args)

    def test_mapping_audio_subtitles_attachments(self):
        m = media()
        m.streams += [{"index": 1, "codec_type": "audio", "codec_name": "ac3", "channels": 6, "channel_layout": "5.1"},
                      {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "channel_layout": "stereo"},
                      {"index": 3, "codec_type": "subtitle", "codec_name": "ass"},
                      {"index": 4, "codec_type": "attachment", "codec_name": "ttf"}]
        args = encode_command(m, Path("/out/tmp.mkv"), Settings(), hardware_decode=False)
        maps = [args[i+1] for i, x in enumerate(args) if x == "-map"]
        self.assertEqual(maps, ["0:0", "0:1", "0:2", "0:3", "0:4"])
        self.assertIn("-map_chapters", args)
        for index in range(1, 5):
            self.assertEqual(args[args.index(f"-c:{index}") + 1], "copy")
        opus = encode_command(m, Path("/out/tmp.mkv"), Settings(audio="opus"), hardware_decode=False)
        self.assertIn("384k", opus)
        self.assertIn("160k", opus)
        self.assertNotIn("-ac", opus)

    def test_unsupported_stream_is_never_silently_dropped(self):
        m = media()
        m.streams.append({"index": 1, "codec_type": "subtitle", "codec_name": "mov_text"})
        with self.assertRaises(Unsupported):
            stream_plan(m, Settings(incompatible="skip"))
        plan = stream_plan(m, Settings(incompatible="omit"))
        self.assertEqual(len(plan.streams), 1)
        self.assertIn("mov_text", plan.warnings[0])

    def test_hdr_and_unvalidated_formats_skip(self):
        for m in (media(color_transfer="smpte2084"), media(pix_fmt="yuva420p"), media(field_order="tt")):
            with self.assertRaises(Unsupported):
                stream_plan(m, Settings())

    def test_separate_encoder_quality_scales(self):
        self.assertEqual(Settings().quality, 105)
        self.assertEqual(Settings(codec="hevc_vaapi").quality, 24)
        self.assertIn("-global_quality:v:0", quality_args(Settings()))
        self.assertIn("-qp:v:0", quality_args(Settings(codec="hevc_vaapi")))

    def test_progress(self):
        self.assertEqual(progress_seconds({"out_time_us": "2500000"}), 2.5)
        self.assertEqual(progress_seconds({"out_time": "01:02:03.500000"}), 3723.5)
        self.assertEqual(progress_seconds({"out_time_us": "-100"}), 0)
        self.assertIsNone(progress_seconds({"out_time_us": "N/A"}))

    def test_savings(self):
        self.assertTrue(enough_savings(1000, 950, 5))
        self.assertFalse(enough_savings(1000, 951, 5))
        self.assertFalse(enough_savings(1000, 1001, 0))
        self.assertFalse(enough_savings(1000, 1000, 0))
        self.assertFalse(enough_savings(1000, 0, 5))


class SubprocessTests(unittest.TestCase):
    def test_corruption_warning_survives_bounded_stderr_history(self):
        code = "import sys; print('File ended prematurely', file=sys.stderr); [print('later warning '+str(i), file=sys.stderr) for i in range(400)]"
        with self.assertRaisesRegex(ProcessError, "File ended prematurely"):
            Runner().run([sys.executable, "-c", code])

    def test_child_dies_after_application_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            child_code = "import os,time,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(20)"
            parent_code = "from petal.process import Runner; import sys; Runner().run([sys.executable, '-c', sys.argv[1], sys.argv[2]])"
            parent = subprocess.Popen([sys.executable, "-c", parent_code, child_code, str(pidfile)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            child = None
            try:
                deadline = time.monotonic() + 4
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(pidfile.exists(), "Child failed to start")
                child = int(pidfile.read_text())
                parent.kill()
                parent.wait(timeout=3)
                deadline = time.monotonic() + 3
                running = True
                while time.monotonic() < deadline:
                    try:
                        running = Path(f"/proc/{child}/stat").read_text().split(")", 1)[1].strip()[0] != "Z"
                    except FileNotFoundError:
                        running = False
                    if not running:
                        break
                    time.sleep(.01)
                self.assertFalse(running, "Media subprocess survived its application")
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait()
                if child:
                    try:
                        os.killpg(child, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_structured_progress_and_large_stderr(self):
        records = []
        runner = Runner()
        runner.run([sys.executable, "-c", "import sys; sys.stderr.write('x'*100000); print('out_time_us=1000000'); print('progress=end')"], progress=records.append)
        self.assertEqual(progress_seconds(records[0]), 1)

    def test_process_group_cancel(self):
        cancel = threading.Event()
        runner = Runner(cancel)
        timer = threading.Timer(.3, cancel.set)
        timer.start()
        began = time.monotonic()
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pid"
            script = "import os,time,pathlib; pathlib.Path(__import__('sys').argv[1]).write_text(str(os.getpid())); time.sleep(20)"
            with self.assertRaises(Cancelled):
                runner.run([sys.executable, "-c", script, str(pid_file)])
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        timer.join()
        self.assertLess(time.monotonic() - began, 5)


if __name__ == "__main__":
    unittest.main()
