"""A readable summary of measured quality; diagnostics stay one click away."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)
from .models import Job


def presentation(report: dict) -> tuple[str, str, float | None, str]:
    if report.get('geometry_note') or report.get('status') == 'Geometry mismatch':
        return 'Frame size needs attention', '#f2bb79', None, (
            'The encoded picture size differs from its container. Earlier scores may be misleading. '
            'Keep the original and try validated H.265 in a separate output folder.')
    if 'mean' not in report:
        return report.get('status', 'Not checked'), '#b2afbd', None, report.get('reason', 'A short local check will appear here when it finishes.')
    score = float(report['mean'])
    if report.get('status') == 'Review suggested':
        return 'Compare these scenes', '#efa1aa' if score < 80 else '#f2bb79', score, 'Some checked frames differ noticeably. Compare them visually; a lower score can also indicate frame alignment problems.'
    return 'Looks close in checked scenes', '#b1ddc6', score, 'The checked scenes look similar by this measure. Unchecked parts can still differ.'


def stamp(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


class QualityDialog(QDialog):
    recheck_requested = Signal(object)

    def __init__(self, jobs: list[Job], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Petal · Picture quality')
        self.resize(980, 650)
        self.setMinimumSize(780, 540)
        self.jobs = {}
        self.busy = False
        root = QVBoxLayout(self)
        title = QLabel('How close does it look?')
        title.setStyleSheet('font-size: 25px; font-weight: 600; color: #f4b4ce')
        root.addWidget(title)
        note = QLabel('Local VMAF spot checks · A similarity score, not a percentage of quality retained.')
        note.setWordWrap(True)
        root.addWidget(note)
        body = QHBoxLayout()
        self.files = QListWidget()
        self.files.setStyleSheet('QListWidget {background:#26272f; border:1px solid #3a3b46; border-radius:8px; padding:6px} '
                                'QListWidget::item {padding:10px 6px; border-radius:5px} '
                                'QListWidget::item:selected {background:#55404e; color:#fff1f7}')
        self.files.setMinimumWidth(240)
        self.files.setMaximumWidth(340)
        self.files.currentItemChanged.connect(self.select_report)
        body.addWidget(self.files, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body.addWidget(self.scroll, 2)
        root.addLayout(body, 1)
        close = QPushButton('Done')
        close.clicked.connect(self.close)
        footer = QHBoxLayout(); footer.addStretch(); footer.addWidget(close)
        root.addLayout(footer)
        self.set_jobs(jobs)

    def set_jobs(self, jobs: list[Job], busy: bool = False):
        chosen = self.files.currentItem().data(Qt.ItemDataRole.UserRole) if self.files.currentItem() else None
        self.jobs = {j.source: j for j in jobs if j.quality}
        self.busy = busy
        self.files.blockSignals(True)
        self.files.clear()
        index = 0
        for n, job in enumerate(self.jobs.values()):
            title, _, score, _ = presentation(job.quality)
            item = QListWidgetItem(f'{job.source.name}\n{score:.0f}/100 · {title}' if score is not None else f'{job.source.name}\n{title}')
            item.setToolTip(str(job.relative))
            item.setData(Qt.ItemDataRole.UserRole, job.source)
            self.files.addItem(item)
            if job.source == chosen:
                index = n
        self.files.blockSignals(False)
        if self.files.count():
            self.files.setCurrentRow(index)
        else:
            old = self.scroll.takeWidget()
            if old:
                old.deleteLater()
            self.scroll.setWidget(QLabel('No quality reports yet. Checked videos will appear here.'))

    def select_report(self, current, previous=None):
        if current is None:
            return
        job = self.jobs[current.data(Qt.ItemDataRole.UserRole)]
        report = job.quality
        status, color, score, explanation = presentation(report)
        panel = QWidget(); layout = QVBoxLayout(panel)
        name = QLabel(job.source.name); name.setTextFormat(Qt.TextFormat.PlainText); name.setWordWrap(True)
        name.setStyleSheet('font-size: 17px; font-weight: 600')
        layout.addWidget(name)
        self.score_label = QLabel(f'{score:.0f} <span style="font-size:18px">/ 100</span>' if score is not None else '—')
        self.score_label.setStyleSheet(f'font-size: 58px; font-weight: 650; color: {color}')
        layout.addWidget(self.score_label)
        label = QLabel(status); label.setStyleSheet(f'color: {color}; font-size: 18px; font-weight: 600')
        layout.addWidget(label)
        explain = QLabel(explanation); explain.setWordWrap(True)
        layout.addWidget(explain)
        if score is not None:
            stats = QLabel(f'Weakest scene  {report.get("worst_scene", score):.0f}/100     ·     Lower-scoring frames  {report.get("p10", score):.0f}/100')
            stats.setWordWrap(True)
            stats.setToolTip('Lower-scoring frames: the 10th percentile. Nine out of ten sampled frames scored at least this value.')
            layout.addWidget(stats)
            layout.addSpacing(12)
            layout.addWidget(QLabel('Scenes checked · Find these times in your player'))
            for scene in report.get('scenes', []):
                layout.addWidget(QLabel(f'{stamp(scene["start"])} – {stamp(scene["start"] + scene["duration"])}'))
                bar = QProgressBar(); bar.setRange(0, 1000)
                bar.setValue(max(0, min(1000, round(scene['mean']*10))))
                scene_color = '#b1ddc6' if scene['mean'] >= 90 else ('#f2bb79' if scene['mean'] >= 80 else '#efa1aa')
                bar.setStyleSheet(f'QProgressBar {{min-height: 16px; max-height:16px; color:#202127}} QProgressBar::chunk {{background:{scene_color}}}')
                bar.setFormat(f'{scene["mean"]:.0f}/100'); layout.addWidget(bar)
        layout.addSpacing(12)
        scope = QLabel('Only short scenes are checked. Scores compare the requested output size; they do not measure detail lost through downscaling.')
        scope.setWordWrap(True); scope.setObjectName('muted'); layout.addWidget(scope)
        actions = QHBoxLayout()
        for label, path in (('Open original', job.source), ('Open compressed', job.destination)):
            button = QPushButton(label)
            button.setEnabled(path.is_file())
            button.clicked.connect(lambda checked=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            actions.addWidget(button)
        layout.addLayout(actions)
        recheck = QPushButton('Recheck this video')
        recheck.setEnabled(not self.busy and job.destination.is_file())
        recheck.clicked.connect(lambda: self.recheck_requested.emit(job.source))
        layout.addWidget(recheck)
        details_button = QPushButton('Technical details')
        details_button.setCheckable(True)
        details = QPlainTextEdit(json.dumps(report, indent=2, ensure_ascii=False))
        details.setReadOnly(True); details.setMinimumHeight(170); details.hide()
        details_button.toggled.connect(details.setVisible)
        layout.addWidget(details_button); layout.addWidget(details)
        layout.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(panel)
