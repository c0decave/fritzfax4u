"""Building a BoxClient from the settings, asking for password and certificate trust."""
from __future__ import annotations

import re

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..core import config
from ..core.errors import (
    BoxError,
    BoxNotConfigured,
    CertificateMismatch,
    ConnectionFailed,
    FaxBusy,
    FaxFailed,
    FaxNotConfigured,
    FaxResultUnknown,
    InvalidBoxAddress,
    LoginBlocked,
    LoginFailed,
    SessionExpired,
    TwoFactorFailed,
    UntrustedCertificate,
    UploadRejected,
    ValidationFailed,
)
from ..core.render import RenderError
from ..core.render.office import OfficeUnavailable
from ..core.session import BoxClient


def tr(text: str) -> str:
    return QCoreApplication.translate("Connection", text)


def make_client(settings: config.Settings, parent: QWidget | None, ask: bool = True,
                retry: bool = False) -> BoxClient | None:
    """A client for the configured box, or None (message shown when `ask`).

    retry: the box rejected the last password, so ask again instead of using the stored one."""
    if not settings.box_url.strip():
        if ask:
            QMessageBox.warning(parent, tr("Connection"), describe(BoxNotConfigured()))
        return None
    try:  # check the address before asking for a password
        BoxClient._normalize_url(settings.box_url)
    except BoxError as exc:
        if ask:
            QMessageBox.warning(parent, tr("Connection"), describe(exc))
        return None
    password = None if retry else config.get_password(settings)
    if password is None and ask:
        answer = ask_password(parent, settings.username)
        if answer is None:
            return None
        password, store = answer
        settings.password = password  # for this session
        settings.keyring_pending = store  # stored by store_pending_password() after a successful login
    if password is None:
        return None
    return BoxClient(settings.box_url, settings.username, password, cert_sha256=settings.cert_sha256 or None,
                     timeout=settings.timeout)


def store_pending_password(settings: config.Settings, parent: QWidget | None) -> None:
    """The box accepted the typed password: now keep it in the keyring if the user asked for that."""
    if not settings.keyring_pending or not settings.password:
        return
    settings.keyring_pending = False
    try:
        config.set_password(settings, settings.password)
    except Exception as exc:  # noqa: BLE001 - no or locked keyring: keep it for this session only
        QMessageBox.information(parent, tr("Password"), keyring_failed(exc))


def forget_rejected_password(settings: config.Settings) -> None:
    """The box rejected the password: never store it, and ask for a new one next time."""
    settings.password = None
    settings.keyring_pending = False


def describe_number_error(exc) -> str:
    """A translated message for numbers.InvalidNumber."""
    if exc.kind == "empty":
        return tr("Please enter at least one fax number.")
    if exc.kind == "too_long":
        return tr("The number %1 is longer than 40 characters.").replace("%1", exc.value)
    return tr("Invalid number: %1").replace("%1", exc.value)


def keyring_failed(exc: Exception) -> str:
    if isinstance(exc, config.NoKeyring):
        return tr("There is no system keyring. The password is used for this session only.")
    return tr("The password could not be stored in the system keyring (%1). It is used for this session "
              "only.").replace("%1", str(exc) or exc.__class__.__name__)


class PasswordDialog(QDialog):
    """Password prompt with the choice to keep the password in the keyring."""

    def __init__(self, parent: QWidget | None, username: str):
        super().__init__(parent)
        self.setWindowTitle(tr("Password"))
        layout = QVBoxLayout(self)
        self.label = QLabel(tr("Password for the FRITZ!Box user %1:").replace("%1", username or tr("(last user)")))
        layout.addWidget(self.label)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.edit)
        self.store_check = QCheckBox(tr("Store the password in the system keyring"))
        self.store_check.setChecked(True)
        layout.addWidget(self.store_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, bool]:
        return self.edit.text(), self.store_check.isChecked()


def ask_password(parent: QWidget | None, username: str) -> tuple[str, bool] | None:
    """(password, store in keyring) or None when cancelled."""
    dlg = PasswordDialog(parent, username)
    return dlg.values() if dlg.exec() == QDialog.DialogCode.Accepted else None


def ask_trust(parent: QWidget | None, exc: UntrustedCertificate) -> bool:
    answer = QMessageBox.question(
        parent, tr("Unknown certificate"),
        tr("The box presents a TLS certificate that is not trusted yet.\n\nSHA-256 fingerprint:\n%1\n\n"
           "Compare it with the certificate shown in the box's web interface. Trust it?").replace("%1", exc.fingerprint))
    return answer == QMessageBox.StandardButton.Yes


