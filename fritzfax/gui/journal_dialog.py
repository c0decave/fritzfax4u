"""Fax journal of the box and the local send log."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import journal
from ..core.history import History
from . import connection
from .workers import run_async


_PRIVATE: list[Path] = []


def _private_dir() -> Path:
    """A temp directory only this user can read (mode 0700), one per run: faxes are personal."""
    if not _PRIVATE or not _PRIVATE[0].is_dir():
        _PRIVATE[:] = [Path(tempfile.mkdtemp(prefix="fritzfax-"))]
    return _PRIVATE[0]


class JournalDialog(QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.entries: list[journal.JournalEntry] = []
        self.setWindowTitle(self.tr("Fax journal of the box"))
        self.resize(820, 460)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([self.tr("Date"), self.tr("Direction"), self.tr("Number"),
                                              self.tr("Pages"), self.tr("Status")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)
        layout.addWidget(self.table)
        self.info = QLabel()
        layout.addWidget(self.info)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(self.tr("Refresh"))
        self.open_button = QPushButton(self.tr("Open"))
        self.save_button = QPushButton(self.tr("Save as..."))
        self.delete_button = QPushButton(self.tr("Delete"))
        close = QPushButton(self.tr("Close"))
        for b in (self.refresh_button, self.open_button, self.save_button, self.delete_button):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button.clicked.connect(self.open_selected)
        self.save_button.clicked.connect(self.save_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        close.clicked.connect(self.accept)
        self.refresh()

    def refresh(self):
        self.info.setText(self.tr("Loading ..."))
        run_async(lambda: journal.list_faxes(self.client), self._show, self._error)

    def _show(self, entries):
        self.entries = sorted(entries, key=lambda e: e.id, reverse=True)
        self.table.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            status = e.error or (self.tr("new") if e.unread else self.tr("OK"))
            values = [e.date, self.tr("sent") if e.sent else self.tr("received"), e.name or e.number,
                      str(e.pages), status]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, e.id)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.info.setText(self.tr("%1 fax(es)").replace("%1", str(len(self.entries))))

    def _error(self, exc):
        self.info.setText(connection.describe(exc))

    def selected(self) -> journal.JournalEntry | None:
        row = self.table.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _download(self, then):
        entry = self.selected()
        if entry is None:
            return
        self.info.setText(self.tr("Downloading ..."))
        run_async(lambda: (entry, journal.download(self.client, entry)), lambda res: then(*res), self._error)

    def open_selected(self, *_):
        def show(entry, pdf):
            path = _private_dir() / f"fax-{entry.id}.pdf"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(pdf)
            self.info.setText(str(path))
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            if entry.unread:
                run_async(lambda: journal.mark_seen(self.client, entry), lambda _: self.refresh())

        self._download(show)

    def save_selected(self):
        def save(entry, pdf):
            target, _ = QFileDialog.getSaveFileName(self, self.tr("Save fax"),
                                                    str(Path.home() / f"fax-{entry.id}.pdf"), "PDF (*.pdf)")
            if target:
                try:
                    Path(target).write_bytes(pdf)
                except OSError as exc:
                    self.info.setText(self.tr("%1 could not be saved: %2").replace("%1", target).replace(
                        "%2", exc.strerror or str(exc)))
                    return
                self.info.setText(self.tr("Saved: %1").replace("%1", target))

        self._download(save)

    def delete_selected(self):
        entry = self.selected()
        if entry is None:
            return
        if QMessageBox.question(self, self.tr("Delete"), self.tr("Delete this fax from the box?")) != \
                QMessageBox.StandardButton.Yes:
            return
        run_async(lambda: journal.delete(self.client, entry), lambda _: self.refresh(), self._error)


class HistoryDialog(QDialog):
    """Local send log: when, to whom, which content, result."""

    def __init__(self, history: History, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Send log"))
        self.resize(980, 480)
        layout = QVBoxLayout(self)
        self.entries = list(reversed(history.entries()))
        table = QTableWidget(len(self.entries), 8)
        table.setHorizontalHeaderLabels([self.tr("Time"), self.tr("Number"), self.tr("Sender"), self.tr("Content"),
                                         self.tr("Pages"), self.tr("Result"), self.tr("Verification"),
                                         self.tr("Error")])
        verdicts = {"verified": self.tr("verified"), "unverified": self.tr("NOT verified"),
                    "failed": self.tr("failed"), "": "-"}
        modes = {"document": self.tr("Document"), "letter": self.tr("Letter")}
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for r, e in enumerate(self.entries):
            files = ", ".join(f.name for f in e.files)
            what = " - ".join(x for x in (e.subject, files) if x) or modes.get(e.mode, e.mode)
            vals = [time.strftime("%d.%m.%Y %H:%M", time.localtime(e.timestamp)), e.destination,
                    e.sender_name or e.sender_number, what, str(e.pages),
                    self.tr("OK") if e.ok else self.tr("failed"), verdicts.get(e.verdict, e.verdict),
                    e.error or "; ".join(connection.describe_problem(p) for p in e.verification.get("problems", []))]
            for c, v in enumerate(vals):
                table.setItem(r, c, QTableWidgetItem(v))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        table.doubleClicked.connect(self.open_content)
        layout.addWidget(table)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)
        table.currentCellChanged.connect(lambda row, *_: self._show_detail(row))
        row = QHBoxLayout()
        self.open_button = QPushButton(self.tr("Open content"))
        self.open_button.clicked.connect(self.open_content)
        row.addWidget(self.open_button)
        self.report_button = QPushButton(self.tr("Open transmission report"))
        self.report_button.clicked.connect(self.open_report)
        row.addWidget(self.report_button)
        row.addStretch(1)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.table = table

    def selected(self):
        r = self.table.currentRow()
        return self.entries[r] if 0 <= r < len(self.entries) else None

    def _show_detail(self, row):
        if not 0 <= row < len(self.entries):
            self.detail.clear()
            return
        e = self.entries[row]
        lines = [self.tr("Files: %1").replace("%1", ", ".join(f"{f.name} (sha256 {f.sha256[:12]}...)"
                                                                for f in e.files) or "-")]
        if e.text:
            lines.append(self.tr("Text: %1").replace("%1", e.text[:300]))
        lines.append(self.tr("Content as sent: %1").replace("%1", e.content_pdf or "-"))
        self.detail.setText("\n".join(lines))

    def open_report(self, *_):
        e = self.selected()
        if e and e.report_pdf and Path(e.report_pdf).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(e.report_pdf))

    def open_content(self, *_):
        e = self.selected()
        if e and e.content_pdf and Path(e.content_pdf).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(e.content_pdf))
