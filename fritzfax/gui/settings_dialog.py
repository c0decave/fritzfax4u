"""Settings dialog: connection, sender, sending, documents, box fax settings, interface."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import boxconfig, config, faxapi
from ..core.errors import BoxError
from ..core.numbers import box_number_display
from ..core.session import BoxClient
from . import connection, theme
from .helptip import with_help
from .workers import run_async


class _TwoFactorBridge(QObject):
    challenge = Signal(object)


class SettingsDialog(QDialog):
    def __init__(self, settings: config.Settings, parent=None, config_path: Path | None = None):
        super().__init__(parent)
        self.settings = settings
        self.config_path = config_path
        self.box_settings: boxconfig.FaxSettings | None = None
        self.setWindowTitle(self.tr("Settings"))
        self.resize(620, 520)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._connection_tab(), self.tr("Connection"))
        self.tabs.addTab(self._sender_tab(), self.tr("Sender"))
        self.tabs.addTab(self._sending_tab(), self.tr("Sending"))
        self.tabs.addTab(self._documents_tab(), self.tr("Documents"))
        self.tabs.addTab(self._box_tab(), self.tr("Box fax settings"))
        self.tabs.addTab(self._ui_tab(), self.tr("Interface"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setObjectName("primary")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)
        theme.tidy(self)
        self.bridge = _TwoFactorBridge()
        self.bridge.challenge.connect(self._show_challenge)

    # --- tabs ------------------------------------------------------------------------------
    def _connection_tab(self):
        s = self.settings
        w = QWidget()
        f = QFormLayout(w)
        self.url_edit = QLineEdit(s.box_url)
        f.addRow(self.tr("Box address:"), with_help(self, "box_url", self.url_edit))
        self.user_edit = QLineEdit(s.username)
        self.user_edit.setPlaceholderText(self.tr("empty = user last logged in at the box"))
        f.addRow(self.tr("User name:"), with_help(self, "username", self.user_edit))
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(self.tr("unchanged"))
        f.addRow(self.tr("Password:"), self.password_edit)
        self.keyring_check = QCheckBox(self.tr("Store the password in the system keyring"))
        self.keyring_check.setChecked(True)
        f.addRow("", with_help(self, "keyring", self.keyring_check))
        self.cert_label = QLabel(s.cert_sha256 or self.tr("(not trusted yet)"))
        self.cert_label.setWordWrap(True)
        f.addRow(self.tr("Certificate (SHA-256):"), with_help(self, "certificate", self.cert_label))
        row = QHBoxLayout()
        self.cert_button = QPushButton(self.tr("Check certificate"))
        self.test_button = QPushButton(self.tr("Test connection"))
        row.addWidget(self.cert_button)
        row.addWidget(self.test_button)
        f.addRow("", row)
        self.test_label = QLabel()
        self.test_label.setWordWrap(True)
        f.addRow(self.test_label)
        self.timeout_spin = QDoubleSpinBox(minimum=3, maximum=120, value=s.timeout, suffix=" s")
        f.addRow(self.tr("Network timeout:"), with_help(self, "timeout", self.timeout_spin))
        self.cert_button.clicked.connect(self.check_certificate)
        self.test_button.clicked.connect(self.test_connection)
        return w

    def _sender_tab(self):
        s = self.settings
        w = QWidget()
        f = QFormLayout(w)
        self.sender_name_edit = QLineEdit(s.sender_name)
        self.sender_name_edit.setMaxLength(200)
        f.addRow(self.tr("Default sender name:"), with_help(self, "sender_name", self.sender_name_edit))
        self.sender_number_edit = QLineEdit(s.sender_number)
        self.sender_number_edit.setPlaceholderText(self.tr("empty = first fax number of the box"))
        f.addRow(self.tr("Default sender number:"), with_help(self, "sender_number", self.sender_number_edit))
        self.country_edit = QLineEdit(f"+{s.country_code}")  # written with + like everywhere
        self.country_edit.setMaxLength(6)
        f.addRow(self.tr("Own country code:"), with_help(self, "country_code", self.country_edit))
        note = QLabel(self.tr("The sender name is printed in the header line and on the cover sheet. The station "
                              "id that the remote fax machine displays is a box setting (tab 'Box fax settings')."))
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _sending_tab(self):
        s = self.settings
        w = QWidget()
        f = QFormLayout(w)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.tr("Document (the files are the fax)"), "document")
        self.mode_combo.addItem(self.tr("Letter (text, then files)"), "letter")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(s.default_mode)))
        f.addRow(self.tr("Default mode:"), with_help(self, "mode", self.mode_combo))
        self.cover_check = QCheckBox(self.tr("Add a cover sheet by default"))
        self.cover_check.setChecked(s.cover)
        f.addRow("", with_help(self, "cover", self.cover_check))
        self.header_combo = QComboBox()
        for key, label in (("overlay", self.tr("Header line on top of the page")),
                           ("shrink", self.tr("Header line, page shrunk to make room")),
                           ("off", self.tr("No header line"))):
            self.header_combo.addItem(label, key)
        self.header_combo.setCurrentIndex(max(0, self.header_combo.findData(s.header_mode)))
        f.addRow(self.tr("Header line:"), with_help(self, "header", self.header_combo))
        self.dither_combo = QComboBox()
        for key, label in (("auto", self.tr("Automatic")), ("threshold", self.tr("Text and line art (sharp)")),
                           ("dither", self.tr("Photos (dithering)"))):
            self.dither_combo.addItem(label, key)
        self.dither_combo.setCurrentIndex(max(0, self.dither_combo.findData(s.dither)))
        f.addRow(self.tr("Image conversion:"), with_help(self, "dither", self.dither_combo))
        self.max_pages_spin = QSpinBox(minimum=1, maximum=200, value=s.max_pages)
        f.addRow(self.tr("Maximum pages:"), with_help(self, "max_pages", self.max_pages_spin))
        self.start_spin = QSpinBox(minimum=10, maximum=600, value=int(s.start_timeout), suffix=" s")
        f.addRow(self.tr("Timeout until the box starts:"), with_help(self, "start_timeout", self.start_spin))
        self.stall_spin = QSpinBox(minimum=30, maximum=3600, value=int(s.stall_timeout), suffix=" s")
        f.addRow(self.tr("Timeout without progress:"), with_help(self, "stall_timeout", self.stall_spin))
        return w

    def _documents_tab(self):
        s = self.settings
        w = QWidget()
        f = QFormLayout(w)
        self.soffice_edit = QLineEdit(s.soffice)
        self.soffice_edit.setPlaceholderText(self.tr("automatic (soffice in PATH)"))
        browse = QPushButton(self.tr("Browse..."))
        browse.clicked.connect(lambda: self._browse(self.soffice_edit))
        row = QHBoxLayout()
        row.addWidget(self.soffice_edit, 1)
        row.addWidget(browse)
        f.addRow(self.tr("LibreOffice program:"), with_help(self, "soffice", row))
        self.font_edit = QLineEdit(s.font_file)
        self.font_edit.setPlaceholderText(self.tr("automatic (DejaVu Sans)"))
        browse2 = QPushButton(self.tr("Browse..."))
        browse2.clicked.connect(lambda: self._browse(self.font_edit, "Fonts (*.ttf *.otf)"))
        row2 = QHBoxLayout()
        row2.addWidget(self.font_edit, 1)
        row2.addWidget(browse2)
        f.addRow(self.tr("Font for letters:"), with_help(self, "font", row2))
        self.font_size_spin = QDoubleSpinBox(minimum=6, maximum=24, value=s.font_size, suffix=" pt")
        f.addRow(self.tr("Font size:"), self.font_size_spin)
        return w

    def _box_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        note = QLabel(self.tr("These settings are stored in the FRITZ!Box itself. Load them first; only the "
                              "fields you change are written back."))
        note.setWordWrap(True)
        v.addWidget(note)
        f = QFormLayout()
        self.box_identifier = QLineEdit()
        self.box_identifier.setMaxLength(20)
        f.addRow(self.tr("Station id (max. 20):"), with_help(self, "box_identifier", self.box_identifier))
        self.box_header = QLineEdit()
        self.box_header.setMaxLength(40)
        f.addRow(self.tr("Header line (1-40):"), with_help(self, "box_header", self.box_header))
        self.box_sender = QLineEdit()
        self.box_sender.setMaxLength(200)
        f.addRow(self.tr("Sender name:"), with_help(self, "box_sender", self.box_sender))
        self.box_email_check = QCheckBox(self.tr("Forward received faxes by e-mail"))
        f.addRow("", with_help(self, "box_email", self.box_email_check))
        self.box_email = QLineEdit()
        self.box_email.setPlaceholderText(self.tr("empty = address of the push service"))
        f.addRow(self.tr("E-mail address:"), self.box_email)
        self.box_store_check = QCheckBox(self.tr("Store received faxes in the internal memory"))
        f.addRow("", with_help(self, "box_store", self.box_store_check))
        self.box_send_only = QCheckBox(self.tr("Only send, do not receive faxes"))
        f.addRow("", with_help(self, "box_send_only", self.box_send_only))
        self.box_numbers = QLabel()
        f.addRow(self.tr("Fax numbers:"), self.box_numbers)
        v.addLayout(f)
        row = QHBoxLayout()
        self.box_load_button = QPushButton(self.tr("Load from box"))
        self.box_save_button = QPushButton(self.tr("Save to box"))
        self.box_save_button.setEnabled(False)
        row.addWidget(self.box_load_button)
        row.addWidget(self.box_save_button)
        row.addStretch(1)
        v.addLayout(row)
        self.box_status = QLabel()
        self.box_status.setWordWrap(True)
        v.addWidget(self.box_status)
        v.addStretch(1)
        self.box_load_button.clicked.connect(self.load_box_settings)
        self.box_save_button.clicked.connect(self.save_box_settings)
        return w

    def _ui_tab(self):
        s = self.settings
        w = QWidget()
        f = QFormLayout(w)
        self.language_combo = QComboBox()
        self.language_combo.addItem(self.tr("System language"), "auto")
        self.language_combo.addItem("Deutsch", "de")
        self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(s.language)))
        f.addRow(self.tr("Language:"), self.language_combo)
        f.addRow("", QLabel(self.tr("A new language is used after restarting fritzfax.")))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(self.tr("System"), "system")
        self.theme_combo.addItem(self.tr("Light"), "light")
        self.theme_combo.addItem(self.tr("Dark"), "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(s.theme)))
        f.addRow(self.tr("Theme:"), with_help(self, "theme", self.theme_combo))
        self.history_check = QCheckBox(self.tr("Keep a local send log with a copy of every fax"))
        self.history_check.setChecked(s.keep_history)
        f.addRow("", with_help(self, "history", self.history_check))
        self.animation_check = QCheckBox(self.tr("Animation with sound while a fax is sent"))
        self.animation_check.setChecked(s.animation)
        f.addRow("", with_help(self, "animation", self.animation_check))
        self.clear_recent = QPushButton(self.tr("Forget recent numbers"))
        self.clear_recent.setEnabled(bool(s.recent_numbers))
        self.forget_recent = False  # applied by save(); Cancel keeps the numbers
        self.clear_recent.clicked.connect(lambda: (setattr(self, "forget_recent", True),
                                                   self.clear_recent.setEnabled(False)))
        f.addRow("", self.clear_recent)
        return w

    def _browse(self, edit: QLineEdit, filt: str = ""):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Choose file"), edit.text() or "/usr/bin", filt)
        if path:
            edit.setText(path)

    # --- connection helpers ------------------------------------------------------------------------
    def _settings_from_widgets(self) -> config.Settings:
        s = replace(self.settings)
        s.password = self.password_edit.text() or self.settings.password
        s.box_url = self.url_edit.text().strip()
        s.username = self.user_edit.text().strip()
        cert = self.cert_label.text()
        s.cert_sha256 = cert if ":" in cert else ""
        s.timeout = self.timeout_spin.value()
        return s

    def _show_tab_of(self, widget) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).isAncestorOf(widget):
                self.tabs.setCurrentIndex(i)
                return

    def _country_code(self) -> str | None:
        """The typed country code without + ('+49', '49' -> '49'), '49' when empty, None if invalid."""
        text = re.sub(r"\s", "", self.country_edit.text())
        if not text:
            return "49"
        m = re.fullmatch(r"\+?([1-9]\d{0,2})", text)
        return m.group(1) if m else None

    def _client(self) -> BoxClient | None:
        s = self._settings_from_widgets()
        try:
            return connection.make_client(s, self)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, self.tr("Connection"), connection.describe(exc))
            return None

    def check_certificate(self):
        s = self._settings_from_widgets()
        try:
            probe = BoxClient(s.box_url, s.username, "", trust_unknown_cert=True, timeout=s.timeout)
        except Exception as exc:  # noqa: BLE001
            self.test_label.setText(connection.describe(exc))
            return

        def done(_):
            fp = probe.peer_fingerprint
            if not fp:
                self.test_label.setText(self.tr("The box is reached without TLS; there is no certificate."))
                return
            from ..core.errors import UntrustedCertificate

            if connection.ask_trust(self, UntrustedCertificate(fp)):
                self.cert_label.setText(fp)

        run_async(lambda: probe.raw_request("GET", "/login_sid.lua", params={"version": "2"}), done,
                  lambda exc: self.test_label.setText(connection.describe(exc)))

    def test_connection(self):
        client = self._client()
        if client is None:
            return
        self.test_label.setText(self.tr("Testing ..."))

        def work():
            client.login()
            info = faxapi.FaxApi(client).read_info()
            location = boxconfig.read_location(client)
            client.logout()
            return info, location

        def done(result):
            info, location = result
            if info.configured:
                self.test_label.setText(self.tr("Connection OK. Fax numbers of the box: %1").replace(
                    "%1", ", ".join(box_number_display(n, location) for n in info.src_numbers)))
            else:
                self.test_label.setText(self.tr("Connection OK, but the fax function of the box is not set up."))

        run_async(work, done, lambda exc: self.test_label.setText(connection.describe(exc)))

    # --- box settings ---------------------------------------------------------------------------------
    def load_box_settings(self):
        client = self._client()
        if client is None:
            return
        self.box_status.setText(self.tr("Loading ..."))
        run_async(lambda: boxconfig.read_settings(client), self._box_loaded,
                  lambda exc: self.box_status.setText(connection.describe(exc)))
        self._box_client = client

    def _box_loaded(self, st: boxconfig.FaxSettings):
        self.box_settings = st
        self.box_identifier.setText(st.identifier)
        self.box_header.setText(st.header)
        self.box_sender.setText(st.sender_name)
        self.box_email_check.setChecked(st.email_forward)
        self.box_email.setText(st.email_address)
        self.box_store_check.setChecked(st.store_internal)
        self.box_send_only.setChecked(st.send_only)
        self.box_numbers.setText(", ".join(st.numbers))
        self.box_save_button.setEnabled(True)
        self.box_status.setText(self.tr("Loaded from the box."))

    def box_changes(self) -> dict:
        st = self.box_settings
        if st is None:
            return {}
        changes = {}
        for key, widget in (("identifier", self.box_identifier), ("header", self.box_header),
                            ("sender_name", self.box_sender)):
            if widget.text() != getattr(st, key):
                changes[key] = widget.text()
        if self.box_email_check.isChecked() != st.email_forward:
            changes["email_forward"] = self.box_email_check.isChecked()
        if self.box_email.text().strip() != st.email_address and self.box_email_check.isChecked():
            changes["email_address"] = self.box_email.text().strip()
        if self.box_store_check.isChecked() != st.store_internal:
            changes["store_internal"] = self.box_store_check.isChecked()
        if self.box_send_only.isChecked() != st.send_only:
            changes["send_only"] = self.box_send_only.isChecked()
        return changes

    def save_box_settings(self):
        changes = self.box_changes()
        if not changes:
            self.box_status.setText(self.tr("Nothing changed."))
            return
        client = self._box_client
        self.box_status.setText(self.tr("Saving ..."))
        bridge = self.bridge

        def work():
            boxconfig.update_settings(client, **changes, on_two_factor=bridge.challenge.emit)
            return boxconfig.read_settings(client)

        def done(st):
            self._box_loaded(st)
            self.box_status.setText(self.tr("Saved in the box."))

        run_async(work, done, lambda exc: self.box_status.setText(connection.describe(exc)))

    def _show_challenge(self, challenge):
        text = self.tr("The box wants a confirmation: press any button on the FRITZ!Box")
        if challenge.dial:
            text += self.tr(" or dial %1 on a connected telephone").replace("%1", challenge.dial)
        self.box_status.setText(text + ".")

    # --- save -------------------------------------------------------------------------------------------
    def save(self):
        s = self.settings
        new = self._settings_from_widgets()
        account_changed = (new.box_url, new.username) != (s.box_url, s.username)
        if new.box_url:
            try:
                BoxClient._normalize_url(new.box_url)
            except BoxError as exc:
                self.tabs.setCurrentIndex(0)
                QMessageBox.warning(self, self.tr("Connection"), connection.describe(exc))
                self.url_edit.setFocus()
                return
        country = self._country_code()
        if country is None:
            self._show_tab_of(self.country_edit)
            QMessageBox.warning(self, self.tr("Country code"), self.tr(
                "\"%1\" is not a country code. Enter it with +, for example +49 for Germany or +43 for Austria.")
                .replace("%1", self.country_edit.text().strip()))
            self.country_edit.setFocus()
            return
        s.box_url, s.username, s.cert_sha256, s.timeout = new.box_url, new.username, new.cert_sha256, new.timeout
        s.sender_name = self.sender_name_edit.text().strip()
        s.sender_number = self.sender_number_edit.text().strip()
        s.country_code = country
        s.default_mode = self.mode_combo.currentData()
        s.cover = self.cover_check.isChecked()
        s.header_mode = self.header_combo.currentData()
        s.dither = self.dither_combo.currentData()
        s.max_pages = self.max_pages_spin.value()
        s.start_timeout = float(self.start_spin.value())
        s.stall_timeout = float(self.stall_spin.value())
        s.soffice = self.soffice_edit.text().strip()
        s.font_file = self.font_edit.text().strip()
        s.font_size = self.font_size_spin.value()
        s.language = self.language_combo.currentData()
        s.theme = self.theme_combo.currentData()
        theme.apply(QApplication.instance(), s.theme)
        s.keep_history = self.history_check.isChecked()
        s.animation = self.animation_check.isChecked()
        if self.forget_recent:
            s.recent_numbers.clear()
        if self.password_edit.text():
            s.password = self.password_edit.text()
            if self.keyring_check.isChecked():
                try:
                    config.set_password(s, s.password)
                except Exception as exc:  # noqa: BLE001 - no keyring, or the wallet stayed locked
                    QMessageBox.information(self, self.tr("Password"), connection.keyring_failed(exc))
            else:
                config.delete_password(s)  # "not in the keyring" means: not there any more either
        elif account_changed:
            s.password = None  # the password of the old box or user must not go to the new one
        try:
            config.save(s, self.config_path)
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Settings"), self.tr("The settings could not be saved: %1").replace(
                "%1", exc.strerror or str(exc)))
            return
        self.accept()
