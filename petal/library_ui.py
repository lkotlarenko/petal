"""Library presentation and explicit file actions, separate from media processing."""

from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from .history import FINISHED
from .models import Status
from .report_ui import ReportDialog


class LibraryTable(QTableView):
    def __init__(self, finished=False):
        super().__init__()
        self.finished = finished

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.model() and self.model().rowCount() == 0:
            searching = bool(self.model().query)
            message = (
                "No matching videos\nTry another filename or clear the search."
                if searching
                else "Your results will appear here\nCompleted, skipped and failed files stay together."
                if self.finished
                else "Ready for your next batch\nAdd videos or drop a folder here."
            )
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#b2afbd"))
            painter.drawText(
                self.viewport().rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter,
                message,
            )
            painter.end()


class LibraryFilter(QSortFilterProxyModel):
    def __init__(self, finished, parent=None):
        super().__init__(parent)
        self.finished = finished
        self.query = ""
        self.status = None
        self.setDynamicSortFilter(True)

    def filterAcceptsRow(self, row, parent):
        job = self.sourceModel().index(row, 0, parent).data(Qt.ItemDataRole.UserRole)
        if (job.status in FINISHED) != self.finished:
            return False
        if self.status and job.status != self.status:
            return False
        text = f"{job.relative} {job.status.value} {job.message}".casefold()
        return not self.query or self.query in text


