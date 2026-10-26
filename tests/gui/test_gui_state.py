"""Main window state across settings changes, connects and sends (review round 2, 2026-10-25)."""
import os
import stat
from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from fritzfax.core import config
from fritzfax.gui import connection, i18n, workers
from fritzfax.gui.main_window import MainWindow
from tests_helpers import one_page_pdf


@pytest.fixture
def shown(monkeypatch, qtbot):
    seen = []

    def record(kind, answer=None):
        def f(parent, title, text, *a, **k):
            seen.append((kind, title, text))
            return answer
        return f

    monkeypatch.setattr(QMessageBox, "question", record("question", QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", record("information"))
    monkeypatch.setattr(QMessageBox, "warning", record("warning"))
    yield seen
    workers.wait_all(10000)


def window(qtbot, tmp_path, box=None, **kw):
    s = config.Settings(box_url=box.url if box else "http://127.0.0.1:9",
                        username=box.state.username if box else "", poll_interval=0.02, **kw)
    s.password = box.state.password if box else "x"
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    return w


def compose(qtbot, w, tmp_path, number="0301"):
    w.to_edit.setText(number)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None and w.thumbs.count() == 1, timeout=20000)


def save_settings_with(monkeypatch, w, change):
    """Run open_settings() as if the user changed something and pressed Save."""
    from fritzfax.gui import settings_dialog

    def exec_(dlg):
        change(dlg)
        dlg.save()
        return dlg.result()

    monkeypatch.setattr(settings_dialog.SettingsDialog, "exec", exec_)
    w.open_settings()


# --- settings saved while connecting / sending -----------------------------------------------------

def test_settings_saved_while_connecting_do_not_leave_a_half_connection(qtbot, tmp_path, fakebox, monkeypatch,
                                                                         shown):
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path)
    w.connect_box()
    save_settings_with(monkeypatch, w, lambda d: d.timeout_spin.setValue(30))  # a connection setting
    workers.wait_all(10000)
    QTest.qWait(100)
    assert w.box_info is None or w.client is not None  # never "connected" without a client
    w.send()
    qtbot.waitUntil(lambda: any(k in ("information", "warning") for k, _, _ in shown), timeout=30000)
    assert not any("NoneType" in t for _, _, t in shown), shown
    assert len(fakebox.state.runs) == 1


def test_settings_saved_during_a_send_keep_the_send_working(qtbot, tmp_path, fakebox, monkeypatch, shown):
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path, number="87654321")  # without area code: the box asks for 2FA
    fakebox.state.tfa_auto_confirm_after = None
    w.send()
    qtbot.waitUntil(lambda: w.twofa_dialog is not None, timeout=20000)
    used = w.twofa_dialog.client
    save_settings_with(monkeypatch, w, lambda d: d.timeout_spin.setValue(30))
    assert w.twofa_dialog is None or w.twofa_dialog.client is used
    assert used is not None
    w.events.cancel_event.set()
    qtbot.waitUntil(lambda: not w.sending, timeout=30000)


def test_two_factor_dialog_uses_the_client_of_the_running_send(qtbot, tmp_path, fakebox, shown):
    from fritzfax.core import twofactor

    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path, number="87654321")
    fakebox.state.tfa_auto_confirm_after = None
    w.send()
    qtbot.waitUntil(lambda: w.sending, timeout=20000)
    sending_client = w.send_client
    w.client = None  # what a settings change did
    w._on_two_factor("87654321", twofactor.Challenge({"button"}, "", totp_available=True))
    assert w.twofa_dialog.client is sending_client and sending_client is not None
    w.events.cancel_event.set()
    qtbot.waitUntil(lambda: not w.sending, timeout=30000)


def test_unchanged_connection_settings_keep_the_connection(qtbot, tmp_path, fakebox, monkeypatch, shown):
    w = window(qtbot, tmp_path, fakebox)
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    client = w.client
    save_settings_with(monkeypatch, w, lambda d: d.sender_name_edit.setText("Neu"))
    assert w.client is client and w.box_info is not None


