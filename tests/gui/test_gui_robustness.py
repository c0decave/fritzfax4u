"""GUI robustness (review 2026-10-25): double clicks, bad input, write errors, keyring, translations."""
import ast
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from fritzfax.core import config, verify
from fritzfax.gui import connection, i18n, theme, workers
from fritzfax.gui.main_window import MainWindow
from fritzfax.gui.settings_dialog import SettingsDialog
from tests_helpers import one_page_pdf

ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def shown(monkeypatch, qtbot):
    """Record message boxes; the question box answers Yes."""
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


def ready_to_send(qtbot, w, tmp_path, number="0301"):
    w.to_edit.setText(number)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None and w.thumbs.count() == 1, timeout=20000)


# --- sending twice ---------------------------------------------------------------------------

def test_second_click_while_connecting_sends_only_once(qtbot, tmp_path, fakebox, monkeypatch, shown):
    w = window(qtbot, tmp_path, fakebox)
    ready_to_send(qtbot, w, tmp_path)
    questions = []

    def slow_question(*a, **k):  # the real question box runs its own event loop
        questions.append(a[2])
        QTest.qWait(400)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", slow_question)
    w.send()
    w.send()  # second click before the connection is up
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in shown), timeout=30000)
    QTest.qWait(500)
    assert len(fakebox.state.runs) == 1
    assert len(questions) == 1


def test_confirming_twice_starts_one_send(qtbot, tmp_path, fakebox, monkeypatch, shown):
    # already connected: two clicks, each answered with Yes while the other question is open
    w = window(qtbot, tmp_path, fakebox)
    ready_to_send(qtbot, w, tmp_path)
    w.connect_box()
    qtbot.waitUntil(lambda: w.box_info is not None, timeout=15000)
    nested = []

    def question(*a, **k):
        if not nested:
            nested.append(1)
            w.send()  # the second click arrives while the first question is open
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" for k, _, _ in shown), timeout=30000)
    QTest.qWait(300)
    assert len(fakebox.state.runs) == 1


# --- bad box address -------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["ftp://fritz.box", "fritz.box:abc", "http://"])
def test_invalid_box_address_gives_a_message(qtbot, tmp_path, shown, url):
    w = window(qtbot, tmp_path)
    w.settings.box_url = url
    w.connect_box()  # must not raise
    kind, _title, text = shown[-1]
    assert kind == "warning" and "address" in text.lower()
    assert w.client is None


def test_settings_refuse_an_invalid_box_address(qtbot, tmp_path, shown):
    conf = tmp_path / "f.conf"
    dlg = SettingsDialog(config.Settings(), None, conf)
    qtbot.addWidget(dlg)
    dlg.url_edit.setText("ftp://fritz.box")
    dlg.save()
    assert shown and shown[-1][0] == "warning" and "address" in shown[-1][2].lower()
    assert dlg.result() == 0 and not conf.exists()
    dlg.url_edit.setText("fritz.box")
    dlg.save()
    assert config.load(conf).box_url == "fritz.box"


# --- write errors -------------------------------------------------------------------------------------

def test_export_into_a_folder_without_permission(qtbot, tmp_path, monkeypatch, shown):
    w = window(qtbot, tmp_path)
    ready_to_send(qtbot, w, tmp_path)
    target = tmp_path / "no" / "such" / "dir" / "fax.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    w.export_preview()  # must not raise
    assert shown[-1][0] == "warning" and "fax.pdf" in shown[-1][2]


def test_journal_save_into_a_folder_without_permission(qtbot, tmp_path, fakebox, client_for, monkeypatch, shown):
    from fritzfax.gui.journal_dialog import JournalDialog

    fakebox.state.journal.append({"number": "0301", "msn": "1", "sent": 1, "date": "x", "date_time": 5,
                                  "pages": 1, "unread": 0, "name": ""})
    dlg = JournalDialog(client_for(fakebox))
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.table.rowCount() == 1, timeout=15000)
    dlg.table.selectRow(0)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "no" / "dir" / "f.pdf"), ""))
    dlg.save_selected()
    qtbot.waitUntil(lambda: "f.pdf" in dlg.info.text(), timeout=15000)
    assert "Saved" not in dlg.info.text()