class LibraryActions:
    def column_menu(self, table, point):
        header = table.horizontalHeader()
        menu = QMenu(self)
        for column, label in enumerate(self.model.columns):
            item = menu.addAction(label)
            item.setCheckable(True)
            item.setChecked(not table.isColumnHidden(column))
            item.setEnabled(column != 0)
            item.toggled.connect(
                lambda checked, c=column: table.setColumnHidden(c, not checked)
            )
        menu.exec(header.mapToGlobal(point))
        menu.deleteLater()

    def sync_library(self):
        merged = dict(self.history.jobs)
        merged.update({j.source: j for j in self.jobs})
        self.model.replace(list(merged.values()))
        self.filter_library()
        self.refresh_controls()

    def install_desktop(self):
        from .icons import install_desktop

        try:
            entry = install_desktop()
            self.statusBar().showMessage(f"Added to applications · {entry}", 8000)
        except (OSError, ValueError, RuntimeError) as error:
            self.error(str(error))

    def filter_library(self, *_):
        for proxy in self.filters:
            proxy.query = self.search.text().strip().casefold()
            proxy.status = self.result_filter.currentData() if proxy.finished else None
            proxy.invalidate()
        self.tabs.setTabText(0, f"Jobs  {self.filters[0].rowCount()}")
        self.tabs.setTabText(1, f"Finished  {self.filters[1].rowCount()}")

    def save_history(self):
        try:
            self.history.jobs.update({j.source: j for j in self.jobs})
            self.history.save()
        except (OSError, ValueError) as error:
            self.logs.appendPlainText(f"Could not save finished history: {error}")
            self.statusBar().showMessage(
                "History could not be saved. Check the detailed log."
            )

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos")
        if files:
            self.add_inputs([Path(p) for p in files])

    def remove_selected(self):
        if self.busy:
            return
        sources = {j.source for j in self.selected_jobs()}
        self.jobs = [j for j in self.jobs if j.source not in sources]
        for source in sources:
            self.history.jobs.pop(source, None)
        self.sync_library()
        self.save_history()
        self.update_totals()
        self.statusBar().showMessage(
            f"Removed {len(sources)} entries from the list. Files stay in place.", 5000
        )

    def queue_again(self, jobs):
        if self.busy:
            return
        jobs = [j for j in jobs if not j.relocated_to and j.source.is_file()]
        if not jobs:
            return
        current = {j.source: j for j in self.jobs}
        try:
            roots = self.input_roots()
            same_scope = all(
                j.output_root == roots[1]
                and (j.source.is_relative_to(roots[0]) or bool(self.drop_inputs))
                for j in jobs
            )
        except (OSError, ValueError):
            same_scope = False
        if same_scope:
            for job in jobs:
                if job.source not in current:
                    self.jobs.append(job)
                    current[job.source] = job
            self.scanned_roots = roots
            self.scanned_selection = self.input_signature()
        if any(j.source not in current for j in jobs):
            self.add_inputs([j.source for j in jobs])
        else:
            for job in jobs:
                job = current[job.source]
                job.status = Status.WAITING
                job.message = "Ready to try again"
                job.progress = 0
                self.history.jobs.pop(job.source, None)
            self.sync_library()
            self.save_history()
            self.update_totals()
        self.tabs.setCurrentIndex(0)

    def open_job(self, job, compressed=False, folder=False):
        path = job.destination if compressed else job.relocated_to or job.source
        if folder:
            path = path.parent
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def file_menu(self, table, point):
        index = table.indexAt(point)
        if not index.isValid():
            return
        if not table.selectionModel().isRowSelected(index.row(), index.parent()):
            table.selectRow(index.row())
        job = index.data(Qt.ItemDataRole.UserRole)
        selected = self.selected_jobs()
        menu = QMenu(self)
        original = job.relocated_to or job.source

        def action(label, callback, enabled=True):
            item = menu.addAction(label)
            item.setEnabled(enabled)
            item.triggered.connect(callback)

        action("Open original", lambda: self.open_job(job), original.is_file())
        action(
            "Open compressed",
            lambda: self.open_job(job, compressed=True),
            job.destination.is_file(),
        )
        action(
            "Show original folder",
            lambda: self.open_job(job, folder=True),
            original.parent.is_dir(),
        )
        action(
            "Copy file path", lambda: QApplication.clipboard().setText(str(original))
        )
        action("File details", lambda: self.file_details(job))
        menu.addSeparator()
        action(
            "Return selected to Jobs",
            lambda: self.queue_again(selected),
            not self.busy
            and any(
                j.status in FINISHED and not j.relocated_to and j.source.is_file()
                for j in selected
            ),
        )
        for category, status in [
            ("skipped", Status.SKIPPED),
            ("failed", Status.FAILED),
        ]:
            eligible = [
                j for j in selected if j.status == status and not j.relocated_to
            ]
            action(
                f"Move selected {category} originals…",
                lambda checked=False, c=category, rows=eligible: self.move_originals(
                    c, rows
                ),
                bool(eligible) and not self.busy,
            )
        action("Remove selected from list", self.remove_selected, not self.busy)
        menu.exec(table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def file_details(self, job):
        dialog = QDialog(self)
        dialog.setWindowTitle("Petal · File details")
        dialog.resize(680, 420)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            f"{job.relative}\n\nStatus: {job.status.value}\nResult: {job.message}\n\nOriginal: {job.source}\nCompressed: {job.destination}"
            + (f"\nMoved original: {job.relocated_to}" if job.relocated_to else "")
            + (f"\nMove issue: {job.relocation_error}" if job.relocation_error else "")
            + (
                f"\n\nSource codec: {job.media.video.get('codec_name', 'unknown')}\nPixel format: {job.media.video.get('pix_fmt', 'unknown')}\nFrame rate: {job.media.video.get('avg_frame_rate', 'unknown')}"
                if job.media
                else ""
            )
        )
        layout.addWidget(text)
        button = QPushButton("Done")
        button.clicked.connect(dialog.close)
        layout.addWidget(button)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def show_report(self):
        if not self.history.report and any(j.status in FINISHED for j in self.jobs):
            self.history.set_report(
                self.jobs, getattr(self, "scanned_roots", (None, None))[1]
            )
            self.save_history()
        if not self.history.report:
            return
        if self.report_dialog is None:
            self.report_dialog = ReportDialog(self)
            self.report_dialog.move_requested.connect(self.move_originals)
            self.report_dialog.view_requested.connect(self.show_results)
            self.report_dialog.cancel_requested.connect(self.stop)
        self.report_dialog.set_report(
            self.history.report, self.history.output, bool(self.busy)
        )
        self.report_dialog.show()
        self.report_dialog.raise_()
        self.report_dialog.activateWindow()

    def show_results(self, category):
        values = {
            "completed": Status.COMPLETED,
            "skipped": Status.SKIPPED,
            "failed": Status.FAILED,
        }
        self.tabs.setCurrentIndex(1)
        self.search.clear()
        self.result_filter.setCurrentIndex(
            self.result_filter.findData(values[category])
        )
        if self.report_dialog:
            self.report_dialog.hide()

    def move_originals(self, category, jobs=None):
        if self.busy:
            return
        status = {"skipped": Status.SKIPPED, "failed": Status.FAILED}.get(category)
        if status is None:
            return
        jobs = [
            j
            for j in (jobs if jobs is not None else self.history.report)
            if j.status == status and not j.relocated_to
        ]
        if not jobs:
            return
        roots = {j.output_root or self.history.output for j in jobs}
        if len(roots) != 1 or None in roots:
            self.error("Select originals from one output folder at a time.")
            return
        output = next(iter(roots))
        target = output / category
        answer = QMessageBox.question(
            self,
            "Move originals?",
            f"Move {len(jobs)} {category} original(s) into:\n{target}\n\nTheir relative folders and filenames will be preserved. Each copy is synced and fully verified before its original is removed. Existing destination files are never replaced.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        from .relocate import Relocator

        def operation(event):
            return Relocator(output, self.state_dir, self.cancel, event).move(
                jobs, category
            )

        def completed(results):
            moved = sum(success for _, success, _ in results)
            errors = sum(not success for _, success, _ in results)
            message = f"{moved} original(s) moved · {errors} need attention"
            if self.cancel.is_set():
                message = f"Stopped safely · {moved} moved · {len(jobs) - moved} originals kept"
            self.current.setText(message)
            if self.report_dialog:
                self.report_dialog.activity.setText(
                    message + " · Details are available in Finished."
                )
            self.save_history()

        self.launch("relocation", operation, completed)
        if self.report_dialog:
            self.report_dialog.set_report(
                self.history.report, self.history.output, True
            )