def describe(exc: Exception) -> str:
    """A translated, user-facing description of an error."""
    if isinstance(exc, BoxNotConfigured):
        return tr("No FRITZ!Box address is set. Enter the address of your box under Settings - Connection.")
    if isinstance(exc, InvalidBoxAddress):
        return tr("The box address \"%1\" is not valid. Use the address you open in the browser, for example "
                  "https://fritz.box.").replace("%1", exc.address)
    if isinstance(exc, UntrustedCertificate):
        return tr("The certificate of the box is not trusted, so fritzfax did not connect. Compare it with the "
                  "certificate in your browser and trust it under Settings - Connection.") + \
            f"\n\nSHA-256: {exc.fingerprint}"
    if isinstance(exc, LoginBlocked):
        return tr("The box blocks logins for %1 seconds after failed attempts.").replace("%1", str(exc.seconds))
    if isinstance(exc, LoginFailed):
        return tr("Login failed: wrong user name or password.")
    if isinstance(exc, ConnectionFailed):
        return tr("The box cannot be reached. Check the address in the settings.") + f"\n\n{exc}"
    if isinstance(exc, CertificateMismatch):
        return tr("The certificate of the box has changed. If that is expected, trust the new one in the "
                  "settings.") + f"\n\n{exc.actual}"
    if isinstance(exc, SessionExpired):
        return tr("The box ended the session.")
    if isinstance(exc, FaxNotConfigured):
        return tr("The fax function of the box is not set up (or the sender number is not a fax number).") + \
            f"\n\n{exc}"
    if isinstance(exc, FaxBusy):
        return tr("The box is already sending a fax. Try again when it has finished.")
    if isinstance(exc, TwoFactorFailed):
        return tr("The confirmation on the box failed:") + f" {exc}"
    if isinstance(exc, UploadRejected):
        return tr("The fax was not accepted:") + f" {exc.status_text}"
    if isinstance(exc, ValidationFailed):
        return tr("The box rejected an entry:") + f" {exc.alert or exc.field}"
    if isinstance(exc, FaxResultUnknown):
        return tr("The box gave no result within %1 seconds and may still be sending. Check the fax journal "
                  "before you send this fax again.").replace("%1", f"{exc.seconds:.0f}")
    if isinstance(exc, FaxFailed):
        if exc.reason:
            return tr("Transmission failed (error code 0x%1).").replace("%1", f"{exc.reason:04X}")
        return tr("Transmission failed:") + f" {exc}"
    if isinstance(exc, OfficeUnavailable):
        return tr("Office documents (Word, Excel, ...) need LibreOffice, which is not available here. "
                  "Please save the document as PDF and add the PDF, or install LibreOffice.") + f"\n({exc})"
    if isinstance(exc, RenderError):
        return tr("A file cannot be converted:") + f" {exc}"
    return str(exc) or exc.__class__.__name__


def _problems() -> list[tuple[re.Pattern, str]]:
    """The problems verify.collect() reports (English) and their translations; %1/%2 = captured parts."""
    return [
        (r"fax journal not readable before sending", tr("The fax journal of the box could not be read before "
                                                         "sending.")),
        (r"fax journal not readable: (.*)", tr("The fax journal of the box could not be read: %1")),
        (r"no journal entry for this fax", tr("The fax journal of the box has no entry for this fax.")),
        (r"journal reports an error: (.*)", tr("The fax journal of the box reports an error: %1")),
        (r"journal shows (\d+) page\(s\), (\d+) were sent", tr("The fax journal shows %1 page(s), %2 were sent.")),
        (r"archive copy of the box differs from the sent pages", tr("The copy kept by the box differs from the "
                                                                     "pages that were sent.")),
        (r"archive copy not available: (.*)", tr("The copy kept by the box is not available: %1")),
        (r"call list not readable \(HTTP (\d+)\)", tr("The call list of the box could not be read (HTTP %1).")),
        (r"no outgoing call to the destination in the call list", tr("The call list of the box shows no call to "
                                                                      "this number.")),
        (r"call list not readable: (.*)", tr("The call list of the box could not be read: %1")),
        (r"no final status from the box", tr("The box reported no final status for this fax.")),
    ]


def _match_problem(text: str):
    for pattern, translated in _problems():
        m = re.fullmatch(pattern, text)
        if m:
            return m, translated
    return None, None


def problem_known(text: str) -> bool:
    return _match_problem(text)[0] is not None


def describe_problem(text: str) -> str:
    """A verification problem in the UI language; unknown texts are shown as they are."""
    m, translated = _match_problem(text)
    if m is None:
        return text
    for i, part in enumerate(m.groups(), 1):
        translated = translated.replace(f"%{i}", part)
    return translated