def test_connect_during_a_send_does_not_log_in_again(qtbot, tmp_path, fakebox, shown):
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path, number="87654321")
    fakebox.state.tfa_auto_confirm_after = None
    w.send()
    qtbot.waitUntil(lambda: w.twofa_dialog is not None, timeout=20000)
    sid = w.send_client.sid
    w.connect_box()
    QTest.qWait(300)
    assert w.send_client.sid == sid
    w.events.cancel_event.set()
    qtbot.waitUntil(lambda: not w.sending, timeout=30000)


def test_journal_while_connecting_waits_for_the_connection(qtbot, tmp_path, fakebox, monkeypatch, shown):
    from fritzfax.gui import journal_dialog

    opened = []
    monkeypatch.setattr(journal_dialog.JournalDialog, "exec", lambda self: opened.append(1))
    w = window(qtbot, tmp_path, fakebox)
    w.connect_box()
    w.open_journal()
    assert opened == [] and shown and shown[-1][0] == "information"
    qtbot.waitUntil(lambda: not w.connecting, timeout=15000)


# --- settings file cannot be written ------------------------------------------------------------------------

def test_result_is_shown_even_if_the_settings_cannot_be_saved(qtbot, tmp_path, fakebox, monkeypatch, shown):
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path)

    def broken(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config, "save", broken)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" and "0301" in t for k, _, t in shown), timeout=30000)
    assert any(k == "warning" and "Permission denied" in t for k, _, t in shown)


def test_settings_dialog_stays_open_if_the_file_cannot_be_written(qtbot, tmp_path, monkeypatch, shown):
    from fritzfax.gui.settings_dialog import SettingsDialog

    def broken(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config, "save", broken)
    dlg = SettingsDialog(config.Settings(), None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.save()
    assert dlg.result() == 0 and shown[-1][0] == "warning" and "Permission denied" in shown[-1][2]


# --- passwords ---------------------------------------------------------------------------------------------------

class FakeKeyring:
    def __init__(self, stored=None):
        self.stored = dict(stored or {})

    def get_password(self, service, account):
        return self.stored.get(account)

    def set_password(self, service, account, password):
        self.stored[account] = password

    def delete_password(self, service, account):
        self.stored.pop(account, None)


def test_wrong_password_is_asked_again_and_not_stored(qtbot, tmp_path, fakebox, monkeypatch, shown):
    kr = FakeKeyring()
    monkeypatch.setattr(config, "_keyring", lambda: kr)
    answers = iter([("wrong", True), (fakebox.state.password, True)])
    asked = []
    monkeypatch.setattr(connection, "ask_password", lambda parent, user: asked.append(1) or next(answers))
    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username, poll_interval=0.02)
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    w.connect_box()
    qtbot.waitUntil(lambda: any(k == "warning" for k, _, _ in shown), timeout=15000)
    fakebox.state.block_time = 0  # the lockout of the box after a wrong password has run out
    assert kr.stored == {}  # a password the box rejected is never stored
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert len(asked) == 2
    assert list(kr.stored.values()) == [fakebox.state.password]  # stored once the box accepted it


def test_without_keyring_the_password_is_asked_once_and_not_on_the_next_start(qtbot, tmp_path, fakebox,
                                                                               monkeypatch, shown):
    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: None)
    asked = []
    monkeypatch.setattr(connection, "ask_password",
                        lambda parent, user: asked.append(1) or (fakebox.state.password, True))
    conf = tmp_path / "f.conf"
    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username, poll_interval=0.02)
    w = MainWindow(s, conf)
    qtbot.addWidget(w)
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert asked == [1]
    w2 = MainWindow(config.load(conf), conf)  # the next start
    qtbot.addWidget(w2)
    w2.connect_box()
    qtbot.waitUntil(lambda: w2.box_info is not None, timeout=15000)
    assert asked == [1]  # no second prompt


def test_rejected_keyring_password_leads_to_a_new_prompt(qtbot, tmp_path, fakebox, monkeypatch, shown):
    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username, poll_interval=0.02)
    kr = FakeKeyring({config._account(s): "old-and-wrong"})
    monkeypatch.setattr(config, "_keyring", lambda: kr)
    monkeypatch.setattr(connection, "ask_password", lambda parent, user: (fakebox.state.password, True))
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    w.connect_box()
    qtbot.waitUntil(lambda: any(k == "warning" for k, _, _ in shown), timeout=15000)
    fakebox.state.block_time = 0  # the lockout of the box after a wrong password has run out
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert kr.stored[config._account(s)] == fakebox.state.password


