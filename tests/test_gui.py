"""Offscreen native Qt checks; run automatically where PySide6 is installed."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

PYSIDE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE, "PySide6 not installed; native GUI runtime not verified")
class NativeQtSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from petal.gui import STYLE

        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        from petal.gui import Window

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
            },
        )
        self.env.start()
        self.hardware = patch.object(Window, "hardware_check")
        self.hardware.start()
        self.window = Window()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.stop()
        deadline = time.monotonic() + 10
        while self.window.thread and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.window.thread, "Qt worker failed to stop")
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.hardware.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_layout_settings_and_worker(self):
        self.window.resize(900, 680)
        self.window.show_settings()
        self.app.processEvents()
        self.assertTrue(self.window.settings_dialog.isVisible())
        self.window.profile.setCurrentText("Maximum Savings")
        self.assertIn("degrade", self.window.profile_description.text())
        self.window.settings_dialog.hide()
        result = []
        self.window.launch(
            "test",
            lambda event: (event("log", "Background worker responsive"), 42)[1],
            result.append,
        )
        deadline = time.monotonic() + 5
        while self.window.thread and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(result, [42])
        self.assertIn("responsive", self.window.logs.toPlainText())
        if os.environ.get("PETAL_GUI_SCREENSHOT"):
            self.assertTrue(self.window.grab().save(os.environ["PETAL_GUI_SCREENSHOT"]))

    def test_scan_and_skip_restore(self):
        from petal.models import Status
        from tests.test_integration import generate

        source, output = self.root / "videos", self.root / "compressed"
        source.mkdir()
        generate(source / "猫.mkv", 160, 90)
        self.window.source.setText(str(source))
        self.window.output.setText(str(output))
        self.window.scan()
        deadline = time.monotonic() + 15
        while self.window.thread and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(self.window.jobs), 1)
        self.window.table.selectRow(0)
        self.window.skip_selected()
        self.assertEqual(self.window.jobs[0].status, Status.SKIPPED)
        self.assertEqual(self.window.filters[0].rowCount(), 0)
        self.window.tabs.setCurrentIndex(1)
        self.window.finished_table.selectRow(0)
        self.window.skip_selected()
        self.assertEqual(self.window.jobs[0].status, Status.WAITING)
        self.assertTrue(self.window.start_button.isEnabled())

    def test_encoding_and_verification_have_independent_progress(self):
        from petal.models import Job, Status
        from tests.test_core import media

        encoding = Job(
            self.root / "encoding.mkv",
            Path("encoding.mkv"),
            self.root / "out.mkv",
            media=media(),
            status=Status.ENCODING,
            progress=0.5,
        )
        verifying = Job(
            self.root / "verifying.mkv",
            Path("verifying.mkv"),
            self.root / "out2.mkv",
            media=media(),
            status=Status.VERIFYING,
        )
        self.window.jobs = [encoding, verifying]
        self.window.model.replace(self.window.jobs)
        self.window.handle_event(
            "progress", {"job": encoding, "fields": {"fps": "100", "speed": "4x"}}
        )
        self.window.handle_event("job", verifying)
        self.window.handle_event("verification", {"job": verifying, "seconds": 2})
        self.assertEqual(self.window.file_progress.value(), 500)
        self.assertEqual(self.window.verify_progress.value(), 200)
        self.assertIn("encoding.mkv", self.window.current.text())
        self.assertIn("verifying.mkv", self.window.verification_status.text())
        self.window.handle_event(
            "quality",
            {
                "job": encoding,
                "report": {
                    "status": "Review suggested",
                    "mean": 88,
                    "p10": 75,
                    "worst_scene": 82,
                },
            },
        )
        self.assertIn("Review 1", self.window.quality_label.text())
        self.assertEqual(self.window.file_progress.value(), 500)

    def wait_idle(self):
        deadline = time.monotonic() + 15
        while self.window.thread and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.window.thread)

    def test_drop_adds_files_before_output_and_preserves_existing_names(self):
        from tests.test_integration import generate

        source = self.root / "videos"
        source.mkdir()
        video = source / "猫.mkv"
        generate(video, 160, 90)
        self.window.add_inputs([video])
        self.wait_idle()
        self.assertEqual(len(self.window.jobs), 1)
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.source.isReadOnly())
        self.window.output.setText(str(self.root / "output"))
        self.window.scan()
        self.wait_idle()
        original = self.window.jobs[0].destination
        another = source / "猫.mov"
        another.write_bytes(video.read_bytes())
        self.window.add_inputs([another])
        self.wait_idle()
        self.assertEqual(len(self.window.jobs), 2)
        self.assertEqual(
            next(j.destination for j in self.window.jobs if j.source == video), original
        )
        self.assertEqual(len({j.destination for j in self.window.jobs}), 2)

    def test_actual_drop_event_forces_copy_and_cached_launch_does_no_probe(self):
        from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
        from PySide6.QtGui import QDropEvent
        from petal import hardware_cache
        from petal.capabilities import Capabilities
        from tests.test_integration import generate

        video = self.root / "clip.mkv"
        generate(video, 160, 90)
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(video))])
        event = QDropEvent(
            QPointF(30, 30),
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        self.wait_idle()
        config = self.window.settings()
        hardware_cache.save(
            self.window.state_dir,
            config,
            Capabilities(config.device, tested={"av1_vaapi:Balanced:8": True}),
        )
        with patch(
            "petal.gui.detect", side_effect=AssertionError("Cache must avoid processes")
        ):
            self.window.initialize_hardware()
        self.assertTrue(self.window.cached_hardware)
        self.assertIn("saved check", self.window.hardware_label.text())
        self.assertTrue(video.exists())

    def test_recommendation_is_actionable_and_uses_measured_data(self):
        from tests.test_features import RecommendationTests

        self.window.jobs = [RecommendationTests().checked(mean=80)]
        self.window.update_recommendation()
        self.assertTrue(self.window.advice_card.isVisible())
        self.assertIn("High Quality", self.window.advice_title.text())
        self.window.apply_recommendation()
        self.assertEqual(self.window.settings().profile, "High Quality")
        self.assertFalse(self.window.advice_card.isVisible())
        self.window.resolution.setCurrentIndex(self.window.resolution.findData("1440p"))
        self.assertEqual(self.window.settings().resolution, "1440p")

    def test_move_only_drop_is_rejected_to_protect_originals(self):
        from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
        from PySide6.QtGui import QDropEvent

        video = self.root / "source.mkv"
        video.write_bytes(b"original")
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(video))])
        event = QDropEvent(
            QPointF(30, 30),
            Qt.DropAction.MoveAction,
            data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        with patch.object(self.window, "add_inputs") as add:
            self.window.dropEvent(event)
            add.assert_not_called()
        self.assertFalse(event.isAccepted())
        self.assertEqual(video.read_bytes(), b"original")

    def test_finished_filter_selection_remove_and_report_persist_without_touching_files(
        self,
    ):
        from petal.models import Job, Status
        from petal.history import History, summary

        source = self.root / "a.mp4"
        source.write_bytes(b"original-a")
        other = self.root / "b.mp4"
        other.write_bytes(b"original-b")
        jobs = [
            Job(source, Path("a.mp4"), self.root / "out/a.mkv"),
            Job(
                other,
                Path("b.mp4"),
                self.root / "out/b.mkv",
                status=Status.SKIPPED,
                message="No worthwhile savings",
                output_root=self.root / "out",
            ),
        ]
        self.window.jobs = jobs
        self.window.sync_library()
        self.window.tabs.setCurrentIndex(1)
        self.window.finished_table.selectRow(0)
        self.assertIs(self.window.selected_jobs()[0], jobs[1])
        self.window.scanned_roots = (self.root, self.root / "out")
        self.window.history.set_report(jobs, self.root / "out")
        self.window.show_report()
        self.assertEqual(self.window.report_dialog.counts["skipped"].text(), "1")
        self.window.save_history()
        self.window.remove_selected()
        self.assertEqual(self.window.filters[1].rowCount(), 0)
        self.assertEqual(other.read_bytes(), b"original-b")
        self.assertEqual(len(History(self.window.state_dir).report), 2)

    def test_batch_completion_opens_report_after_worker_and_moves_original_on_explicit_action(
        self,
    ):
        from petal.models import Job, Status
        from petal.history import History
        from PySide6.QtWidgets import QMessageBox

        source = self.root / "source"
        source.mkdir()
        video = source / "clip.mp4"
        video.write_bytes(b"keep these exact bytes")
        output = self.root / "output"
        job = Job(
            video,
            Path("nested/clip.mp4"),
            output / "nested/clip.mkv",
            status=Status.WAITING,
            size=22,
            output_root=output,
        )
        self.window.jobs = [job]
        self.window.sync_library()
        self.window.scanned_roots = (source, output)

        def operation(event):
            job.status = Status.SKIPPED
            job.message = "No savings"
            event("job", job)

        self.window.launch("batch", operation, lambda _: None)
        self.wait_idle()
        self.assertTrue(self.window.report_dialog.isVisible())
        self.assertIsNone(self.window.thread)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            self.window.move_originals("skipped")
        self.wait_idle()
        target = output / "skipped/nested/clip.mp4"
        self.assertEqual(target.read_bytes(), b"keep these exact bytes")
        self.assertFalse(video.exists())
        self.assertEqual(job.relocated_to, target)
        restored = History(self.window.state_dir)
        self.assertEqual(restored.report[0].relocated_to, target)
        self.assertFalse(self.window.report_dialog.move_buttons["skipped"].isEnabled())

    def test_report_snapshot_survives_return_to_jobs_and_both_lists_search(self):
        from petal.models import Job, Status

        path = self.root / "one.mp4"
        path.write_bytes(b"data")
        job = Job(
            path, Path("one.mp4"), self.root / "out/one.mkv", status=Status.SKIPPED
        )
        self.window.jobs = [job]
        self.window.sync_library()
        self.window.history.set_report([job], self.root / "out")
        self.window.queue_again([job])
        self.assertEqual(self.window.history.report[0].status, Status.SKIPPED)
        self.assertEqual(self.window.filters[0].rowCount(), 1)
        self.window.search.setText("absent")
        self.assertEqual(self.window.filters[0].rowCount(), 0)
        self.window.search.clear()
        self.assertEqual(self.window.filters[0].rowCount(), 1)

    def test_desktop_integration_uses_real_raster_icon_and_quotes_appimage_path(self):
        from petal.icons import install_desktop
        from PySide6.QtGui import QImage

        image = self.root / "Petal demo % test.AppImage"
        image.write_bytes(b"fixture")
        entry = install_desktop(self.root / "data", image)
        self.assertIn('%% test.AppImage"', entry.read_text())
        icon = self.root / "data/icons/hicolor/256x256/apps/petal.png"
        self.assertEqual(QImage(str(icon)).width(), 256)
        self.assertFalse(self.window.windowIcon().pixmap(128, 128).isNull())

    def test_small_window_preserves_usable_lists_and_report_view_clears_search(self):
        from petal.models import Job, Status

        self.window.resize(900, 740)
        self.app.processEvents()
        self.assertLessEqual(self.window.height(), 740)
        self.assertGreaterEqual(self.window.table.viewport().height(), 90)
        self.assertLessEqual(
            self.window.start_button.geometry().bottom(), self.window.height()
        )
        job = Job(
            self.root / "skipped.mp4",
            Path("skipped.mp4"),
            self.root / "out.mkv",
            status=Status.SKIPPED,
        )
        self.window.jobs = [job]
        self.window.sync_library()
        self.window.search.setText("absent")
        self.window.show_results("skipped")
        self.assertEqual(self.window.filters[1].rowCount(), 1)
        self.assertEqual(self.window.search.text(), "")

    def test_scan_imports_unchanged_recorded_completions_without_encoding(self):
        from tests.test_integration import generate
        from petal.safety import StateStore, fingerprint
        from petal.models import Status

        source = self.root / "source"
        source.mkdir()
        video = source / "scene.mkv"
        generate(video, 160, 90)
        output = self.root / "output"
        output.mkdir()
        encoded = output / "scene.mkv"
        encoded.write_bytes(video.read_bytes())
        store = StateStore(self.window.state_dir, source, output)
        store.data["jobs"]["scene.mkv"] = {
            "status": "Completed",
            "source": fingerprint(video),
            "output": fingerprint(encoded),
            "destination": str(encoded),
            "verification": "full",
        }
        store.save()
        self.window.source.setText(str(source))
        self.window.output.setText(str(output))
        self.window.scan()
        self.wait_idle()
        self.assertEqual(self.window.jobs[0].status, Status.COMPLETED)
        self.assertEqual(self.window.filters[1].rowCount(), 1)
        self.assertEqual(self.window.filters[0].rowCount(), 0)
        self.assertEqual(video.read_bytes(), encoded.read_bytes())

    def test_queued_files_persist_for_restart_and_10_bit_only_hardware_is_ready(self):
        from petal.models import Job, Status
        from petal.history import History
        from petal.capabilities import Capabilities

        source = self.root / "source"
        source.mkdir()
        video = source / "pending.mp4"
        video.write_bytes(b"queued original")
        job = Job(
            video,
            Path("pending.mp4"),
            self.root / "output/pending.mkv",
            output_root=self.root / "output",
        )
        self.window.jobs = [job]
        self.window.sync_library()
        self.window.save_history()
        restored = History(self.window.state_dir)
        self.assertEqual(restored.jobs[video].status, Status.WAITING)
        config = self.window.settings()
        self.window.caps = Capabilities(
            config.device, tested={"av1_vaapi:Balanced:10": True}
        )
        self.assertIn("Ready", self.window.capability_text())
        self.assertEqual(video.read_bytes(), b"queued original")

    def test_large_library_keeps_filtered_selections_attached_to_the_correct_file(self):
        from petal.models import Job, Status

        jobs = []
        statuses = (Status.WAITING, Status.COMPLETED, Status.SKIPPED, Status.FAILED)
        for i in range(550):
            jobs.append(
                Job(
                    self.root / f"video-{i}.mp4",
                    Path(f"video-{i}.mp4"),
                    self.root / "output" / f"video-{i}.mkv",
                    status=statuses[i % 4],
                )
            )
        self.window.jobs = jobs
        self.window.sync_library()
        self.app.processEvents()
        self.assertEqual(self.window.filters[0].rowCount(), 138)
        self.assertEqual(self.window.filters[1].rowCount(), 412)
        self.window.tabs.setCurrentIndex(1)
        self.window.search.setText("video-549.mp4")
        self.assertEqual(self.window.filters[1].rowCount(), 1)
        self.window.finished_table.selectRow(0)
        self.assertIs(self.window.selected_jobs()[0], jobs[549])
        self.window.remove_selected()
        self.assertEqual(self.window.filters[1].rowCount(), 0)
        self.assertIn(jobs[548], self.window.jobs)

    def test_returning_a_saved_result_only_queues_the_selected_file(self):
        from petal.models import Job, Status

        source = self.root / "source"
        source.mkdir()
        selected = source / "selected.mp4"
        selected.write_bytes(b"selected")
        (source / "unselected.mp4").write_bytes(b"unselected")
        output = self.root / "output"
        job = Job(
            selected,
            Path("selected.mp4"),
            output / "selected.mkv",
            status=Status.FAILED,
            output_root=output,
        )
        self.window.source.setText(str(source))
        self.window.output.setText(str(output))
        self.window.history.jobs[selected] = job
        self.window.sync_library()
        self.window.queue_again([job])
        self.assertEqual([j.source for j in self.window.jobs], [selected])
        self.assertEqual(job.status, Status.WAITING)


@unittest.skipUnless(PYSIDE, "PySide6 not installed")
class QualityDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_geometry_hides_legacy_score_and_empty_reports_clear_panel(self):
        from PySide6.QtWidgets import QLabel
        from petal.models import Job
        from petal.quality_ui import QualityDialog

        job = Job(
            Path("/source/clip.mp4"),
            Path("clip.mp4"),
            Path("/output/clip.mkv"),
            quality={"mean": 64.41, "geometry_note": "Decoded output is 1920×1082"},
        )
        dialog = QualityDialog([job])
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.score_label.text(), "—")
        self.assertTrue(
            any(
                label.text() == "Frame size needs attention"
                for label in dialog.findChildren(QLabel)
            )
        )
        from petal.gui import QueueModel
        from petal.models import Status
        from PySide6.QtCore import Qt

        job.status = Status.COMPLETED
        model = QueueModel()
        model.replace([job])
        self.assertIn("Frame size needs attention", model.data(model.index(0, 6)))
        self.assertNotIn("64.4", model.data(model.index(0, 6)))
        self.assertNotIn(
            "64.4", model.data(model.index(0, 6), Qt.ItemDataRole.ToolTipRole)
        )
        dialog.set_jobs([])
        self.app.processEvents()
        self.assertIn("No quality reports", dialog.scroll.widget().text())
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()
