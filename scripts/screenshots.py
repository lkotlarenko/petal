#!/usr/bin/env python3
"""Render actual Qt widgets with labeled demonstration data, never personal media."""

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from petal.gui import STYLE, Window
from petal.models import Job, Media, Status
from petal.quality_ui import QualityDialog


def main():
    destination = ROOT / "docs/screenshots"
    destination.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="petal-screenshot-") as folder,
        patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": folder + "/config",
                "XDG_STATE_HOME": folder + "/state",
                "XDG_CACHE_HOME": folder + "/cache",
            },
        ),
        patch.object(Window, "initialize_hardware"),
    ):
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setFont(QFont("Noto Sans", 10))
        app.setStyleSheet(STYLE)
        window = Window()
        window.resize(1240, 900)
        window.source.setText("/home/demo/Videos")
        window.output.setText("/home/demo/Compressed")
        window.hardware_label.setText("Demo preview · AMD VA-API · AV1 / H.265")
        jobs = []
        for name, score, original, final in [
            ("Coastal walk.mp4", 97.2, 860, 420),
            ("Evening lights.mp4", 86.5, 620, 270),
            ("Garden in spring.mkv", 95.4, 480, 220),
        ]:
            source = Path("/home/demo/Videos") / name
            media = Media(
                source,
                [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1920,
                        "height": 1080,
                        "pix_fmt": "yuv420p",
                        "avg_frame_rate": "30/1",
                    }
                ],
                {"duration": "120"},
            )
            report = {
                "status": "Review suggested"
                if score < 90
                else "No concern in sampled scenes",
                "mean": score,
                "p10": score - 3,
                "worst_scene": score - 1.5,
                "model": "vmaf_v0.6.1neg",
                "scenes": [
                    {"start": s, "duration": 2, "mean": score + d}
                    for s, d in [(12, 1), (54, -1.5), (98, 0.5)]
                ],
            }
            jobs.append(
                Job(
                    source,
                    Path(name),
                    Path("/home/demo/Compressed") / Path(name).with_suffix(".mkv"),
                    media=media,
                    status=Status.COMPLETED,
                    size=original * 1024**2,
                    final_size=final * 1024**2,
                    message="Verified · Demo data",
                    quality=report,
                )
            )
        jobs[1].status = Status.SKIPPED
        jobs[1].quality = {}
        jobs[1].final_size = 0
        jobs[1].message = "No worthwhile savings · Demo data"
        failed = Job(
            Path("/home/demo/Videos/Old recording.mov"),
            Path("Old recording.mov"),
            Path("/home/demo/Compressed/Old recording.mkv"),
            status=Status.FAILED,
            size=300 * 1024**2,
            message="Could not read video stream · Demo data",
        )
        waiting = Job(
            Path("/home/demo/Videos/Forest trail.mp4"),
            Path("Forest trail.mp4"),
            Path("/home/demo/Compressed/Forest trail.mkv"),
            media=jobs[0].media,
            size=520 * 1024**2,
        )
        jobs.extend([failed, waiting])
        for job in jobs:
            job.output_root = Path("/home/demo/Compressed")
        window.jobs = jobs
        window.sync_library()
        window.update_totals()
        window.tabs.setCurrentIndex(1)
        window.history.set_report(jobs, Path("/home/demo/Compressed"))
        window.hardware_label.setText(
            "Demo preview · Ready for AV1 / H.265 compression"
        )
        window.current.setText(
            "Demo preview · Compression complete · Originals kept in place"
        )
        window.show()
        app.processEvents()
        assert window.grab().save(str(destination / "petal.png"))
        window.show_report()
        app.processEvents()
        assert window.report_dialog.grab().save(str(destination / "report.png"))
        window.report_dialog.hide()
        window.resize(900, 740)
        app.processEvents()
        assert window.grab().save(str(destination / "compact.png"))
        dialog = QualityDialog(jobs, window)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(destination / "quality.png"))
        dialog.close()
        window.close()
        dialog.deleteLater()
        window.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    print(destination)


if __name__ == "__main__":
    main()
