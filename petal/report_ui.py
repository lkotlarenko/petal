"""A calm completion summary with explicit, opt-in original-file organization."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .history import summary
from .models import Status


def size_text(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


class ReportDialog(QDialog):
    move_requested = Signal(str)
    view_requested = Signal(str)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Petal · Batch report")
        self.resize(720, 570)
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 26)
        root.setSpacing(18)
        eyebrow = QLabel("YOUR BATCH, AT A GLANCE")
        eyebrow.setObjectName("muted")
        root.addWidget(eyebrow)
        self.heading = QLabel("A little lighter.")
        self.heading.setStyleSheet("font-size:30px;font-weight:600;")
        root.addWidget(self.heading)
        self.saved = QLabel()
        self.saved.setStyleSheet("font-size:52px;font-weight:650;color:#b1ddc6;")
        root.addWidget(self.saved)
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)
        row = QHBoxLayout()
        self.counts = {}
        for key, title, color in [
            ("completed", "Compressed", "#b1ddc6"),
            ("skipped", "Skipped", "#f2c083"),
            ("failed", "Needs attention", "#efa1aa"),
        ]:
            card = QFrame()
            card.setObjectName("card")
            box = QVBoxLayout(card)
            number = QLabel("0")
            number.setStyleSheet(f"font-size:32px;font-weight:600;color:{color}")
            box.addWidget(number)
            button = QPushButton(title)
            button.clicked.connect(
                lambda checked=False, k=key: self.view_requested.emit(k)
            )
            box.addWidget(button)
            self.counts[key] = number
            row.addWidget(card)
        root.addLayout(row)
        self.output_label = QLabel()
        self.output_label.setTextFormat(Qt.TextFormat.PlainText)
        self.output_label.setWordWrap(True)
        self.output_label.setObjectName("muted")
        root.addWidget(self.output_label)
        organize = QLabel("Bring the rest together")
        organize.setStyleSheet("font-size:17px;font-weight:600")
        root.addWidget(organize)
        hint = QLabel(
            "Move skipped or failed originals into their own folders in the output. Folder paths and filenames are preserved. Every byte is verified before a source is removed."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        root.addWidget(hint)
        actions = QHBoxLayout()
        self.move_buttons = {}
        for category, label in [
            ("skipped", "Move skipped originals"),
            ("failed", "Move failed originals"),
        ]:
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, c=category: self.move_requested.emit(c)
            )
            actions.addWidget(button)
            self.move_buttons[category] = button
        root.addLayout(actions)
        self.activity = QLabel()
        self.activity.setTextFormat(Qt.TextFormat.PlainText)
        self.activity.setWordWrap(True)
        root.addWidget(self.activity)
        footer = QHBoxLayout()
        self.cancel_button = QPushButton("Stop moving safely")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.hide)
        footer.addWidget(done)
        root.addLayout(footer)

    def set_report(self, jobs, output, busy=False):
        self.cancel_button.setVisible(
            busy and getattr(self.parent(), "busy", "") == "relocation"
        )
        values = summary(jobs)
        unfinished = sum(
            j.status not in (Status.COMPLETED, Status.SKIPPED, Status.FAILED)
            for j in jobs
        )
        self.heading.setText(
            "A little lighter."
            if not values["cancelled"]
            else "Your batch, safely stopped."
        )
        if not values["completed"] and not values["cancelled"]:
            self.heading.setText("Your batch is ready to review.")
        self.saved.setText(size_text(values["saved"]) + " saved")
        self.detail.setText(
            f"{values['percent']:.1f}% smaller across {values['completed']} verified compressed file"
            + ("s" if values["completed"] != 1 else "")
            + ". "
            + (
                f"{unfinished} unfinished file"
                + ("s remain" if unfinished != 1 else " remains")
                + " in Jobs."
                if unfinished
                else "Compressed originals stay in place."
            )
        )
        for key, label in self.counts.items():
            label.setText(str(values[key]))
        self.output_label.setText(
            f"Output · {output}"
            if output
            else "Choose an output folder to organize originals."
        )
        for category, status in [
            ("skipped", Status.SKIPPED),
            ("failed", Status.FAILED),
        ]:
            eligible = [j for j in jobs if j.status == status and not j.relocated_to]
            self.move_buttons[category].setEnabled(
                bool(output) and bool(eligible) and not busy
            )
            self.move_buttons[category].setText(
                f"Move {len(eligible)} {category} original"
                + ("s" if len(eligible) != 1 else "")
            )
