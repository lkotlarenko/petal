from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from dataclasses import asdict, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QCoreApplication,
    QEvent,
    QModelIndex,
    QObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, hardware_cache
from .capabilities import Capabilities, detect
from .discovery import discover, discover_selection, validate_roots
from .engine import Batch, benchmark
from .history import FINISHED, History
from .library_ui import LibraryActions, LibraryFilter, LibraryTable
from .media import target_size
from .models import (
    PROFILE_HELP,
    PROFILES,
    RESOLUTION_LABELS,
    RESOLUTIONS,
    Job,
    Settings,
    Status,
    xdg_path,
)
from .process import Cancelled, Runner
from .recommend import recommend
from .safety import StateStore, fingerprint, output_lock


def size_text(value: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return ""


def time_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class QueueModel(QAbstractTableModel):
    columns = (
        "Filename",
        "Folder",
        "Original",
        "Resolution",
        "Codec",
        "Status",
        "Result / details",
    )

    def __init__(self):
        super().__init__()
        self.jobs: list[Job] = []
        self.rows: dict[Path, int] = {}
        self.settings = Settings()
        self.destination_ready = True

    def replace(self, jobs):
        self.beginResetModel()
        self.jobs = jobs
        self.rows = {j.source: i for i, j in enumerate(jobs)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.jobs)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return self.columns[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        job = self.jobs[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return job
        geometry_warning = bool(
            job.quality.get("geometry_note")
            or job.quality.get("status") == "Geometry mismatch"
        )
        if role == Qt.ItemDataRole.ToolTipRole:
            quality = (
                "\nFrame size needs attention · Open Quality details"
                if geometry_warning
                else f"\nVMAF: {job.quality.get('mean', 'pending/unavailable')} · {job.quality.get('status', '')}"
                if job.quality
                else ""
            )
            destination = (
                str(job.destination)
                if self.destination_ready
                else "Choose an output directory"
            )
            return f"{job.source}\n→ {destination}\n{job.message}{quality}"
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 5:
            return QColor(
                {
                    Status.COMPLETED: "#b1ddc6",
                    Status.FAILED: "#ff999e",
                    Status.SKIPPED: "#c7b7db",
                    Status.ENCODING: "#f2adc7",
                    Status.VERIFYING: "#f2adc7",
                }.get(job.status, "#b5b4c2")
            )
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        media = job.media
        resolution = "—"
        if media:
            v = media.video
            try:
                w, h = (
                    job.output_dimensions
                    if job.status == Status.COMPLETED and job.output_dimensions
                    else target_size(media, self.settings)
                )
                resolution = (
                    f"{w}×{h}"
                    if (v["width"], v["height"]) == (w, h)
                    else f"{v['width']}×{v['height']} → {w}×{h}"
                )
            except ValueError:
                resolution = f"{v.get('width')}×{v.get('height')}"
        result = job.message
        if job.relocated_to:
            result = f"Moved original → {job.relocated_to}"
        elif job.relocation_error:
            result += f" · Move incomplete: {job.relocation_error}"
        if job.status == Status.COMPLETED:
            savings = 100 * (1 - job.final_size / job.size) if job.size else 0
            result = (
                f"{size_text(job.final_size)} · {savings:.1f}% saved · {job.message}"
            )
            if geometry_warning:
                result = "Frame size needs attention · " + result
            elif "mean" in job.quality:
                result = (
                    f"VMAF {job.quality['mean']:.1f} · {job.quality['status']} · "
                    + result
                )
        return (
            job.source.name,
            str(job.relative.parent),
            size_text(job.size),
            resolution,
            media.video.get("codec_name", "?") if media else "—",
            job.status.value,
            result,
        )[index.column()]

    def update_job(self, job):
        row = self.rows.get(job.source)
        if row is not None:
            self.dataChanged.emit(
                self.index(row, 0), self.index(row, len(self.columns) - 1)
            )


class Worker(QObject):
    event = Signal(str, object)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self):
        try:
            self.result.emit(self.operation(self.event.emit))
        except Cancelled:
            self.event.emit("log", "Operation cancelled safely")
        except Exception as error:
            logging.getLogger("petal").exception("Background operation failed")
            self.error.emit(str(error))
        finally:
            self.finished.emit()


STYLE = """
QWidget { background: #202127; color: #edebf2; font-size: 13px; }
QMainWindow { background: #202127; }
QLabel#title { font-size: 32px; font-weight: 650; color: #f4b4ce; }
QLabel#subtitle, QLabel#muted { color: #b2afbd; }
QLabel#saved { color: #c4e4d2; font-size: 25px; font-weight: 600; }
QFrame#card { background: #292a32; border: 1px solid #383943; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #34353f; border: 1px solid #454650; border-radius: 8px; padding: 9px 16px; }
QPushButton:hover { background: #44424e; border-color: #ca93ac; }
QPushButton:pressed { background: #514552; }
QPushButton:disabled { color: #777580; border-color: #383943; }
QPushButton#primary { background: #edabc6; color: #27222b; border: none; font-weight: 650; }
QPushButton#primary:hover { background: #f5c1d6; }
QPushButton#primary:disabled { background: #594550; color: #92818a; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #202127; border: 1px solid #484650; border-radius: 6px; padding: 7px; selection-background-color: #ad6d89; }
QComboBox QAbstractItemView { background: #292a32; selection-background-color: #594550; }
QCheckBox { spacing: 8px; }
QTableView { background: #26272f; alternate-background-color: #2a2b34; border: 1px solid #3a3b46; border-radius: 9px; gridline-color: #34353f; selection-background-color: #55404e; }
QTableView::item { padding: 7px; border: none; }
QHeaderView::section { background: #30313a; color: #c8c3d0; border: none; border-bottom: 1px solid #41414c; padding: 10px 8px; }
QProgressBar { background: #18191f; border: none; border-radius: 5px; min-height: 10px; max-height: 10px; text-align: center; }
QProgressBar::chunk { background: #e9a3c0; border-radius: 5px; }
QPlainTextEdit { background: #191a20; color: #cac3cf; border: 1px solid #393843; border-radius: 8px; font-family: monospace; font-size: 11px; }
QToolTip { background: #38333f; color: #ffffff; border: 1px solid #9b7288; padding: 6px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background:#292a32; border-radius:8px; padding:10px 28px; margin:0 8px 10px 0; color:#aaa8b4; }
QTabBar::tab:selected { background:#55404e; color:#ffe4ef; }
QMenu { background:#2c2c35; border:1px solid #47434d; padding:6px; }
QMenu::item { padding:9px 22px; border-radius:5px; }
QMenu::item:selected { background:#55404e; }
QMenu::item:disabled { color:#777580; }
QSplitter::handle { background: #202127; height: 5px; }
"""


class Window(LibraryActions, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Petal {__version__} · Video compressor")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("petal.png"))))
        self.resize(1220, 850)
        self.setMinimumSize(860, 640)
        self.config_path = xdg_path("config") / "settings.json"
        self.state_dir = xdg_path("state")
        logger = logging.getLogger("petal")
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            self.state_dir / "petal.log", maxBytes=4 * 1024 * 1024, backupCount=3
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        self.log_handler = handler
        self.jobs = []
        self.history = History(self.state_dir)
        self.batch_jobs = []
        self.report_dialog = None
        self.relocation_recovered = set()
        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(400)
        self.history_timer.timeout.connect(self.save_history)
        self.caps: Capabilities | None = None
        self.batch: Batch | None = None
        self.thread = None
        self.worker = None
        self.completion = None
        self.operation_succeeded = False
        self.cancel = threading.Event()
        self.busy = ""
        self.pause_requested = False
        self.closing = False
        self.started_at = 0.0
        self.last_fields = {}
        self.active_job: Job | None = None
        self.active_verifier: Job | None = None
        self.quality_results: dict[Path, dict] = {}
        self.drop_inputs: list[Path] = []
        self.drop_namespaces: dict[str, str] = {}
        self.pending_drops: list[Path] = []
        self.cached_hardware = False
        self.loading = True
        self.recommendation = None
        self.hardware_recheck = QTimer(self)
        self.hardware_recheck.setSingleShot(True)
        self.hardware_recheck.setInterval(500)
        self.hardware_recheck.timeout.connect(self.initialize_hardware)
        self.setAcceptDrops(True)
        self.setup_ui()
        self.setMinimumHeight(
            max(700, self.centralWidget().minimumSizeHint().height() + 24)
        )
        self.load_config()
        self.jobs = [j for j in self.history.jobs.values() if j.status not in FINISHED]
        try:
            restored_roots = self.input_roots()
            if all(j.output_root == restored_roots[1] for j in self.jobs):
                self.scanned_roots = restored_roots
                self.scanned_selection = self.input_signature()
        except (OSError, ValueError):
            pass
        self.sync_library()
        self.loading = False
        self.resolution_changed()
        self.hardware_recheck.stop()
        QTimer.singleShot(0, self.initialize_hardware)

    def card(self):
        frame = QFrame()
        frame.setObjectName("card")
        return frame

    def button(self, label, slot, primary=False):
        button = QPushButton(label)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(slot)
        return button

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("✿  Petal")
        title.setObjectName("title")
        subtitle = QLabel("A little lighter. Every memory kept.")
        subtitle.setObjectName("subtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()
        self.saved = QLabel("0 B saved")
        self.saved.setObjectName("saved")
        header.addWidget(self.saved)
        self.settings_button = self.button("Settings", self.show_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)
        paths = self.card()
        form = QFormLayout(paths)
        form.setContentsMargins(16, 12, 16, 12)
        self.source = QLineEdit()
        self.output = QLineEdit()
        self.source.setAcceptDrops(False)
        self.output.setAcceptDrops(False)
        self.source.setPlaceholderText("Your original video collection")
        self.output.setPlaceholderText("A separate folder for compressed videos")
        self.folder_buttons = []
        for label, edit in (
            ("Source directory", self.source),
            ("Output directory", self.output),
        ):
            row = QHBoxLayout()
            row.addWidget(edit)
            button = self.button(
                "Choose…", lambda checked=False, e=edit: self.choose_directory(e)
            )
            self.folder_buttons.append(button)
            row.addWidget(button)
            form.addRow(label, row)
        options = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(list(PROFILES))
        self.profile.setCurrentText("Balanced")
        self.profile.currentTextChanged.connect(self.profile_changed)
        options.addWidget(self.profile)
        self.profile_description = QLabel(PROFILE_HELP["Balanced"])
        self.profile_description.setWordWrap(True)
        self.profile_description.setObjectName("muted")
        self.resolution = QComboBox()
        for key, label in RESOLUTION_LABELS.items():
            self.resolution.addItem(label, key)
        self.resolution.setCurrentIndex(self.resolution.findData("1080p"))
        self.resolution.currentIndexChanged.connect(self.resolution_changed)
        options.addWidget(QLabel("Target"))
        options.addWidget(self.resolution)
        options.addWidget(self.profile_description, 1)
        self.scan_button = self.button("Scan folders", self.scan)
        options.addWidget(self.scan_button)
        form.addRow("Compression", options)
        layout.addWidget(paths)
        self.settings_card = self.card()
        setting_form = QFormLayout(self.settings_card)
        self.codec = QComboBox()
        self.codec.addItem("AV1 · AMD VA-API", "av1_vaapi")
        self.codec.addItem("H.265 · AMD VA-API", "hevc_vaapi")
        self.codec.currentIndexChanged.connect(self.refresh_controls)
        self.device = QLineEdit("/dev/dri/renderD128")
        self.device.textChanged.connect(self.refresh_controls)
        self.strict = QCheckBox("Thorough verification · recommended")
        self.strict.setChecked(True)
        self.strict.setToolTip(
            "Uncheck for faster metadata/timeline checks plus sampled decoding. Full mode decodes every frame using up to four CPU threads."
        )
        self.auto_quality = QCheckBox("Check picture quality on the first 3 new videos")
        self.auto_quality.setChecked(True)
        self.auto_quality.setToolTip(
            "Low-priority CPU analysis of reproducible scenes at output resolution. Reports mean, low-percentile and worst-scene scores; samples are not a guarantee for the entire file."
        )
        self.minimum = QDoubleSpinBox()
        self.minimum.setRange(0, 99)
        self.minimum.setValue(5)
        self.minimum.setSuffix(" %")
        self.audio = QComboBox()
        self.audio.addItem("Preserve original audio · stream copy", "copy")
        self.audio.addItem("Opus · preserve tracks and channel counts", "opus")
        self.incompatible = QComboBox()
        self.incompatible.addItem(
            "Maximum compatibility · preserve / convert supported streams", "compatible"
        )
        self.incompatible.addItem("Skip file and explain (safest)", "skip")
        self.incompatible.addItem(
            "Explicitly omit incompatible streams / cover art", "omit"
        )
        self.incompatible.setToolTip(
            "Maximum compatibility preserves cover art as attachments, timecode tracks as metadata, and converts MOV text subtitles to ASS. Other unsupported streams still need an explicit omission choice. All conversions are logged. HDR and multiple video angles are skipped."
        )
        setting_form.addRow("Video codec", self.codec)
        setting_form.addRow("VA-API device", self.device)
        setting_form.addRow("Verification", self.strict)
        setting_form.addRow("Quality guard", self.auto_quality)
        setting_form.addRow("Minimum savings", self.minimum)
        setting_form.addRow("Audio", self.audio)
        setting_form.addRow("Incompatible streams", self.incompatible)
        checks = QVBoxLayout()
        self.check_button = self.button("Run hardware check", self.hardware_check)
        checks.addWidget(self.check_button)
        self.sample_button = self.button("Compare selected video samples", self.samples)
        checks.addWidget(self.sample_button)
        self.log_folder_button = self.button(
            "Open logs",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.state_dir))),
        )
        checks.addWidget(self.log_folder_button)
        self.desktop_button = self.button(
            "Add AppImage to applications", self.install_desktop
        )
        import os

        self.desktop_button.setEnabled(bool(os.environ.get("APPIMAGE")))
        checks.addWidget(self.desktop_button)
        setting_form.addRow(checks)
        self.settings_dialog = QDialog(self)
        self.settings_dialog.setWindowTitle("Petal · Compression settings")
        self.settings_dialog.setMinimumWidth(680)
        dialog_layout = QVBoxLayout(self.settings_dialog)
        dialog_layout.addWidget(self.settings_card)
        close_settings = self.button("Done", self.settings_dialog.hide)
        dialog_layout.addWidget(close_settings)
        self.hardware_label = QLabel("Checking FFmpeg and hardware…")
        self.hardware_label.setObjectName("muted")
        self.hardware_label.setWordWrap(True)
        layout.addWidget(self.hardware_label)
        self.advice_card = self.card()
        advice_layout = QHBoxLayout(self.advice_card)
        advice_text = QVBoxLayout()
        self.advice_title = QLabel("Recommended for you")
        self.advice_title.setStyleSheet("color: #edabc6; font-weight: 600")
        self.advice_detail = QLabel()
        self.advice_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.advice_detail.setWordWrap(True)
        advice_text.addWidget(self.advice_title)
        advice_text.addWidget(self.advice_detail)
        advice_layout.addLayout(advice_text, 1)
        self.advice_button = self.button("Apply", self.apply_recommendation)
        advice_layout.addWidget(self.advice_button)
        self.advice_card.hide()
        layout.addWidget(self.advice_card)
        queue_heading = QHBoxLayout()
        self.queue_label = QLabel(
            "Your queue · Drop videos/folders here, or choose a source"
        )
        queue_heading.addWidget(self.queue_label, 1)
        self.retry_button = self.button("Queue failed again", self.retry)
        self.skip_button = self.button("Skip selected", self.skip_selected)
        self.add_button = self.button("Add videos…", self.add_files)
        self.remove_button = self.button("Remove selected", self.remove_selected)
        self.report_button = self.button("Batch report", self.show_report)
        queue_heading.addWidget(self.add_button)
        queue_heading.addWidget(self.remove_button)
        queue_heading.addWidget(self.report_button)
        queue_heading.addWidget(self.retry_button)
        queue_heading.addWidget(self.skip_button)
        layout.addLayout(queue_heading)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a filename, folder or result…")
        self.search.setClearButtonEnabled(True)
        self.result_filter = QComboBox()
        for label, value in [
            ("All results", None),
            ("Compressed", Status.COMPLETED),
            ("Skipped", Status.SKIPPED),
            ("Needs attention", Status.FAILED),
        ]:
            self.result_filter.addItem(label, value)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.result_filter)
        layout.addLayout(search_row)
        self.model = QueueModel()
        self.tabs = QTabWidget()
        self.tables = []
        self.filters = []
        for finished, label in [(False, "Jobs"), (True, "Finished")]:
            proxy = LibraryFilter(finished, self)
            proxy.setSourceModel(self.model)
            table = LibraryTable(finished)
            table.setModel(proxy)
            table.setAcceptDrops(False)
            table.viewport().setAcceptDrops(False)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setWordWrap(False)
            table.verticalHeader().hide()
            table.verticalHeader().setDefaultSectionSize(42)
            table.setMinimumHeight(160)
            table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Interactive
            )
            table.horizontalHeader().setStretchLastSection(True)
            for i, width in enumerate((240, 110, 90, 135, 80, 100, 120)):
                table.setColumnWidth(i, width)
            table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            table.customContextMenuRequested.connect(
                lambda point, t=table: self.file_menu(t, point)
            )
            table.doubleClicked.connect(
                lambda index: self.open_job(index.data(Qt.ItemDataRole.UserRole))
            )
            table.selectionModel().selectionChanged.connect(self.refresh_controls)
            self.tables.append(table)
            table.setColumnHidden(4, True)
            table.horizontalHeader().setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            table.horizontalHeader().customContextMenuRequested.connect(
                lambda point, t=table: self.column_menu(t, point)
            )
            self.filters.append(proxy)
            self.tabs.addTab(table, label)
        self.table, self.finished_table = self.tables
        self.search.textChanged.connect(self.filter_library)
        self.result_filter.currentIndexChanged.connect(self.filter_library)
        self.tabs.currentChanged.connect(self.refresh_controls)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.tabs)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setAcceptDrops(False)
        self.logs.setMaximumBlockCount(2500)
        self.logs.setMinimumHeight(85)
        self.splitter.addWidget(self.logs)
        self.logs.hide()
        layout.addWidget(self.splitter, 1)
        progress_card = self.card()
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(16, 12, 16, 12)
        self.current = QLabel("Ready when you are")
        self.current.setTextFormat(Qt.TextFormat.PlainText)
        self.current.setWordWrap(True)
        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 1000)
        self.file_progress.setTextVisible(False)
        self.metrics = QLabel(
            "Originals stay in place. Organize skipped or failed files after the batch."
        )
        self.metrics.setObjectName("muted")
        self.metrics.setWordWrap(True)
        self.overall_label = QLabel("Queue progress · 0 files")
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall.setTextVisible(False)
        self.verification_status = QLabel("CPU verifier · Idle")
        self.verification_status.setTextFormat(Qt.TextFormat.PlainText)
        self.verification_status.setWordWrap(True)
        self.verify_progress = QProgressBar()
        self.verify_progress.setRange(0, 1000)
        self.verify_progress.setTextVisible(False)
        for widget in (
            self.current,
            self.file_progress,
            self.metrics,
            self.verification_status,
            self.verify_progress,
            self.overall_label,
            self.overall,
        ):
            progress_layout.addWidget(widget)
        quality_row = QHBoxLayout()
        self.quality_label = QLabel(
            "Picture quality · A few scenes are checked automatically"
        )
        self.quality_label.setTextFormat(Qt.TextFormat.PlainText)
        self.quality_label.setWordWrap(True)
        self.quality_button = self.button("Quality details", self.show_quality)
        self.recheck_quality_button = self.button(
            "Check selected", self.recheck_quality
        )
        quality_row.addWidget(self.quality_label, 1)
        quality_row.addWidget(self.recheck_quality_button)
        quality_row.addWidget(self.quality_button)
        progress_layout.addLayout(quality_row)
        layout.addWidget(progress_card)
        controls = QHBoxLayout()
        self.start_button = self.button("Compress videos", self.start, True)
        self.pause_button = self.button("Pause after current", self.pause)
        self.stop_button = self.button("Stop safely", self.stop)
        self.log_button = self.button("Show detailed log", self.toggle_log)
        for widget in (self.start_button, self.pause_button, self.stop_button):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(self.log_button)
        layout.addLayout(controls)
        self.refresh_controls()

    def load_config(self):
        try:
            data = json.loads(self.config_path.read_text())
            values = dict(data.get("settings", {}))
            if data.get("version", 1) < 2 and values.get("incompatible") == "skip":
                values["incompatible"] = "compatible"
            settings = Settings(**values)
            self.source.setText(data.get("source", ""))
            self.output.setText(data.get("output", ""))
            self.profile.setCurrentText(settings.profile)
            self.codec.setCurrentIndex(self.codec.findData(settings.codec))
            self.device.setText(settings.device)
            self.strict.setChecked(settings.strict)
            self.auto_quality.setChecked(settings.auto_quality)
            self.minimum.setValue(settings.min_savings)
            self.audio.setCurrentIndex(self.audio.findData(settings.audio))
            self.incompatible.setCurrentIndex(
                self.incompatible.findData(settings.incompatible)
            )
            self.resolution.setCurrentIndex(
                self.resolution.findData(settings.resolution)
            )
            self.drop_inputs = [
                Path(p) for p in data.get("drop_inputs", []) if isinstance(p, str)
            ]
            self.drop_namespaces = {
                str(k): str(v) for k, v in data.get("drop_namespaces", {}).items()
            }
            if self.drop_inputs:
                self.show_drop_sources()
        except (OSError, ValueError, TypeError):
            pass

    def show_settings(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def settings(self):
        return Settings(
            codec=self.codec.currentData(),
            profile=self.profile.currentText(),
            device=self.device.text().strip(),
            strict=self.strict.isChecked(),
            min_savings=self.minimum.value(),
            audio=self.audio.currentData(),
            incompatible=self.incompatible.currentData(),
            auto_quality=self.auto_quality.isChecked(),
            resolution=self.resolution.currentData(),
        )

    def save_config(self):
        data = {
            "version": 3,
            "source": self.source.text(),
            "output": self.output.text(),
            "settings": asdict(self.settings()),
            "drop_inputs": [str(p) for p in self.drop_inputs],
            "drop_namespaces": self.drop_namespaces,
        }
        temp = self.config_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(self.config_path)

    def profile_changed(self, text):
        self.profile_description.setText(PROFILE_HELP[text])
        if hasattr(self, "model"):
            self.model.settings = self.settings()
            self.update_recommendation()
        self.refresh_controls()

    def resolution_changed(self, *_):
        if not hasattr(self, "model"):
            return
        self.model.settings = self.settings()
        if self.model.rowCount():
            self.model.dataChanged.emit(
                self.model.index(0, 0), self.model.index(self.model.rowCount() - 1, 6)
            )
        config = self.settings()
        limit = RESOLUTIONS[config.resolution]
        label = (
            f"{limit[0]}×{limit[1]} landscape, swapped for portrait"
            if limit
            else "Original dimensions"
        )
        self.resolution.setToolTip(
            f"{label}. Never upscales. Quality adapts to each file's actual output size, using the selected profile."
        )
        self.profile_description.setToolTip(
            f"Nominal {config.codec} CQP at this ceiling: {config.quality}. Smaller output dimensions use their own quality setting. Defaults are provisional; use samples/VMAF to review."
        )
        self.refresh_controls()
        if not self.loading and not self.busy:
            self.hardware_recheck.start()

    def choose_directory(self, edit):
        selected = QFileDialog.getExistingDirectory(
            self, "Choose directory", edit.text() or str(Path.home())
        )
        if selected:
            if edit is self.source:
                self.drop_inputs.clear()
                self.drop_namespaces.clear()
                self.source.setReadOnly(False)
            edit.setText(selected)
            if edit is self.output and self.drop_inputs and not self.busy:
                self.scan()

    def launch(self, kind, operation, completed):
        if self.thread:
            return
        self.busy = kind
        self.operation_succeeded = False
        self.cancel = threading.Event()
        self.thread = QThread(self)
        self.worker = Worker(operation)
        self.completion = completed
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self.handle_event, Qt.ConnectionType.QueuedConnection)
        self.worker.result.connect(
            self.deliver_result, Qt.ConnectionType.QueuedConnection
        )
        self.worker.error.connect(self.error, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.work_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.refresh_controls()
        self.thread.start()

    @Slot(object)
    def deliver_result(self, result):
        self.operation_succeeded = True
        if self.completion:
            self.completion(result)

    @Slot()
    def work_finished(self):
        completed_kind = self.busy
        self.thread = None
        self.worker = None
        self.completion = None
        self.batch = None
        self.busy = ""
        self.pause_requested = False
        self.pause_button.setText("Pause after current")
        self.refresh_controls()
        if completed_kind == "batch" and self.operation_succeeded:
            self.history.set_report(
                self.jobs, getattr(self, "scanned_roots", (None, None))[1]
            )
            self.save_history()
            if not self.closing:
                self.show_report()
        if self.report_dialog:
            self.report_dialog.set_report(
                self.history.report, self.history.output, bool(self.busy)
            )
        if completed_kind == "recovery" and not self.closing:
            QTimer.singleShot(0, self.initialize_hardware)
        if self.closing:
            self.close()
        elif self.pending_drops:
            paths, self.pending_drops = self.pending_drops, []
            self.statusBar().clearMessage()
            self.add_inputs(paths)
        self.update_recommendation()

    @Slot(str)
    def error(self, message):
        self.current.setText("Operation failed · " + message[:300])
        self.logs.appendPlainText(message)
        self.logs.show()
        QMessageBox.warning(self, "Petal", message[:2500])

    def initialize_hardware(self):
        if self.busy or self.closing:
            return
        output = (
            Path(self.output.text()).expanduser().resolve()
            if self.output.text().strip()
            else self.history.output
        )
        if output and output not in self.relocation_recovered:
            self.relocation_recovered.add(output)
            from .relocate import Relocator

            try:
                recovery = Relocator(output, self.state_dir, self.cancel)
            except (OSError, ValueError) as error:
                self.logs.appendPlainText(
                    f"Relocation journal needs attention: {error}"
                )
                recovery = None
            if recovery and recovery.path.exists():

                def operation(event):
                    recovery.cancel = self.cancel
                    recovery.event = event
                    with output_lock(output):
                        recovery.recover()
                    return recovery.records

                def recovered(records):
                    for job in self.model.jobs + self.history.report:
                        record = records.get(str(job.source), {})
                        if record.get("phase") == "moved":
                            job.relocated_to = Path(record["destination"])
                    self.sync_library()
                    self.save_history()

                self.launch("recovery", operation, recovered)
                return
        config = self.settings()
        cached = hardware_cache.load(self.state_dir, config)
        if cached is None:
            self.hardware_check()
            return
        self.caps, self.cached_hardware = cached, True
        self.current.setText("Ready · Saved hardware validation loaded")
        self.adopt_verified_fallback()
        self.refresh_controls()
        try:
            roots = self.input_roots()
            store = StateStore(
                self.state_dir,
                *roots,
                namespace="selection" if self.drop_inputs else None,
            )
            if store.data["temporary"]:

                def recover(event):
                    with output_lock(roots[1]):
                        for line in store.recover(roots[1]):
                            event("log", line)

                self.launch(
                    "recovery",
                    recover,
                    lambda _: self.current.setText(
                        "Ready · Interrupted temporary outputs recovered"
                    ),
                )
        except (OSError, ValueError):
            pass

    def show_drop_sources(self):
        self.source.setReadOnly(True)
        self.source.setText(f"{len(self.drop_inputs)} added locations")
        self.source.setToolTip("\n".join(str(p) for p in self.drop_inputs))

    def input_roots(self, allow_unset_output=False):
        output = (
            Path(self.output.text()).expanduser().resolve()
            if self.output.text().strip()
            else None
        )
        if self.drop_inputs:
            if output is None and not allow_unset_output:
                raise ValueError("Choose an output directory")
            first = self.drop_inputs[0]
            return (first if first.is_dir() else first.parent), output
        if not self.source.text().strip() or output is None:
            raise ValueError("Choose both source and output directories")
        return validate_roots(Path(self.source.text()), output)

    def input_signature(self):
        return tuple(str(p) for p in self.drop_inputs)

    def add_inputs(self, paths):
        paths = [Path(p).expanduser().absolute() for p in paths]
        if self.busy:
            self.pending_drops.extend(p for p in paths if p not in self.pending_drops)
            self.statusBar().showMessage(
                "Added locations will be scanned when the current operation finishes"
            )
            return
        if not self.drop_inputs and self.source.text().strip():
            original = Path(self.source.text()).expanduser().resolve()
            if original.is_dir():
                self.drop_inputs.append(original)
                self.drop_namespaces[str(original)] = ""
        for path in paths:
            if path.is_symlink() or not path.exists():
                self.logs.appendPlainText(
                    f"Ignored unavailable or symbolic-link input: {path}"
                )
                continue
            path = path.resolve()
            if path not in self.drop_inputs:
                self.drop_inputs.append(path)
        if self.drop_inputs:
            self.show_drop_sources()
            self.scan()

    def dragEnterEvent(self, event):
        if (
            event.possibleActions() & Qt.DropAction.CopyAction
            and event.mimeData().hasUrls()
            and all(url.isLocalFile() for url in event.mimeData().urls())
        ):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if (
            event.possibleActions() & Qt.DropAction.CopyAction
            and event.mimeData().hasUrls()
            and all(url.isLocalFile() for url in event.mimeData().urls())
        ):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.add_inputs(
                [Path(url.toLocalFile()) for url in event.mimeData().urls()]
            )
        else:
            event.ignore()

    def update_recommendation(self):
        self.recommendation = recommend(self.settings(), self.jobs)
        advice = self.recommendation
        self.advice_card.setVisible(advice is not None)
        self.setMinimumHeight(
            max(700, self.centralWidget().minimumSizeHint().height() + 24)
        )
        if advice:
            self.advice_title.setText("Recommended for you · " + advice.title)
            self.advice_detail.setText(advice.detail)
            self.advice_button.setText(advice.button or "Applied")
            self.advice_button.setVisible(bool(advice.action))
            self.advice_button.setEnabled(
                not self.busy or self.busy == "batch" or advice.action == "details"
            )

    def apply_recommendation(self):
        advice = self.recommendation
        if not advice:
            return
        if advice.action == "details":
            self.show_quality()
            return
        if self.batch:
            try:
                self.batch.request_options(
                    profile=advice.value if advice.action == "profile" else None,
                    strict=False if advice.action == "fast" else None,
                )
                self.advice_button.setEnabled(False)
                self.advice_button.setText("Applies after current files")
            except ValueError as error:
                self.error(str(error))
            return
        if self.busy:
            return
        if advice.action == "profile":
            self.profile.setCurrentText(advice.value)
        elif advice.action == "fast":
            self.strict.setChecked(False)
        self.save_config()
        self.update_recommendation()

    def hardware_check(self):
        try:
            settings = self.settings()
        except ValueError as error:
            self.error(str(error))
            return
        self.current.setText(
            f"Testing real {RESOLUTION_LABELS[settings.resolution]} hardware encodes and decodes · First setup for this configuration…"
        )
        try:
            roots = (
                self.input_roots()
                if self.source.text() and self.output.text()
                else None
            )
        except (OSError, ValueError):
            roots = None
        selection = bool(self.drop_inputs)

        def operation(event):
            if roots:
                with output_lock(roots[1]):
                    store = StateStore(
                        self.state_dir,
                        *roots,
                        namespace="selection" if selection else None,
                    )
                    for message in store.recover(roots[1]):
                        event("log", message)
            return detect(settings, self.worker_runner(event))

        def completed(report):
            self.caps, self.cached_hardware = report, False
            hardware_cache.save(self.state_dir, settings, report)
            report_path = self.state_dir / "hardware-report.json"
            report_path.write_text(
                json.dumps(report.to_dict(), indent=2), encoding="utf-8"
            )
            passed = sum(report.tested.values())
            self.current.setText(
                f"Hardware check: {passed} configurations passed. Report: {report_path}"
            )
            self.hardware_label.setText(self.capability_text())
            if report.errors:
                self.logs.appendPlainText("\n".join(report.errors))
            self.adopt_verified_fallback()
            self.refresh_controls()

        self.launch("hardware", operation, completed)

    def worker_runner(self, event):
        def log(message):
            logging.getLogger("petal").info(message)
            event("log", message)

        return Runner(self.cancel, log)

    def adopt_verified_fallback(self):
        config = self.settings()
        if (
            self.caps
            and config.codec == "av1_vaapi"
            and not any(self.caps.supports(config, bits) for bits in (8, 10))
            and any(
                self.caps.supports(replace(config, codec="hevc_vaapi"), bits)
                for bits in (8, 10)
            )
        ):
            self.codec.setCurrentIndex(self.codec.findData("hevc_vaapi"))
            message = "Using validated H.265 VA-API: AV1 did not pass the real output checks. See the hardware log."
            self.current.setText(message)
            self.logs.appendPlainText(message)
            self.save_config()

    def capability_text(self):
        if not self.caps:
            return "Hardware has not been validated"
        try:
            config = self.settings()
            if any(self.caps.supports(config, bits) for bits in (8, 10)):
                return (
                    f"Ready · {'AV1' if config.codec == 'av1_vaapi' else 'H.265'} · {config.profile} · {RESOLUTION_LABELS[config.resolution]}"
                    + (" · saved check" if self.cached_hardware else "")
                )
        except ValueError:
            pass
        return "Hardware unavailable for this selection · Run hardware check, or select a verified H.265 configuration in Settings"

    def scan(self):
        try:
            source, output = self.input_roots(allow_unset_output=True)
            self.save_config()
        except (OSError, ValueError) as error:
            self.error(str(error))
            return
        inputs, namespaces = list(self.drop_inputs), dict(self.drop_namespaces)
        preferred = {
            str(j.relative): j.destination
            for j in self.jobs
            if output and j.destination.is_absolute()
        }
        previous = {j.source: j for j in self.jobs}
        self.jobs = []
        self.history.jobs = {
            source: job
            for source, job in self.history.jobs.items()
            if job.status in FINISHED
        }
        self.quality_results.clear()
        self.sync_library()
        self.model.destination_ready = output is not None
        self.current.setText("Scanning local videos and reading media metadata…")

        def operation(event):
            if output:
                with output_lock(output):
                    from .relocate import Relocator

                    Relocator(output, self.state_dir, self.cancel, event).recover()
                    store = StateStore(
                        self.state_dir,
                        source,
                        output,
                        namespace="selection" if inputs else None,
                    )
                    for line in store.recover(output):
                        event("log", line)
                    # Preserve completed records when a folder queue becomes a mixed drop queue.
                    if inputs:
                        for root, prefix in namespaces.items():
                            if prefix == "":
                                old = StateStore(self.state_dir, Path(root), output)
                                for name, record in old.data["jobs"].items():
                                    store.data["jobs"].setdefault(name, record)
                        store.save()
                    for relative, record in store.data["jobs"].items():
                        if isinstance(record, dict) and record.get("destination"):
                            preferred.setdefault(relative, Path(record["destination"]))
            count = 0

            def found(job):
                nonlocal count
                count += 1
                event("scan", (count, str(job.relative)))

            runner = self.worker_runner(event)
            from .scan_cache import ProbeCache

            try:
                cache = ProbeCache(xdg_path("cache") / "discovery.sqlite3")
            except (OSError, __import__("sqlite3").DatabaseError) as error:
                event(
                    "log",
                    f"Discovery cache unavailable; continuing without it: {error}",
                )
                cache = None
            try:
                began = time.monotonic()
                jobs = (
                    discover_selection(
                        inputs,
                        output,
                        runner,
                        namespaces,
                        found,
                        preferred,
                        cache=cache,
                    )
                    if inputs
                    else discover(source, output, runner, found, preferred, cache=cache)
                )
                event(
                    "log",
                    f"Scan: {len(jobs)} videos in {time.monotonic() - began:.2f}s"
                    + (
                        f" · {cache.hits} cached / {cache.misses} probed"
                        if cache
                        else ""
                    ),
                )
            finally:
                if cache:
                    cache.close()
            for job in jobs:
                record = store.data["jobs"].get(str(job.relative), {}) if output else {}
                if record.get("status") == "Completed" and record.get(
                    "destination"
                ) == str(job.destination):
                    try:
                        if fingerprint(job.source) == record.get(
                            "source"
                        ) and fingerprint(job.destination) == record.get("output"):
                            job.status = Status.COMPLETED
                            job.message = "Recorded verified compression · unchanged file identities"
                            job.final_size = job.destination.stat().st_size
                            job.source_key = record["source"]
                            job.quality = record.get("quality", {})
                            job.output_dimensions = record.get("dimensions")
                            job.encoder_profile = record.get("profile")
                            job.encoder_codec = record.get("codec")
                            job.encoder_quality = record.get("encoder_quality")
                            job.progress = 1
                    except (OSError, ValueError):
                        pass
                old = previous.get(job.source)
                if (
                    old
                    and old.destination == job.destination
                    and old.status == Status.SKIPPED
                    and old.message == "Skipped by user"
                ):
                    job.status, job.message = old.status, old.message
            return jobs, namespaces

        def completed(result):
            jobs, self.drop_namespaces = result
            self.jobs = jobs
            for job in jobs:
                job.output_root = output
                if job.status in FINISHED:
                    self.history.jobs[job.source] = job
            self.sync_library()
            self.history_timer.start()
            self.scanned_roots = (source, output)
            self.scanned_selection = tuple(str(p) for p in inputs)
            if any(j.status in FINISHED for j in jobs):
                self.history.set_report(jobs, output)
                self.save_history()
            self.current.setText(
                f"Scan complete · {len(jobs)} videos · "
                + (
                    "Review the queue before starting"
                    if output
                    else "Choose an output directory to continue"
                )
            )
            self.save_config()
            self.update_totals()
            self.update_recommendation()

        self.launch("scan", operation, completed)

    def start(self):
        try:
            roots = self.input_roots()
            if roots != getattr(
                self, "scanned_roots", None
            ) or self.input_signature() != getattr(self, "scanned_selection", ()):
                raise ValueError("Folders changed; scan again before encoding")
            config = self.settings()
            if not self.caps or not any(
                self.caps.supports(config, bits) for bits in (8, 10)
            ):
                raise ValueError(
                    "This codec/profile has no passing hardware test. Check Settings."
                )
            self.save_config()
            for job in self.jobs:
                if job.status == Status.CANCELLED:
                    job.status, job.progress = Status.WAITING, 0
            self.started_at = time.monotonic()
            self.active_job = None
            self.batch_jobs = [
                j
                for j in self.jobs
                if j.status in (Status.WAITING, Status.CANCELLED) and not j.relocated_to
            ]
            if not self.batch_jobs:
                self.show_report()
                return
            batch = Batch(
                self.batch_jobs,
                *roots,
                config,
                self.caps,
                self.state_dir,
                selection=bool(self.drop_inputs),
            )
            self.batch = batch

            def operation(event):
                batch.event = event
                batch.run()

            self.launch(
                "batch",
                operation,
                lambda _: self.current.setText(
                    "Batch finished · Review results in the queue"
                ),
            )
        except (OSError, ValueError) as error:
            self.error(str(error))

    def pause(self):
        if self.batch:
            self.pause_requested = not self.pause_requested
            self.batch.pause(self.pause_requested)
            self.pause_button.setText(
                "Resume queue" if self.pause_requested else "Pause after current"
            )
            if self.pause_requested:
                self.current.setText(
                    "Pause requested · Finishing the current file and verification"
                )

    def stop(self):
        if self.batch:
            self.batch.stop()
        self.cancel.set()
        self.current.setText("Stopping safely…")

    def retry(self):
        self.queue_again([j for j in self.model.jobs if j.status == Status.FAILED])

    def selected_jobs(self):
        if not hasattr(self, "tabs"):
            return []
        return [
            i.data(Qt.ItemDataRole.UserRole)
            for i in self.tabs.currentWidget().selectionModel().selectedRows()
        ]

    def skip_selected(self):
        selected = self.selected_jobs()
        if self.batch:
            self.batch.skip([j.source for j in selected if j.status == Status.WAITING])
        else:
            for job in selected:
                if not job.relocated_to and job.status not in (
                    Status.COMPLETED,
                    Status.ENCODING,
                    Status.VERIFYING,
                ):
                    job.status = (
                        Status.WAITING
                        if job.status == Status.SKIPPED
                        else Status.SKIPPED
                    )
                    job.message = (
                        "Restored to queue"
                        if job.status == Status.WAITING
                        else "Skipped by user"
                    )
                    job.progress = 0
                    self.model.update_job(job)
                    if job.status in FINISHED:
                        self.history.jobs[job.source] = job
            self.history_timer.start()
        self.update_totals()
        self.filter_library()
        if not self.busy:
            self.history.set_report(
                self.jobs, getattr(self, "scanned_roots", (None, None))[1]
            )
            self.save_history()
        self.refresh_controls()

    def samples(self):
        selected = self.selected_jobs()
        if len(selected) != 1 or not selected[0].media:
            self.error("Select one scanned video for the sample comparison")
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Choose folder for three comparison samples"
        )
        if not directory:
            return
        sample_root = Path(directory).resolve()
        source_root = getattr(
            self, "scanned_roots", (Path(self.source.text()).resolve(),)
        )[0]
        if sample_root == source_root or source_root in sample_root.parents:
            self.error("Choose a sample folder outside the source tree")
            return
        config = self.settings()
        if not self.caps:
            self.error("Run the hardware check first")
            return
        media = selected[0].media
        self.current.setText("Encoding three identical excerpts for visual comparison…")

        def operation(event):
            return benchmark(
                media, sample_root, config, self.caps, self.worker_runner(event)
            )

        def completed(results):
            report = json.dumps(results, indent=2, ensure_ascii=False)
            self.logs.appendPlainText(report)
            self.logs.show()
            text = "\n".join(
                f"{r['profile']}: "
                + (
                    r.get("error")
                    or f"{size_text(r['bytes'])} · {r['encode_seconds']} s encode · SSIM {r['ssim'] if r['ssim'] is not None else 'unavailable'}\n{r['path']}"
                )
                for r in results
            )
            QMessageBox.information(
                self,
                "Sample comparison",
                text
                + "\n\nSSIM compares against a scaled reference; higher is closer. Compare detail, motion and gradients visually too. Sample size is not a prediction of full-file savings.",
            )
            self.current.setText(
                "Sample comparison finished · Files retained in your chosen folder"
            )

        self.launch("samples", operation, completed)

    def toggle_log(self):
        self.logs.setVisible(not self.logs.isVisible())
        self.log_button.setText(
            "Hide detailed log" if self.logs.isVisible() else "Show detailed log"
        )

    def show_quality(self):
        # Restored jobs carry persisted reports, even if no new audit ran this batch.
        reports = {j.source: (j, j.quality) for j in self.model.jobs if j.quality}
        if not reports:
            QMessageBox.information(
                self,
                "Quality guard",
                "VMAF checks up to three eligible videos per batch, including retries of unavailable checks. Select existing completed files and use Recheck selected to analyze them without re-encoding.",
            )
            return
        from .quality_ui import QualityDialog

        dialog = getattr(self, "quality_dialog", None)
        if dialog is None:
            dialog = QualityDialog(self.model.jobs, self)
            self.quality_dialog = dialog

            def recheck(source):
                row = self.model.rows.get(source)
                if row is not None and not self.busy:
                    self.tabs.setCurrentIndex(1)
                    mapped = self.filters[1].mapFromSource(self.model.index(row, 0))
                    self.finished_table.selectRow(mapped.row())
                    self.recheck_quality()

            dialog.recheck_requested.connect(recheck)
        dialog.set_jobs(self.model.jobs, bool(self.busy))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def recheck_quality(self):
        selected = self.selected_jobs()
        if not selected:
            self.error("Select one or more queue rows to recheck their saved outputs")
            return
        try:
            roots = self.input_roots()
            if roots != getattr(
                self, "scanned_roots", None
            ) or self.input_signature() != getattr(self, "scanned_selection", ()):
                raise ValueError(
                    "Folders changed; scan again before rechecking quality"
                )
            settings = self.settings()
        except (OSError, ValueError) as error:
            self.error(str(error))
            return
        self.current.setText("Rechecking saved outputs · No video encoding is needed")
        selection = bool(self.drop_inputs)

        def operation(event):
            audit = Batch(
                self.jobs,
                *roots,
                settings,
                self.caps or Capabilities(settings.device),
                self.state_dir,
                event,
                selection=selection,
            )
            audit.cancel = self.cancel
            audit.runner = self.worker_runner(event)
            audit.audit_quality(selected)

        self.launch(
            "quality",
            operation,
            lambda _: self.current.setText(
                "Quality recheck finished · Open Quality details for results"
            ),
        )

    @Slot(str, object)
    def handle_event(self, kind, payload):
        if kind == "settings_applied":
            self.profile.setCurrentText(payload.profile)
            self.strict.setChecked(payload.strict)
            self.save_config()
            self.update_recommendation()
        elif kind == "log":
            self.logs.appendPlainText(str(payload))
        elif kind == "scan":
            count, name = payload
            self.queue_label.setText(f"Scanning · {count} videos found")
            self.current.setText(name)
        elif kind == "job":
            job = payload
            if job.status in FINISHED:
                self.history.jobs[job.source] = job
                self.history_timer.start()
            self.model.update_job(job)
            if job.status == Status.ENCODING:
                if self.active_job is not job:
                    self.last_fields = {}
                self.active_job = job
                self.current.setText(
                    f"{job.status.value} · {job.relative} · {job.message}"
                )
                self.file_progress.setRange(0, 1000)
            elif (
                job.status == Status.VERIFYING
                and job.message != "Queued for verification"
            ):
                self.active_verifier = job
                self.verification_status.setText(
                    f"CPU verifier · {job.relative} · {job.message}"
                )
                self.verify_progress.setRange(0, 0)
            elif self.active_verifier is job and job.status in (
                Status.COMPLETED,
                Status.FAILED,
                Status.CANCELLED,
            ):
                self.verification_status.setText(
                    f"CPU verifier · {job.status.value} · {job.relative}"
                )
                self.verify_progress.setRange(0, 1000)
                self.verify_progress.setValue(
                    1000 if job.status == Status.COMPLETED else 0
                )
                self.active_verifier = None
            if job.status == Status.VERIFYING and not any(
                j.status == Status.ENCODING for j in self.jobs
            ):
                self.current.setText("GPU encoder · File encoded; verification pending")
            self.filter_library()
            self.refresh_controls()
            self.update_totals()
        elif kind == "quality":
            job, report = payload["job"], payload["report"]
            job.quality = report
            self.quality_results[job.source] = report
            self.model.update_job(job)
            if report.get("status") == "Analyzing":
                self.quality_label.setText(
                    f"Quality guard · {job.relative} · Scene {report['scene']}/{report['scenes']}"
                )
            elif (
                report.get("geometry_note")
                or report.get("status") == "Geometry mismatch"
            ):
                self.quality_label.setText(
                    f"Quality guard · Frame size needs attention · {job.relative}"
                )
                self.quality_label.setStyleSheet("color: #f2bb79")
                self.quality_button.setText("Review picture framing")
            elif "mean" in report:
                review_count = sum(
                    r.get("status") == "Review suggested"
                    for r in self.quality_results.values()
                )
                self.quality_label.setText(
                    f"Quality guard · VMAF {report['mean']:.1f} · Worst scene {report['worst_scene']:.1f} · "
                    + (
                        f"Review {review_count} video(s)"
                        if review_count
                        else report["status"]
                    )
                )
                self.quality_label.setStyleSheet(
                    "color: #edabc6" if review_count else "color: #b1ddc6"
                )
                self.quality_button.setText(
                    f"Review quality ({review_count})"
                    if review_count
                    else "Quality details"
                )
            else:
                self.quality_label.setText(
                    f"Quality guard · {report.get('status', 'Pending')} · {job.relative}"
                )
            self.update_recommendation()
            dialog = getattr(self, "quality_dialog", None)
            if dialog and dialog.isVisible():
                dialog.set_jobs(self.model.jobs, bool(self.busy))
        elif kind == "verification":
            job, seconds = payload["job"], payload["seconds"]
            if self.strict.isChecked() and job.media.duration and seconds is not None:
                fraction = min(1, seconds / job.media.duration)
                self.verify_progress.setRange(0, 1000)
                self.verify_progress.setValue(int(fraction * 1000))
                self.verification_status.setText(
                    f"CPU verifier · {job.relative} · {fraction * 100:.1f}% decoded"
                )
        elif kind == "progress":
            self.active_job = payload["job"]
            self.last_fields = payload["fields"]
            self.file_progress.setRange(
                0, 1000 if self.active_job.media.duration else 0
            )
            self.file_progress.setValue(int(self.active_job.progress * 1000))
            percent = (
                f"{self.active_job.progress * 100:.1f}%"
                if self.active_job.media.duration
                else "duration unknown"
            )
            self.current.setText(f"Encoding · {self.active_job.relative} · {percent}")
            self.update_totals()
        elif kind == "paused":
            self.current.setText("Queue paused · Resume when ready")
        elif kind == "relocated":
            job = payload
            self.history.jobs[job.source] = job
            for row in self.jobs + self.history.report + self.model.jobs:
                if row.source == job.source:
                    row.relocated_to, row.relocation_error = (
                        job.relocated_to,
                        job.relocation_error,
                    )
                    if job.relocated_to:
                        row.status = job.status
            self.model.update_job(job)
            self.history_timer.start()
        elif kind == "relocation_progress":
            done, total = payload
            self.current.setText(
                f"Organizing originals · {done} / {total} files checked"
            )
        elif kind == "relocation_detail":
            name, phase, amount, total = payload
            percent = f"{min(100, 100 * amount / total):.0f}%" if total else "0%"
            message = f"{phase} · {name} · {percent}"
            self.current.setText(message)
            if self.report_dialog:
                self.report_dialog.activity.setText(message)
        elif kind == "finished":
            self.file_progress.setRange(0, 1000)
            self.file_progress.setValue(0)
            self.verify_progress.setRange(0, 1000)
            self.update_totals()

    def update_totals(self):
        counts = Counter(j.status for j in self.jobs)
        terminal = {Status.COMPLETED, Status.SKIPPED, Status.FAILED, Status.CANCELLED}
        done = sum(counts[s] for s in terminal)
        library_saved = sum(
            max(0, j.size - j.final_size)
            for j in self.model.jobs
            if j.status == Status.COMPLETED
        )
        self.saved.setText(size_text(library_saved) + " saved")
        self.queue_label.setText(f"Your library · {len(self.model.jobs)} videos")
        self.overall_label.setText(
            f"{done} / {len(self.jobs)} settled · {counts[Status.COMPLETED]} completed · {counts[Status.SKIPPED]} skipped · {counts[Status.FAILED]} failed · {counts[Status.VERIFYING]} verifying"
        )
        known = all(j.media and j.media.duration for j in self.jobs)
        if self.jobs and not known:
            self.overall_label.setText(
                self.overall_label.text() + " · Progress by file count"
            )
        total = (
            sum(j.media.duration for j in self.jobs)
            if known and self.jobs
            else len(self.jobs)
        )
        work = sum(
            (j.media.duration if known else 1)
            * (1 if j.status in terminal else j.progress)
            for j in self.jobs
        )
        self.overall.setValue(int(work / total * 1000) if total else 0)
        if self.busy == "batch" and self.started_at:
            elapsed = time.monotonic() - self.started_at
            # ETA follows measured encoding speed, excluding skipped/completed work from past runs.
            try:
                speed = float(self.last_fields.get("speed", "").rstrip("x"))
            except ValueError:
                speed = 0
            remaining = sum(
                (j.media.duration or 0) * (1 - j.progress)
                for j in self.jobs
                if j.media and j.status in (Status.WAITING, Status.ENCODING)
            )
            eta = (
                time_text(remaining / speed) + " + verification"
                if known and speed > 0
                else "—"
            )
            fps = self.last_fields.get("fps", "—")
            original = size_text(self.active_job.size) if self.active_job else "—"
            self.metrics.setText(
                f"Elapsed {time_text(elapsed)} · ETA {eta} · {fps} FPS · {self.last_fields.get('speed', '—')} · Original {original}"
            )

    def refresh_controls(self, *_):
        if not hasattr(self, "start_button"):
            return
        idle = not self.busy
        for widget in (
            self.source,
            self.output,
            self.profile,
            self.resolution,
            self.settings_card,
            self.scan_button,
            *self.folder_buttons,
        ):
            widget.setEnabled(idle)
        self.start_button.setEnabled(
            idle
            and any(
                j.status in (Status.WAITING, Status.CANCELLED) and not j.relocated_to
                for j in self.jobs
            )
            and bool(self.output.text().strip())
        )
        self.add_button.setEnabled(idle)
        self.remove_button.setEnabled(idle and bool(self.selected_jobs()))
        self.report_button.setEnabled(
            bool(self.history.report) or any(j.status in FINISHED for j in self.jobs)
        )
        self.result_filter.setEnabled(self.tabs.currentIndex() == 1)
        self.retry_button.setEnabled(
            idle
            and any(
                j.status == Status.FAILED and not j.relocated_to
                for j in self.model.jobs
            )
        )
        selected = self.selected_jobs()
        self.skip_button.setText(
            "Return to Jobs" if self.tabs.currentIndex() == 1 else "Skip selected"
        )
        self.skip_button.setEnabled(
            bool(selected)
            and (idle or self.busy == "batch")
            and any(
                not j.relocated_to and j.status != Status.COMPLETED for j in selected
            )
        )
        self.recheck_quality_button.setEnabled(
            idle
            and any(
                j.status == Status.COMPLETED and j.destination.is_file()
                for j in selected
            )
        )
        waiting = sum(
            j.status in (Status.WAITING, Status.CANCELLED) and not j.relocated_to
            for j in self.jobs
        )
        self.start_button.setText(
            f"Compress {waiting} video" + ("s" if waiting != 1 else "")
            if waiting
            else "Compress videos"
        )
        active = self.busy == "batch"
        self.file_progress.setVisible(active)
        self.metrics.setVisible(active)
        verifying = active and any(j.status == Status.VERIFYING for j in self.jobs)
        self.verification_status.setVisible(verifying)
        self.verify_progress.setVisible(verifying)
        self.overall.setVisible(bool(self.jobs))
        self.current.setVisible(not idle)
        self.overall_label.setVisible(not idle)
        self.overall.setVisible(active)
        self.pause_button.setEnabled(self.busy == "batch")
        self.stop_button.setEnabled(not idle)
        self.hardware_label.setText(self.capability_text())

    def closeEvent(self, event):
        if self.thread:
            self.closing = True
            self.stop()
            self.current.setText(
                "Stopping processes and cleaning temporary files before closing…"
            )
            event.ignore()
            return
        try:
            self.save_config()
            self.save_history()
        except (OSError, ValueError):
            pass
        logging.getLogger("petal").removeHandler(self.log_handler)
        self.log_handler.close()
        event.accept()


def run():
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("petal.png"))))
    app.setApplicationName("Petal")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Petal")
    app.setDesktopFileName("petal")
    app.setStyle("Fusion")
    app.setFont(QFont("Noto Sans", 10))
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    result = app.exec()
    # Dispose Qt-owned children while Python wrappers and the application still
    # exist. Frozen builds otherwise reach binding cleanup with live widgets.
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


def smoke():
    """Render the packaged GUI without requiring an encoder or a GPU."""

    class SmokeWindow(Window):
        def initialize_hardware(self):
            self.hardware_label.setText("Packaging check · Hardware check deferred")

    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("petal.png"))))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = SmokeWindow()
    window.show()

    def finished():
        if window.windowIcon().pixmap(128, 128).isNull():
            app.exit(1)
        else:
            window.close()

    QTimer.singleShot(300, finished)
    result = app.exec()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result
