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
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config"), "XDG_STATE_HOME": str(self.root / "state")})
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
            time.sleep(.01)
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
        self.window.launch("test", lambda event: (event("log", "Background worker responsive"), 42)[1], result.append)
        deadline = time.monotonic() + 5
        while self.window.thread and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
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
            time.sleep(.01)
        self.assertEqual(len(self.window.jobs), 1)
        self.window.table.selectRow(0)
        self.window.skip_selected()
        self.assertEqual(self.window.jobs[0].status, Status.SKIPPED)
        self.window.skip_selected()
        self.assertEqual(self.window.jobs[0].status, Status.WAITING)
        self.assertTrue(self.window.start_button.isEnabled())

    def test_encoding_and_verification_have_independent_progress(self):
        from petal.models import Job, Status
        from tests.test_core import media
        encoding = Job(self.root / "encoding.mkv", Path("encoding.mkv"), self.root / "out.mkv",
                       media=media(), status=Status.ENCODING, progress=.5)
        verifying = Job(self.root / "verifying.mkv", Path("verifying.mkv"), self.root / "out2.mkv",
                        media=media(), status=Status.VERIFYING)
        self.window.jobs = [encoding, verifying]
        self.window.model.replace(self.window.jobs)
        self.window.handle_event("progress", {"job": encoding, "fields": {"fps": "100", "speed": "4x"}})
        self.window.handle_event("job", verifying)
        self.window.handle_event("verification", {"job": verifying, "seconds": 2})
        self.assertEqual(self.window.file_progress.value(), 500)
        self.assertEqual(self.window.verify_progress.value(), 200)
        self.assertIn("encoding.mkv", self.window.current.text())
        self.assertIn("verifying.mkv", self.window.verification_status.text())
        self.window.handle_event("quality", {"job": encoding, "report": {
            "status": "Review suggested", "mean": 88, "p10": 75, "worst_scene": 82}})
        self.assertIn("Review 1", self.window.quality_label.text())
        self.assertEqual(self.window.file_progress.value(), 500)

    def wait_idle(self):
        deadline = time.monotonic() + 15
        while self.window.thread and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.window.thread)

    def test_drop_adds_files_before_output_and_preserves_existing_names(self):
        from tests.test_integration import generate
        source = self.root / 'videos'
        source.mkdir()
        video = source / '猫.mkv'
        generate(video,160,90)
        self.window.add_inputs([video])
        self.wait_idle()
        self.assertEqual(len(self.window.jobs),1)
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.source.isReadOnly())
        self.window.output.setText(str(self.root/'output'))
        self.window.scan()
        self.wait_idle()
        original = self.window.jobs[0].destination
        another = source/'猫.mov'
        another.write_bytes(video.read_bytes())
        self.window.add_inputs([another])
        self.wait_idle()
        self.assertEqual(len(self.window.jobs),2)
        self.assertEqual(next(j.destination for j in self.window.jobs if j.source==video),original)
        self.assertEqual(len({j.destination for j in self.window.jobs}),2)

    def test_actual_drop_event_forces_copy_and_cached_launch_does_no_probe(self):
        from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
        from PySide6.QtGui import QDropEvent
        from petal import hardware_cache
        from petal.capabilities import Capabilities
        from tests.test_integration import generate
        video=self.root/'clip.mkv'
        generate(video,160,90)
        data=QMimeData();data.setUrls([QUrl.fromLocalFile(str(video))])
        event=QDropEvent(QPointF(30,30),Qt.DropAction.CopyAction|Qt.DropAction.MoveAction,
                         data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.window.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(),Qt.DropAction.CopyAction)
        self.wait_idle()
        config=self.window.settings()
        hardware_cache.save(self.window.state_dir,config,Capabilities(config.device,tested={'av1_vaapi:Balanced:8':True}))
        with patch('petal.gui.detect',side_effect=AssertionError('Cache must avoid processes')):
            self.window.initialize_hardware()
        self.assertTrue(self.window.cached_hardware)
        self.assertIn('saved check',self.window.hardware_label.text())
        self.assertTrue(video.exists())

    def test_recommendation_is_actionable_and_uses_measured_data(self):
        from tests.test_features import RecommendationTests
        self.window.jobs=[RecommendationTests().checked(mean=80)]
        self.window.update_recommendation()
        self.assertTrue(self.window.advice_card.isVisible())
        self.assertIn('High Quality',self.window.advice_title.text())
        self.window.apply_recommendation()
        self.assertEqual(self.window.settings().profile,'High Quality')
        self.assertFalse(self.window.advice_card.isVisible())
        self.window.resolution.setCurrentIndex(self.window.resolution.findData('1440p'))
        self.assertEqual(self.window.settings().resolution,'1440p')


    def test_move_only_drop_is_rejected_to_protect_originals(self):
        from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
        from PySide6.QtGui import QDropEvent
        video=self.root/'source.mkv';video.write_bytes(b'original')
        data=QMimeData();data.setUrls([QUrl.fromLocalFile(str(video))])
        event=QDropEvent(QPointF(30,30),Qt.DropAction.MoveAction,data,
                         Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        with patch.object(self.window,'add_inputs') as add:
            self.window.dropEvent(event)
            add.assert_not_called()
        self.assertFalse(event.isAccepted())
        self.assertEqual(video.read_bytes(),b'original')


@unittest.skipUnless(PYSIDE, 'PySide6 not installed')
class QualityDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_geometry_hides_legacy_score_and_empty_reports_clear_panel(self):
        from PySide6.QtWidgets import QLabel
        from petal.models import Job
        from petal.quality_ui import QualityDialog
        job=Job(Path('/source/clip.mp4'),Path('clip.mp4'),Path('/output/clip.mkv'),quality={
            'mean':64.41,'geometry_note':'Decoded output is 1920×1082'})
        dialog=QualityDialog([job]);dialog.show();self.app.processEvents()
        self.assertEqual(dialog.score_label.text(),'—')
        self.assertTrue(any(label.text()=='Frame size needs attention' for label in dialog.findChildren(QLabel)))
        from petal.gui import QueueModel
        from petal.models import Status
        from PySide6.QtCore import Qt
        job.status=Status.COMPLETED
        model=QueueModel();model.replace([job])
        self.assertIn('Frame size needs attention',model.data(model.index(0,6)))
        self.assertNotIn('64.4',model.data(model.index(0,6)))
        self.assertNotIn('64.4',model.data(model.index(0,6),Qt.ItemDataRole.ToolTipRole))
        dialog.set_jobs([]);self.app.processEvents()
        self.assertIn('No quality reports',dialog.scroll.widget().text())
        dialog.close();dialog.deleteLater();self.app.processEvents()