# --- keyring --------------------------------------------------------------------------------------------

class FakeKeyring:
    def __init__(self, fail=False):
        self.stored, self.fail = [], fail

    def get_password(self, service, account):
        return None

    def set_password(self, service, account, password):
        if self.fail:
            raise RuntimeError("the wallet was not unlocked")
        self.stored.append((account, password))


def test_settings_save_survives_a_refusing_keyring(qtbot, tmp_path, monkeypatch, shown):
    monkeypatch.setattr(config, "_keyring", lambda: FakeKeyring(fail=True))
    conf = tmp_path / "f.conf"
    dlg = SettingsDialog(config.Settings(box_url="fritz.box"), None, conf)
    qtbot.addWidget(dlg)
    dlg.password_edit.setText("pw")
    dlg.save()
    assert conf.exists() and dlg.result() == QDialogCode.Accepted
    assert any("keyring" in t.lower() for _, _, t in shown)


class QDialogCode:
    Accepted = 1


@pytest.mark.parametrize("store", [True, False])
def test_password_prompt_stores_only_when_asked_to(qtbot, tmp_path, monkeypatch, store):
    kr = FakeKeyring()
    monkeypatch.setattr(config, "_keyring", lambda: kr)
    monkeypatch.setattr(connection, "ask_password", lambda parent, user: ("typed-pw", store))
    s = config.Settings(box_url="http://192.0.2.1")
    client = connection.make_client(s, None)
    assert client is not None and s.password == "typed-pw"
    assert kr.stored == []  # nothing is stored before the box accepted the password
    connection.store_pending_password(s, None)  # what a successful login does
    assert bool(kr.stored) is store


def test_password_prompt_cancelled(qtbot, monkeypatch):
    monkeypatch.setattr(connection, "ask_password", lambda parent, user: None)
    assert connection.make_client(config.Settings(box_url="http://192.0.2.1"), None) is None


def test_password_dialog(qtbot):
    dlg = connection.PasswordDialog(None, "fritz1234")
    qtbot.addWidget(dlg)
    assert "fritz1234" in dlg.label.text()
    assert dlg.store_check.isChecked()  # like the checkbox in the settings
    dlg.edit.setText("secret")
    dlg.store_check.setChecked(False)
    assert dlg.values() == ("secret", False)


# --- two-factor dialog -----------------------------------------------------------------------------------

def test_two_factor_code_button_comes_back_after_an_error(qtbot, monkeypatch):
    import threading

    from fritzfax.core import twofactor
    from fritzfax.core.errors import ConnectionFailed
    from fritzfax.gui.twofa_dialog import TwoFactorDialog

    def broken(client, code):
        raise ConnectionFailed("box went away")

    monkeypatch.setattr(twofactor, "submit_totp", broken)
    dlg = TwoFactorDialog(None, "0301", twofactor.Challenge({"button"}, "", totp_available=True), threading.Event())
    qtbot.addWidget(dlg)
    dlg.code_edit.setText("123456")
    dlg._submit()
    qtbot.waitUntil(lambda: dlg.code_button.isEnabled(), timeout=5000)
    assert "cannot be reached" in dlg.info.text()


# --- translations of results -------------------------------------------------------------------------------

