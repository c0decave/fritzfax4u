"""Address book in the GUI and the send log per recipient (operator 2026-10-26)."""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from fritzfax.core import config
from fritzfax.core.addressbook import AddressBook
from fritzfax.core.history import History
from fritzfax.gui import addressbook_dialog, workers
from fritzfax.gui.addressbook_dialog import AddressBookDialog, ContactDialog
from fritzfax.gui.journal_dialog import HistoryDialog
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


@pytest.fixture
def book(tmp_path):
    return AddressBook(tmp_path / "ab.db")


def fill_and_accept(monkeypatch, name, number, note=""):
    """ContactDialog.exec as if the user typed the entry and pressed OK."""
    def exec_(dlg):
        dlg.name_edit.setText(name)
        dlg.number_edit.setText(number)
        dlg.note_edit.setText(note)
        dlg.save()
        return dlg.result()
    monkeypatch.setattr(ContactDialog, "exec", exec_)


def rows(dlg):
    return [(dlg.table.item(r, 0).text(), dlg.table.item(r, 1).text())
            for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r)]


# --- contact dialog -------------------------------------------------------------------------------

def test_contact_dialog_adds_and_edits(qtbot, book, shown):
    dlg = ContactDialog(book)
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("Muster GmbH")
    dlg.number_edit.setText("0531 49059113")
    dlg.save()
    assert dlg.result() == QDialog.DialogCode.Accepted
    (c,) = book.contacts()
    edit = ContactDialog(book, contact=c)
    qtbot.addWidget(edit)
    assert edit.name_edit.text() == "Muster GmbH"
    edit.note_edit.setText("Buchhaltung")
    edit.save()
    assert book.get(c.id).note == "Buchhaltung"


@pytest.mark.parametrize("name, number, field", [("", "030 1", "name"), ("Amt", "kein Fax", "number")])
def test_contact_dialog_refuses_bad_entries_and_stays_open(qtbot, book, shown, name, number, field):
    dlg = ContactDialog(book)
    qtbot.addWidget(dlg)
    dlg.name_edit.setText(name)
    dlg.number_edit.setText(number)
    dlg.save()
    assert dlg.result() != QDialog.DialogCode.Accepted and book.contacts() == []
    assert shown and shown[-1][0] == "warning"


def test_contact_dialog_refuses_a_duplicate(qtbot, book, shown):
    book.add("Amt", "030 1")
    dlg = ContactDialog(book, number="+49 30 1", name="amt")
    qtbot.addWidget(dlg)
    dlg.save()
    assert dlg.result() != QDialog.DialogCode.Accepted and len(book.contacts()) == 1


# --- address book dialog ----------------------------------------------------------------------------

def test_address_book_lists_searches_adds_and_deletes(qtbot, book, shown, monkeypatch):
    book.add("Muster GmbH", "0531 49059113")
    dlg = AddressBookDialog(book)
    qtbot.addWidget(dlg)
    fill_and_accept(monkeypatch, "Amt", "030 1234567")
    dlg.new_contact()
    assert rows(dlg) == [("Amt", "030 1234567"), ("Muster GmbH", "0531 49059113")]
    dlg.search_edit.setText("muster")
    assert rows(dlg) == [("Muster GmbH", "0531 49059113")]
    dlg.search_edit.setText("")
    dlg.table.selectRow(0)
    dlg.delete_selected()  # asks first (answered Yes)
    assert shown[-1][0] == "question" and [c.name for c in book.contacts()] == ["Muster GmbH"]


def test_address_book_delete_can_be_declined(qtbot, book, monkeypatch):
    book.add("Amt", "030 1")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dlg = AddressBookDialog(book)
    qtbot.addWidget(dlg)
    dlg.table.selectRow(0)
    dlg.delete_selected()
    assert len(book.contacts()) == 1


def test_address_book_hands_the_chosen_entries_to_the_fax(qtbot, book):
    book.add("Amt", "030 1")
    book.add("Muster GmbH", "0402")
    dlg = AddressBookDialog(book, choose=True)
    qtbot.addWidget(dlg)
    dlg.table.selectAll()
    dlg.use_selected()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert [c.name for c in dlg.chosen] == ["Amt", "Muster GmbH"]


def test_address_book_shows_the_faxes_sent_to_an_entry(qtbot, book, tmp_path, monkeypatch):
    c = book.add("Amt", "030 1")
    opened = []
    monkeypatch.setattr(addressbook_dialog, "show_history", lambda parent, bk, contact: opened.append(contact))
    dlg = AddressBookDialog(book)
    qtbot.addWidget(dlg)
    dlg.table.selectRow(0)
    dlg.history_button.click()
    assert opened == [c]


def test_address_book_with_nothing_selected_does_nothing(qtbot, book, shown):
    dlg = AddressBookDialog(book)
    qtbot.addWidget(dlg)
    dlg.delete_selected()
    dlg.use_selected()
    assert book.contacts() == [] and dlg.result() != QDialog.DialogCode.Accepted and not shown