def test_new_box_or_user_without_new_password_drops_the_old_session_password(qtbot, tmp_path):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings(box_url="https://fritz.box", username="alice")
    s.password = "alices-password"
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.user_edit.setText("bob")
    dlg.save()
    assert s.password is None


def test_unticked_keyring_removes_a_stored_password(qtbot, tmp_path, monkeypatch):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings(box_url="https://fritz.box", username="alice")
    kr = FakeKeyring({config._account(s): "stored"})
    monkeypatch.setattr(config, "_keyring", lambda: kr)
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.password_edit.setText("new")
    dlg.keyring_check.setChecked(False)
    dlg.save()
    assert kr.stored == {} and s.password == "new"


# --- what the user composed survives a settings change ------------------------------------------------------

def test_edited_sender_name_is_kept_and_untouched_one_follows_the_settings(qtbot, tmp_path, monkeypatch, shown):
    w = window(qtbot, tmp_path, sender_name="Old Name")
    assert w.from_name_edit.text() == "Old Name"
    save_settings_with(monkeypatch, w, lambda d: d.sender_name_edit.setText("New Name"))
    assert w.from_name_edit.text() == "New Name"  # untouched field follows the new default
    w.from_name_edit.setText("Just for this fax")
    save_settings_with(monkeypatch, w, lambda d: d.sender_name_edit.setText("Third"))
    assert w.from_name_edit.text() == "Just for this fax"


def test_saving_settings_keeps_the_choices_for_this_fax(qtbot, tmp_path, monkeypatch, shown):
    w = window(qtbot, tmp_path)
    w.mode_letter.setChecked(True)
    w.cover_check.setChecked(True)
    w.header_combo.setCurrentIndex(w.header_combo.findData("off"))
    save_settings_with(monkeypatch, w, lambda d: d.sender_name_edit.setText("x"))
    assert w.mode_letter.isChecked() and w.cover_check.isChecked() and w.header_combo.currentData() == "off"


def test_changed_defaults_apply_where_the_user_kept_the_old_default(qtbot, tmp_path, monkeypatch, shown):
    w = window(qtbot, tmp_path)
    assert w.mode_document.isChecked()

    def change(d):
        d.mode_combo.setCurrentIndex(d.mode_combo.findData("letter"))
        d.cover_check.setChecked(True)

    save_settings_with(monkeypatch, w, change)
    assert w.mode_letter.isChecked() and w.cover_check.isChecked()