def _problem_templates():
    """Every problem text verify.collect() can produce, with its placeholders filled."""
    tree = ast.parse((ROOT / "fritzfax" / "core" / "verify.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append"
                and ast.unparse(node.func.value) == "ev.problems"):
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                out.append(arg.value)
            else:  # f-string: fill every {...} with a sample value
                out.append("".join(p.value if isinstance(p, ast.Constant) else "7" for p in arg.values))
    return out


def test_every_verification_problem_has_a_translation():
    templates = _problem_templates()
    assert len(templates) >= 10
    for text in templates:
        assert connection.problem_known(text), text


def test_verification_problems_are_german_in_the_german_ui(qtbot):
    app = QApplication.instance()
    try:
        i18n.install(app, "de")
        assert connection.describe_problem("no journal entry for this fax") == \
            "Das Fax-Journal der Box hat keinen Eintrag für dieses Fax."
        assert connection.describe_problem("journal shows 2 page(s), 3 were sent") == \
            "Das Fax-Journal zeigt 2 Seite(n), gesendet wurden 3."
        assert connection.describe_problem("something new") == "something new"  # unknown: as it is
    finally:
        i18n.install(app, "en")


def test_send_errors_in_the_result_are_described_like_other_errors(qtbot, tmp_path, fakebox_factory, shown):
    box = fakebox_factory(scenario="reason:3490")
    w = window(qtbot, tmp_path, box)
    ready_to_send(qtbot, w, tmp_path)
    w.send()
    qtbot.waitUntil(lambda: any(k == "warning" for k, _, _ in shown), timeout=30000)
    text = [t for k, _, t in shown if k == "warning"][-1]
    assert "Transmission failed (error code 0x3490)." in text


def test_declined_certificate_is_described():
    from fritzfax.core.errors import UntrustedCertificate

    text = connection.describe(UntrustedCertificate("AA:BB"))
    assert "certificate" in text.lower() and "AA:BB" in text


def test_send_log_shows_the_mode_translated(qtbot, tmp_path):
    from fritzfax.core import history, sff
    from fritzfax.gui.journal_dialog import HistoryDialog
    from PIL import Image

    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    page = Image.new("1", (sff.LINE_WIDTH, 50), 1)
    job_id = log.start_job(history.Content(mode="letter"), [page], sff.encode_pages([page]))
    log.record(job_id, "0301", True, "", 0)
    dlg = HistoryDialog(log)
    qtbot.addWidget(dlg)
    headers = [dlg.table.horizontalHeaderItem(c).text() for c in range(dlg.table.columnCount())]
    assert dlg.table.item(0, headers.index("Content")).text() == "Letter"


# --- settings: cancel means cancel ------------------------------------------------------------------------

def test_forget_recent_numbers_is_undone_by_cancel(qtbot, tmp_path):
    s = config.Settings(recent_numbers=["0301", "0302"])
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.clear_recent.click()
    dlg.reject()
    assert s.recent_numbers == ["0301", "0302"]


def test_forget_recent_numbers_takes_effect_on_save(qtbot, tmp_path):
    s = config.Settings(box_url="fritz.box", recent_numbers=["0301"])
    conf = tmp_path / "f.conf"
    dlg = SettingsDialog(s, None, conf)
    qtbot.addWidget(dlg)
    dlg.clear_recent.click()
    assert not dlg.clear_recent.isEnabled()
    dlg.save()
    assert s.recent_numbers == [] and config.load(conf).recent_numbers == []


# --- theme --------------------------------------------------------------------------------------------------

def test_hint_color_follows_a_theme_change(qtbot, tmp_path):
    app = QApplication.instance()
    w = window(qtbot, tmp_path)
    try:
        theme.apply(app, "light")
        QApplication.sendEvent(w, QEvent(QEvent.Type.ApplicationPaletteChange))
        light = w.to_hint.styleSheet()
        theme.apply(app, "dark")
        QApplication.sendEvent(w, QEvent(QEvent.Type.ApplicationPaletteChange))
        assert w.to_hint.styleSheet() != light and theme.hint_color(app).name() in w.to_hint.styleSheet()
    finally:
        theme.apply(app, "system")


# --- German wording -------------------------------------------------------------------------------------------

def test_german_label_for_the_start_timeout_has_its_comma():
    import json

    table = json.loads((ROOT / "tools" / "i18n_de.json").read_text(encoding="utf-8"))
    assert table["Timeout until the box starts:"] == "Timeout, bis die Box startet:"


def test_export_without_extension_writes_a_pdf(qtbot, tmp_path, monkeypatch, shown):
    w = window(qtbot, tmp_path)
    ready_to_send(qtbot, w, tmp_path)
    target = tmp_path / "fax-ohne-endung"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    w.export_preview()
    assert target.read_bytes().startswith(b"%PDF")
    assert not shown