# --- send log per recipient --------------------------------------------------------------------------

def log_with(tmp_path, *destinations):
    from PIL import Image

    from fritzfax.core import history as hist, sff

    log = History(tmp_path / "h.db", tmp_path / "sent")
    page = Image.new("1", (sff.LINE_WIDTH, sff.A4_LINES), 1)
    job = log.start_job(hist.Content(subject="Antrag"), [page], sff.encode_pages([page]))
    for d in destinations:
        log.record(job, d, True, "", 0)
    return log


def test_send_log_names_the_recipients_and_filters(qtbot, tmp_path, book):
    book.add("Muster GmbH", "0301")
    dlg = HistoryDialog(log_with(tmp_path, "0301", "0402"), book=book)
    qtbot.addWidget(dlg)
    recipients = [dlg.table.item(r, 1).text() for r in range(dlg.table.rowCount())]
    assert sorted(recipients) == ["0402", "Muster GmbH (0301)"]
    dlg.filter_edit.setText("muster")
    assert [dlg.table.item(r, 1).text() for r in range(dlg.table.rowCount())
            if not dlg.table.isRowHidden(r)] == ["Muster GmbH (0301)"]


def test_send_log_saves_a_recipient_in_the_address_book(qtbot, tmp_path, book, shown, monkeypatch):
    dlg = HistoryDialog(log_with(tmp_path, "0402"), book=book)
    qtbot.addWidget(dlg)
    dlg.table.setCurrentCell(0, 0)
    fill_and_accept(monkeypatch, "Amt", "0402")
    dlg.save_contact()
    assert [(c.name, c.dial) for c in book.contacts()] == [("Amt", "0402")]
    assert dlg.table.item(0, 1).text() == "Amt (0402)"


def test_show_history_is_filtered_to_the_contact(qtbot, tmp_path, book, monkeypatch):
    c = book.add("Amt", "0301")
    log = log_with(tmp_path, "0301", "0402", "0301")
    monkeypatch.setattr(addressbook_dialog, "History", lambda: log)
    seen = []
    monkeypatch.setattr(HistoryDialog, "exec", lambda dlg: seen.append(dlg) or 0)
    addressbook_dialog.show_history(None, book, c)
    (dlg,) = seen
    assert dlg.table.rowCount() == 2 and "Amt" in dlg.windowTitle()


# --- main window ------------------------------------------------------------------------------------

def window(qtbot, tmp_path, box=None):
    s = config.Settings(box_url=box.url if box else "http://127.0.0.1:9",
                        username=box.state.username if box else "", poll_interval=0.02)
    s.password = box.state.password if box else "x"
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    return w


def test_numbers_menu_offers_the_address_book(qtbot, tmp_path):
    AddressBook().add("Muster GmbH", "0531 49059113")
    w = window(qtbot, tmp_path)
    w._fill_number_menu()
    actions = {a.text(): a for a in w.to_menu.actions()}
    entry = next(a for t, a in actions.items() if "Muster GmbH" in t)
    entry.trigger()
    assert w.to_edit.text() == "0531 49059113" and w.to_name_edit.text() == "Muster GmbH"
    assert any("Address book" in t for t in actions)


def test_recipient_can_be_saved_in_the_address_book(qtbot, tmp_path, shown, monkeypatch):
    w = window(qtbot, tmp_path)
    w.to_edit.setText("030 1234567")
    w.to_name_edit.setText("Amt")
    prefilled = []

    def exec_(dlg):
        prefilled.append((dlg.name_edit.text(), dlg.number_edit.text()))
        dlg.save()
        return dlg.result()
    monkeypatch.setattr(ContactDialog, "exec", exec_)
    w.save_recipient()
    assert prefilled == [("Amt", "030 1234567")]
    assert [c.name for c in AddressBook().contacts()] == ["Amt"]


def test_a_name_from_the_address_book_is_a_valid_recipient(qtbot, tmp_path, fakebox, shown):
    AddressBook().add("Muster GmbH", "0531 49059113")
    w = window(qtbot, tmp_path, fakebox)
    w.to_edit.setText("Muster GmbH")
    w._check_numbers()
    assert not w.to_hint.isVisible() or "Invalid" not in w.to_hint.text()
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None and w.thumbs.count() == 1, timeout=20000)
    w.send()
    qtbot.waitUntil(lambda: any(k == "information" and "053149059113" in t for k, _, t in shown), timeout=30000)
    assert [r.dest for r in fakebox.state.runs] == ["053149059113"]


def test_an_unknown_name_is_refused_before_sending(qtbot, tmp_path, fakebox, shown):
    w = window(qtbot, tmp_path, fakebox)
    w.to_edit.setText("Unbekannt")
    w._check_numbers()
    assert "Unbekannt" in w.to_hint.text()
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None and w.thumbs.count() == 1, timeout=20000)
    w.send()
    assert shown[-1][0] == "warning" and not fakebox.state.runs
