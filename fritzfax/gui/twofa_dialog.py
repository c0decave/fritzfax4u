"""Dialog shown while the box waits for a second-factor confirmation."""
from __future__ import annotations

import threading

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ..core import twofactor
from . import connection
from .workers import run_async


class TwoFactorDialog(QDialog):
    def __init__(self, client, dest: str, challenge: twofactor.Challenge, cancel_event: threading.Event, parent=None):
        super().__init__(parent)
        self.client = client
        self.cancel_event = cancel_event
        self.setWindowTitle(self.tr("Confirmation needed"))
        self.setModal(False)
        layout = QVBoxLayout(self)
        intro = QLabel(self.tr("The FRITZ!Box wants you to confirm the fax to %1.").replace("%1", dest))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        steps = []
        if "button" in challenge.methods:
            steps.append(self.tr("Press any button on the FRITZ!Box."))
        if challenge.dial:
            steps.append(self.tr("Or dial %1 on a telephone connected to the box.").replace("%1", challenge.dial))
        self.steps_label = QLabel("\n".join(f"• {s}" for s in steps))
        self.steps_label.setWordWrap(True)
        layout.addWidget(self.steps_label)
        self.code_edit = QLineEdit()
        self.code_button = QPushButton(self.tr("Confirm code"))
        self.totp_row = QHBoxLayout()
        self.totp_row.addWidget(QLabel(self.tr("Authenticator code:")))
        self.totp_row.addWidget(self.code_edit)
        self.totp_row.addWidget(self.code_button)
        if challenge.totp_available:
            layout.addLayout(self.totp_row)
        else:
            for w in (self.code_edit, self.code_button):
                w.hide()
        self.info = QLabel(self.tr("Waiting for the confirmation ..."))
        layout.addWidget(self.info)
        self.cancel_button = QPushButton(self.tr("Cancel fax"))
        layout.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self._cancel)
        self.code_button.clicked.connect(self._submit)

    def _cancel(self):
        self.cancel_event.set()
        self.reject()

    def _submit(self):
        code = self.code_edit.text().strip()
        if len(code) != 6 or not code.isdigit():
            self.info.setText(self.tr("The code has 6 digits."))
            return
        self.code_button.setEnabled(False)

        def done(ok):
            self.code_button.setEnabled(True)
            self.info.setText(self.tr("Code accepted.") if ok else self.tr("Code rejected, try again."))

        def failed(exc):
            self.code_button.setEnabled(True)
            self.info.setText(connection.describe(exc))

        run_async(lambda: twofactor.submit_totp(self.client, code), done, failed)