def test_cover_sheet_enables_the_subject_in_document_mode(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    assert w.mode_document.isChecked() and not w.subject_edit.isEnabled()
    w.cover_check.setChecked(True)
    assert w.subject_edit.isEnabled()
    w.cover_check.setChecked(False)
    assert not w.subject_edit.isEnabled()


# --- sender number ---------------------------------------------------------------------------------------------

def test_sender_number_the_box_does_not_have_is_pointed_out(qtbot, tmp_path, fakebox, shown):
    w = window(qtbot, tmp_path, fakebox, sender_number="03087654321")
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert "03087654321" in w.from_hint.text() and w.sender_number() in w.from_hint.text()


def test_sender_number_the_box_has_needs_no_hint(qtbot, tmp_path, fakebox, shown):
    w = window(qtbot, tmp_path, fakebox, sender_number="12345678")
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert w.from_hint.text() == ""


# --- number hints and errors -------------------------------------------------------------------------------------

def test_international_number_gets_a_hint(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.to_edit.setText("+43 1 234567")
    assert "00431234567" in w.to_hint.text() and "international" in w.to_hint.text().lower()


def test_number_errors_are_translated(qtbot, tmp_path, shown):
    app = QApplication.instance()
    try:
        i18n.install(app, "de")
        w = window(qtbot, tmp_path)
        w.to_edit.setText("1" * 41)
        assert "40" in w.to_hint.text() and "länger" in w.to_hint.text()
        w.to_edit.setText("abc")
        assert w.to_hint.text() == "Ungültige Nummer: abc"
        w.to_edit.setText(" , ")
        w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
        qtbot.waitUntil(lambda: w.prepared is not None, timeout=20000)
        w.send()
        assert shown[-1][2] == "Bitte mindestens eine Faxnummer eingeben."
    finally:
        i18n.install(app, "en")


# --- export ---------------------------------------------------------------------------------------------------

def test_exported_pdf_has_the_size_of_an_a4_fax_page(qtbot, tmp_path, monkeypatch, shown):
    import re

    w = window(qtbot, tmp_path)
    compose(qtbot, w, tmp_path)
    target = tmp_path / "fax.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    w.export_preview()
    box = re.search(rb"/MediaBox \[ ?0 0 ([\d.]+) ([\d.]+) ?\]", target.read_bytes())
    width_mm, height_mm = (float(v) / 72 * 25.4 for v in box.groups())
    assert abs(width_mm - 215) < 1 and abs(height_mm - 297) < 1, (width_mm, height_mm)


# --- 2FA at upload ---------------------------------------------------------------------------------------------

def test_two_factor_dialog_closes_when_the_box_asked_only_at_upload(qtbot, tmp_path, fakebox, monkeypatch, shown):
    from fakebox import Handler

    from fritzfax.core import job

    monkeypatch.setattr(Handler, "_needs_2fa", staticmethod(lambda d: d == "030999"))
    original = job.faxapi.FaxApi.precheck
    monkeypatch.setattr(job.faxapi.FaxApi, "precheck",
                        lambda self, d, s: False if d == "030999" else original(self, d, s))
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path, number="030999")
    seen = []
    original_phase = w._on_phase

    def phase(dest, ph, detail):
        original_phase(dest, ph, detail)
        if ph == "sending":
            seen.append(w.twofa_dialog)

    monkeypatch.setattr(w, "_on_phase", phase)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in shown), timeout=30000)
    assert seen and all(d is None for d in seen)


# --- reconnect after the connection settings changed (operator 2026-10-26) ---------------------------------------

def test_changed_connection_settings_reconnect(qtbot, tmp_path, fakebox, monkeypatch, shown):
    w = window(qtbot, tmp_path, fakebox)
    calls = []
    monkeypatch.setattr(w, "auto_connect", lambda: calls.append(True))
    save_settings_with(monkeypatch, w, lambda d: d.timeout_spin.setValue(31))
    assert calls == [True]
    save_settings_with(monkeypatch, w, lambda d: d.sender_name_edit.setText("Other"))  # not a connection setting
    assert calls == [True]


def test_send_clicked_while_connecting_is_sent_once_connected(qtbot, tmp_path, fakebox, shown):
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path)
    w.auto_connect()
    assert w.connecting
    w.send()
    w.send()  # a second click while connecting must not send twice
    assert "once connected" in w.status_label.text()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in shown), timeout=30000)
    assert len(fakebox.state.runs) == 1


def test_second_click_while_the_send_connects_does_not_send_twice(qtbot, tmp_path, fakebox, shown):
    w = window(qtbot, tmp_path, fakebox)
    compose(qtbot, w, tmp_path)
    w.send()  # not connected: connects first, then sends
    assert w.connecting
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in shown), timeout=30000)
    QTest.qWait(300)
    assert len(fakebox.state.runs) == 1 and not w.send_after_connect


def test_send_clicked_while_connecting_is_dropped_when_the_connection_fails(qtbot, tmp_path, shown):
    w = window(qtbot, tmp_path)  # nobody listens
    compose(qtbot, w, tmp_path)
    w.auto_connect()
    w.send()
    qtbot.waitUntil(lambda: any(k == "warning" for k, _, _ in shown), timeout=30000)
    QTest.qWait(200)
    assert [k for k, _, _ in shown] == ["warning"] and not w.sending and not w.send_after_connect


