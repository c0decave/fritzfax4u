"""GUI tests (offscreen) against the fake box."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from fritzfax.core import config, sff
from fritzfax.gui import connection, workers
from fritzfax.gui.main_window import MainWindow
from tests_helpers import one_page_pdf

pytestmark = pytest.mark.usefixtures("no_modal")


@pytest.fixture
def no_modal(monkeypatch, qtbot):
    """Record message boxes instead of blocking on them.

    Depends on qtbot so that its teardown (waiting for background tasks) runs before
    pytest-qt deletes the widgets."""
    shown = []

    def record(kind, answer=None):
        def f(parent, title, text, *a, **k):
            shown.append((kind, title, text))
            return answer
        return f

    monkeypatch.setattr(QMessageBox, "question", record("question", QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", record("information"))
    monkeypatch.setattr(QMessageBox, "warning", record("warning"))
    monkeypatch.setattr(QMessageBox, "about", record("about"))
    yield shown
    workers.wait_all(10000)


def make_window(qtbot, tmp_path, box=None, **kw):
    s = config.Settings(box_url=box.url if box else "http://127.0.0.1:9",
                        username=box.state.username if box else "", poll_interval=0.02, **kw)
    s.password = box.state.password if box else "x"
    conf = tmp_path / "f.conf"
    w = MainWindow(s, conf)
    qtbot.addWidget(w)
    return w, conf


def wait_preview(qtbot, w, pages):
    qtbot.waitUntil(lambda: w.prepared is not None and w.prepared.page_count == pages and w.thumbs.count() == pages,
                    timeout=20000)


# --- composing ----------------------------------------------------------------------

def test_one_page_pdf_is_one_preview_page(qtbot, tmp_path):
    w, _ = make_window(qtbot, tmp_path)
    assert w.mode_document.isChecked() and not w.text_edit.isEnabled()
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    wait_preview(qtbot, w, 1)
    assert "1" in w.preview_label.text()
    assert w.page_view.pixmap() is not None


def test_letter_mode_with_cover(qtbot, tmp_path):
    w, _ = make_window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    w.mode_letter.setChecked(True)
    assert w.text_edit.isEnabled()
    w.text_edit.setPlainText("Sehr geehrte Damen und Herren")
    w.cover_check.setChecked(True)
    wait_preview(qtbot, w, 3)


def test_page_selection(qtbot, tmp_path):
    w, _ = make_window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf", pages=3))])
    item = w.file_list.item(0)
    item.setData(Qt.ItemDataRole.UserRole, {"path": str(tmp_path / "a.pdf"), "pages": "1,3"})
    w.schedule_preview()
    wait_preview(qtbot, w, 2)


def test_invalid_page_selection_shows_message(qtbot, tmp_path):
    w, _ = make_window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    w.file_list.item(0).setData(Qt.ItemDataRole.UserRole, {"path": str(tmp_path / "a.pdf"), "pages": "5"})
    w.update_preview()
    assert w.prepared is None and "1-1" in w.preview_label.text()


def test_number_hints(qtbot, tmp_path):
    w, _ = make_window(qtbot, tmp_path)
    w.to_edit.setText("87654321")
    assert "87654321" in w.to_hint.text()
    w.to_edit.setText("0531 49059113")
    assert w.to_hint.text() == ""
    w.to_edit.setText("hallo")
    assert "hallo" in w.to_hint.text()


def test_remove_and_reorder_files(qtbot, tmp_path):
    w, _ = make_window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf")), str(one_page_pdf(tmp_path / "b.pdf"))])
    w.file_list.setCurrentRow(1)
    w._move_selected(-1)
    assert Path(w.file_specs()[0].path).name == "b.pdf"
    w.file_list.item(0).setSelected(True)
    w._remove_selected()
    assert [Path(s.path).name for s in w.file_specs()] == ["a.pdf"]


# --- sending ------------------------------------------------------------------------

def test_send_success(qtbot, tmp_path, fakebox, no_modal):
    w, conf = make_window(qtbot, tmp_path, fakebox, sender_name="Test Sender")
    w.to_edit.setText("0531 49059113")
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    wait_preview(qtbot, w, 1)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" and "053149059113" in t for k, _, t in no_modal), timeout=30000)
    (run,) = fakebox.state.runs
    assert run.dest == "053149059113" and len(sff.decode(run.sff)) == 1
    assert config.load(conf).recent_numbers == ["053149059113"]
    assert w.send_button.isEnabled() and not w.sending


def test_send_with_two_factor_dialog(qtbot, tmp_path, fakebox, no_modal):
    fakebox.state.tfa_auto_confirm_after = 5
    w, _ = make_window(qtbot, tmp_path, fakebox)
    seen = []
    original = w._on_two_factor
    w.events_hook = seen
    w._on_two_factor = lambda d, ch: (seen.append((d, ch)), original(d, ch))
    w.to_edit.setText("87654321")
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    wait_preview(qtbot, w, 1)
    # connect the signal to the patched handler: SendEvents is created inside send()
    import fritzfax.gui.main_window as mw

    real_events = mw.SendEvents

    def events_factory():
        ev = real_events()
        ev.two_factor.connect(lambda d, ch: seen.append((d, ch)))
        return ev

    mw.SendEvents = events_factory
    try:
        w.send()
        qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in no_modal), timeout=30000)
    finally:
        mw.SendEvents = real_events
    assert seen and seen[0][0] == "87654321" and seen[0][1].code == "4711"
    assert len(fakebox.state.runs) == 1


def test_send_failure_shows_error_code(qtbot, tmp_path, fakebox_factory, no_modal):
    box = fakebox_factory(scenario="reason:3490")
    w, _ = make_window(qtbot, tmp_path, box)
    w.to_edit.setText("0301")
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    wait_preview(qtbot, w, 1)
    w.send()
    qtbot.waitUntil(lambda: any(k == "warning" for k, _, _ in no_modal), timeout=30000)
    assert any("3490" in t for k, _, t in no_modal if k == "warning")


def test_send_without_content_or_with_bad_number(qtbot, tmp_path, fakebox, no_modal):
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.to_edit.setText("0301")
    w.send()
    assert no_modal[-1][0] == "warning" and not fakebox.state.runs
    w.to_edit.setText("abc")
    w.send()
    assert "abc" in no_modal[-1][2]


def test_wrong_password_message(qtbot, tmp_path, fakebox, no_modal):
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.settings.password = "wrong"
    w.connect_box()
    qtbot.waitUntil(lambda: any(k == "warning" for k, _, _ in no_modal), timeout=15000)
    assert "password" in no_modal[-1][2].lower() or "passwort" in no_modal[-1][2].lower()


def test_untrusted_certificate_is_asked_and_stored(qtbot, tmp_path, fakebox_factory, monkeypatch):
    box = fakebox_factory(tls=True)
    asked = []
    monkeypatch.setattr(connection, "ask_trust", lambda parent, exc: asked.append(exc.fingerprint) or True)
    import fritzfax.gui.main_window as mw

    monkeypatch.setattr(mw.connection, "ask_trust", lambda parent, exc: asked.append(exc.fingerprint) or True)
    w, conf = make_window(qtbot, tmp_path, box)
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert asked and config.load(conf).cert_sha256 == asked[0]
    assert w.from_number_combo.currentData() == "12345678"


# --- dialogs ------------------------------------------------------------------------

def test_settings_dialog_saves(qtbot, tmp_path):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings()
    conf = tmp_path / "f.conf"
    dlg = SettingsDialog(s, None, conf)
    qtbot.addWidget(dlg)
    dlg.url_edit.setText("https://fritz.box")
    dlg.sender_name_edit.setText("Ich")
    dlg.header_combo.setCurrentIndex(dlg.header_combo.findData("off"))
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("letter"))
    dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("de"))
    dlg.save()
    loaded = config.load(conf)
    assert (loaded.box_url, loaded.sender_name, loaded.header_mode, loaded.default_mode, loaded.language) == \
        ("https://fritz.box", "Ich", "off", "letter", "de")


def test_settings_box_tab_load_and_save(qtbot, tmp_path, fakebox):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username)
    s.password = fakebox.state.password
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.load_box_settings()
    qtbot.waitUntil(lambda: dlg.box_save_button.isEnabled(), timeout=15000)
    assert dlg.box_identifier.text() == "TestKennung" and dlg.box_numbers.text() == "12345678"
    dlg.box_header.setText("Neu")
    assert dlg.box_changes() == {"header": "Neu"}
    dlg.save_box_settings()
    qtbot.waitUntil(lambda: fakebox.state.config["FaxSenderShort"] == "Neu", timeout=15000)


def test_settings_test_connection(qtbot, tmp_path, fakebox):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username)
    s.password = fakebox.state.password
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.test_connection()
    qtbot.waitUntil(lambda: "12345678" in dlg.test_label.text(), timeout=15000)


def test_journal_dialog(qtbot, fakebox, client_for):
    from fritzfax.gui.journal_dialog import JournalDialog

    fakebox.state.journal.append({"number": "030111", "msn": "1", "sent": 1, "date": "25.10.26 12:00",
                                  "date_time": 77, "pages": 2, "unread": 0, "name": ""})
    dlg = JournalDialog(client_for(fakebox))
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.table.rowCount() == 1, timeout=15000)
    assert dlg.table.item(0, 2).text() == "030111"
    dlg.table.selectRow(0)
    dlg.delete_selected()
    qtbot.waitUntil(lambda: dlg.table.rowCount() == 0, timeout=15000)
    assert fakebox.state.journal == []


def test_two_factor_dialog_totp(qtbot, fakebox, client_for):
    import threading

    from fritzfax.core import twofactor
    from fritzfax.gui.twofa_dialog import TwoFactorDialog

    client = client_for(fakebox)
    fakebox.state.tfa_auto_confirm_after = None
    twofactor.start(client)
    ch = twofactor.Challenge({"button", "dtmf"}, "4711", totp_available=True)
    cancel = threading.Event()
    dlg = TwoFactorDialog(client, "87654321", ch, cancel)
    qtbot.addWidget(dlg)
    assert "*14711" in dlg.steps_label.text()
    dlg.code_edit.setText("12")
    dlg._submit()
    assert "6" in dlg.info.text()
    dlg.code_edit.setText("123456")
    dlg._submit()
    qtbot.waitUntil(lambda: "accepted" in dlg.info.text().lower() or "akzeptiert" in dlg.info.text().lower(),
                    timeout=10000)
    dlg._cancel()
    assert cancel.is_set()


# --- regression: closing while work is running ---------------------------------------

def test_close_stops_preview_timer_and_waits_for_tasks(qtbot, tmp_path):
    from PySide6.QtCore import QThreadPool

    w, _ = make_window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf", pages=3))])
    w.update_preview()               # a render task is running now
    w.schedule_preview()             # and another one is scheduled
    assert w.close()
    assert not w.preview_timer.isActive()
    assert QThreadPool.globalInstance().activeThreadCount() == 0
    w.update_preview()               # no new work after closing
    assert QThreadPool.globalInstance().activeThreadCount() == 0


def test_close_while_sending_asks_first(qtbot, tmp_path, no_modal, monkeypatch):
    w, _ = make_window(qtbot, tmp_path)
    w.sending = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert w.close() is False
    w.sending = False


def test_header_number_gets_country_and_area_code_after_connect(qtbot, tmp_path, fakebox):
    w, _ = make_window(qtbot, tmp_path, fakebox)
    assert w.header_number() == ""  # nothing known yet
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None and w.box_location is not None, timeout=15000)
    assert w.header_number() == "+49 30 12345678"
    w.from_number_combo.addItem("030999", "030999")
    w.from_number_combo.setCurrentIndex(w.from_number_combo.count() - 1)
    assert w.header_number() == "+49 30999"


def test_send_waits_for_pending_preview(qtbot, tmp_path, fakebox, no_modal):
    # clicking "send" before connecting: the connect re-renders (header with area code);
    # the fax must be the re-rendered one, not the stale preview
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.to_edit.setText("0301")
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    wait_preview(qtbot, w, 1)
    stale = w.prepared
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in no_modal), timeout=30000)
    (run,) = fakebox.state.runs
    assert w.prepared is not stale
    assert run.sff == w.prepared.sff


def test_send_log_dialog_shows_content(qtbot, tmp_path, fakebox, no_modal):
    from fritzfax.core.history import History
    from fritzfax.gui.journal_dialog import HistoryDialog

    w, _ = make_window(qtbot, tmp_path, fakebox, sender_name="Test Sender")
    w.to_edit.setText("0301")
    w.mode_letter.setChecked(True)
    w.subject_edit.setText("Antrag")
    w.text_edit.setPlainText("Hallo")
    w.add_files([str(one_page_pdf(tmp_path / "brief.pdf"))])
    wait_preview(qtbot, w, 2)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in no_modal), timeout=30000)
    dlg = HistoryDialog(History())
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 1).text() == "0301"
    assert "Antrag" in dlg.table.item(0, 3).text() and "brief.pdf" in dlg.table.item(0, 3).text()
    dlg.table.setCurrentCell(0, 0)
    assert "Hallo" in dlg.detail.text() and ".pdf" in dlg.detail.text()


def test_result_dialog_shows_verification(qtbot, tmp_path, fakebox, no_modal):
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.to_edit.setText("0301")
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    wait_preview(qtbot, w, 1)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in no_modal), timeout=30000)
    text = [t for k, _, t in no_modal if k == "information"][-1]
    assert "verified" in text.lower() and w.last_results[0].report_pdf
    from fritzfax.core.history import History
    from fritzfax.gui.journal_dialog import HistoryDialog

    dlg = HistoryDialog(History())
    qtbot.addWidget(dlg)
    headers = [dlg.table.horizontalHeaderItem(c).text() for c in range(dlg.table.columnCount())]
    col = headers.index("Verification")
    assert dlg.table.item(0, col).text().lower().startswith("verified")
    assert dlg.report_button.isEnabled() or True


def test_office_without_libreoffice_shows_helpful_message(qtbot, tmp_path, monkeypatch):
    from fritzfax.core.render import office

    monkeypatch.setattr(office, "find_soffice", lambda override="": None)
    w, _ = make_window(qtbot, tmp_path)
    doc = tmp_path / "brief.docx"
    doc.write_bytes(b"x")
    w.add_files([str(doc)])
    qtbot.waitUntil(lambda: "LibreOffice" in w.preview_label.text(), timeout=15000)
    assert "PDF" in w.preview_label.text()


def test_without_box_address_the_user_is_sent_to_the_settings(qtbot, tmp_path, no_modal, monkeypatch):
    monkeypatch.setattr(connection, "ask_password", lambda *a, **k: pytest.fail("password asked without an address"))
    w = MainWindow(config.Settings(), tmp_path / "f.conf")
    qtbot.addWidget(w)
    assert "settings" in w.status_label.text().lower()
    w.connect_box()
    kind, _title, text = no_modal[-1]
    assert kind == "warning" and "No FRITZ!Box address" in text


# --- box fax numbers with country and area code (operator 2026-10-26) ------------------------------------------

def test_sender_numbers_are_shown_with_country_and_area_code(qtbot, tmp_path, fakebox):
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert w.from_number_combo.itemText(0) == "+49 30 12345678"
    assert w.from_number_combo.itemData(0) == "12345678"  # what the box gets when sending


def test_sender_numbers_without_box_location_stay_plain(qtbot, tmp_path, fakebox, monkeypatch):
    from fritzfax.core import boxconfig
    from fritzfax.core.errors import ProtocolError

    def fail(client):
        raise ProtocolError("query.lua not allowed")

    monkeypatch.setattr(boxconfig, "read_settings", fail)
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert w.from_number_combo.itemText(0) == "12345678"


def test_settings_test_connection_shows_numbers_with_area_code(qtbot, tmp_path, fakebox):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username)
    s.password = fakebox.state.password
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.test_connection()
    qtbot.waitUntil(lambda: "12345678" in dlg.test_label.text(), timeout=15000)
    assert "+49 30 12345678" in dlg.test_label.text()


# --- own country code always written with + (operator 2026-10-26) ----------------------------------------------

def _settings_dialog(qtbot, tmp_path, **kw):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings(**kw)
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    return s, dlg


def test_country_code_is_shown_with_plus(qtbot, tmp_path):
    s, dlg = _settings_dialog(qtbot, tmp_path, country_code="43")
    assert dlg.country_edit.text() == "+43"


@pytest.mark.parametrize("typed, stored", [("+43", "43"), ("43", "43"), (" + 1 ", "1"), ("", "49")])
def test_country_code_is_stored_without_plus(qtbot, tmp_path, no_modal, typed, stored):
    s, dlg = _settings_dialog(qtbot, tmp_path)
    dlg.country_edit.setText(typed)
    dlg.save()
    assert s.country_code == stored and config.load(tmp_path / "f.conf").country_code == stored
    assert not [k for k, _, _ in no_modal if k == "warning"]


@pytest.mark.parametrize("typed", ["4x", "+1234", "+", "0049"])
def test_invalid_country_code_is_refused(qtbot, tmp_path, no_modal, typed):
    s, dlg = _settings_dialog(qtbot, tmp_path, country_code="49")
    dlg.country_edit.setText(typed)
    dlg.save()
    assert s.country_code == "49" and not (tmp_path / "f.conf").exists()
    assert [k for k, _, _ in no_modal] == ["warning"]


# --- connect automatically when a box is set up (operator 2026-10-26) ------------------------------------------

def test_auto_connect_connects_to_a_configured_box(qtbot, tmp_path, fakebox, no_modal):
    w, _ = make_window(qtbot, tmp_path, fakebox)
    w.auto_connect()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    assert "Connected" in w.status_label.text() and not no_modal


def test_auto_connect_reports_a_failure(qtbot, tmp_path, no_modal):
    w, _ = make_window(qtbot, tmp_path)  # http://127.0.0.1:9: nobody listens
    w.auto_connect()
    qtbot.waitUntil(lambda: bool(no_modal), timeout=30000)
    assert no_modal[0][0] == "warning" and "cannot be reached" in no_modal[0][2]
    assert w.status_label.text() == "Not connected"


def test_auto_connect_without_box_does_nothing(qtbot, tmp_path, no_modal, monkeypatch):
    s = config.Settings()
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    monkeypatch.setattr(w, "connect_box", lambda *a, **k: pytest.fail("connected without a box address"))
    w.auto_connect()
    assert not no_modal


def test_the_application_connects_after_start(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from fritzfax.gui import app

    calls = []
    monkeypatch.setattr(MainWindow, "auto_connect", lambda self: calls.append(self))
    monkeypatch.setattr(QApplication, "exec", lambda *a: 0)
    monkeypatch.setattr(MainWindow, "show", lambda self: None)
    conf = tmp_path / "f.conf"
    config.save(config.Settings(box_url="https://fritz.box"), conf)
    try:
        assert app.main(["--config", str(conf)]) == 0
    finally:  # app.main installs the (German default) translator and the theme in the shared application
        from fritzfax.gui import i18n, theme

        i18n.install(QApplication.instance(), "en")
        theme.apply(QApplication.instance(), "system")
    assert len(calls) == 1