def test_no_reconnect_while_sending(qtbot, tmp_path, fakebox, monkeypatch, shown):
    w = window(qtbot, tmp_path, fakebox)
    calls = []
    monkeypatch.setattr(w, "auto_connect", lambda: calls.append(True))
    w.sending = True
    try:
        save_settings_with(monkeypatch, w, lambda d: d.timeout_spin.setValue(31))
    finally:
        w.sending = False
    assert calls == [] and w.reset_client_after_send


# --- the box's own result after the time budget (live 2026-10-26) -----------------------------------------------

def test_long_call_is_shown_as_waiting_for_the_box(qtbot, tmp_path, fakebox, monkeypatch, shown):
    from fritzfax.core import faxapi

    w = window(qtbot, tmp_path, fakebox)
    seen = []
    original_status = w._on_status
    monkeypatch.setattr(w, "_on_status", lambda d, st: (original_status(d, st), seen.append(w.status_label.text())))
    w._on_phase("0301", "sending", "")
    w._on_status("0301", faxapi.FaxStatus(4, "FAXSEND_CONNECTED", 100, 0))
    assert "transmitting" in w.status_label.text()
    w._on_phase("0301", "overdue", "")
    w._on_status("0301", faxapi.FaxStatus(4, "FAXSEND_CONNECTED", 100, 0))
    assert "still in the call" in w.status_label.text()  # the next poll does not hide the notice
    w._on_phase("030222", "sending", "")
    w._on_status("030222", faxapi.FaxStatus(4, "FAXSEND_CONNECTED", 100, 0))
    assert "transmitting" in w.status_label.text()  # the notice belongs to one destination


def test_unknown_result_is_not_reported_as_failed(qtbot, tmp_path, fakebox, monkeypatch, shown):
    from fritzfax.core import faxapi, job

    monkeypatch.setattr(job, "SECONDS_PER_PAGE", 0)
    monkeypatch.setattr(faxapi, "OVERDUE_GRACE", 0.3)
    fakebox.state.scenario = "stall"
    w = window(qtbot, tmp_path, fakebox, stall_timeout=0.05)
    compose(qtbot, w, tmp_path)
    w.send()
    qtbot.waitUntil(lambda: any(k == "warning" and "0301" in t for k, _, t in shown), timeout=30000)
    (text,) = [t for k, _, t in shown if k == "warning" and "0301" in t]
    assert "0301: result UNKNOWN" in text and "fax journal" in text and "FAILED" not in text


def test_unknown_result_text_is_translated(qtbot):
    from fritzfax.core.errors import FaxResultUnknown

    app = QApplication.instance()
    try:
        i18n.install(app, "de")
        text = connection.describe(FaxResultUnknown(1080))
        assert "1080" in text and "Fax-Journal" in text, text
    finally:
        i18n.install(app, "en")


# --- journal: temporary copies are private ----------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes / shell scripts")
def test_opened_journal_fax_is_not_readable_by_other_users(qtbot, tmp_path, fakebox, client_for, monkeypatch, shown):
    from PySide6.QtGui import QDesktopServices

    from fritzfax.gui.journal_dialog import JournalDialog

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())))
    fakebox.state.journal.append({"number": "0301", "msn": "1", "sent": 1, "date": "x", "date_time": 5,
                                  "pages": 1, "unread": 0, "name": ""})
    dlg = JournalDialog(client_for(fakebox))
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.table.rowCount() == 1, timeout=15000)
    dlg.table.selectRow(0)
    dlg.open_selected()
    qtbot.waitUntil(lambda: bool(opened), timeout=15000)
    path = opened[0]
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0


# --- broken settings file -------------------------------------------------------------------------------------

def test_broken_settings_file_is_reported_in_the_window(qtbot, tmp_path, shown):
    conf = tmp_path / "f.conf"
    conf.write_text("[sender]\nname = A\nname = B\n")
    s = config.load(conf)
    w = MainWindow(s, conf)
    qtbot.addWidget(w)
    w.report_load_problem()
    kind, _title, text = shown[-1]
    assert kind == "warning" and str(tmp_path / "f.conf.broken") in text


def test_good_settings_file_shows_nothing(qtbot, tmp_path, shown):
    w = window(qtbot, tmp_path)
    w.report_load_problem()
    assert shown == []
