"""Address book: names and fax numbers (core.addressbook), and the faxes sent to an entry."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.addressbook import MAX_NAME, MAX_NOTE, AddressBook, Contact, InvalidContact
from ..core.history import History
from ..core.numbers import MAX_LEN, InvalidNumber
from . import connection


def tr(text: str) -> str:
    return QCoreApplication.translate("AddressBook", text)


class ContactDialog(QDialog):
    """Enter or change one entry; OK keeps it in the address book, a bad entry keeps the dialog open."""

    def __init__(self, book: AddressBook, parent: QWidget | None = None, contact: Contact | None = None,
                 name: str = "", number: str = ""):
        super().__init__(parent)
        self.book, self.contact = book, contact
        self.setWindowTitle(self.tr("Edit entry") if contact else self.tr("New entry"))
        form = QFormLayout(self)
        self.name_edit = QLineEdit(contact.name if contact else name)
        self.name_edit.setMaxLength(MAX_NAME)
        form.addRow(self.tr("Name:"), self.name_edit)
        self.number_edit = QLineEdit(contact.number if contact else number)
        self.number_edit.setMaxLength(MAX_LEN + 20)  # room for spaces and dashes
        self.number_edit.setPlaceholderText(self.tr("e.g. 0531 49059113"))
        form.addRow(self.tr("Fax number:"), self.number_edit)
        self.note_edit = QLineEdit(contact.note if contact else "")
        self.note_edit.setMaxLength(MAX_NOTE)
        form.addRow(self.tr("Note:"), self.note_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.saved: Contact | None = None

    def save(self) -> None:
        args = (self.name_edit.text(), self.number_edit.text(), self.note_edit.text())
        try:
            self.saved = self.book.update(self.contact.id, *args) if self.contact else self.book.add(*args)
        except InvalidContact as exc:
            text = (self.tr("Please enter a name.") if exc.kind == "name" else
                    self.tr("This name with this number is already in the address book."))
            QMessageBox.warning(self, self.tr("Address book"), text)
            (self.name_edit if exc.kind == "name" else self.number_edit).setFocus()
            return
        except InvalidNumber as exc:
            QMessageBox.warning(self, self.tr("Address book"), connection.describe_number_error(exc))
            self.number_edit.setFocus()
            return
        except KeyError:  # deleted meanwhile (in another window)
            QMessageBox.warning(self, self.tr("Address book"), self.tr("The entry does not exist any more."))
            self.reject()
            return
        self.accept()


class AddressBookDialog(QDialog):
    """List, search, add, change and delete entries; `choose`: pick entries for the fax (self.chosen)."""

    def __init__(self, book: AddressBook, parent: QWidget | None = None, choose: bool = False):
        super().__init__(parent)
        self.book = book
        self.chosen: list[Contact] = []
        self.setWindowTitle(self.tr("Address book"))
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("Search name, number or note"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh)
        layout.addWidget(self.search_edit)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr("Name"), self.tr("Fax number"), self.tr("Note")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self.use_selected if choose else self.edit_selected)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, slot in ((self.tr("New..."), self.new_contact), (self.tr("Edit..."), self.edit_selected),
                           (self.tr("Delete..."), self.delete_selected)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.history_button = QPushButton(self.tr("Sent faxes..."))
        self.history_button.clicked.connect(self.show_selected_history)
        row.addWidget(self.history_button)
        row.addStretch(1)
        if choose:
            self.use_button = QPushButton(self.tr("Fax to selected"))
            self.use_button.setObjectName("primary")
            self.use_button.clicked.connect(self.use_selected)
            row.addWidget(self.use_button)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.contacts: list[Contact] = []
        self.refresh()

    def refresh(self, *_):
        self.contacts = self.book.contacts(self.search_edit.text())
        self.table.setRowCount(len(self.contacts))
        for r, c in enumerate(self.contacts):
            for col, value in enumerate((c.name, c.number, c.note)):
                self.table.setItem(r, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def selected(self) -> list[Contact]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.contacts[r] for r in rows if 0 <= r < len(self.contacts)]

    def new_contact(self):
        if ContactDialog(self.book, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def edit_selected(self, *_):
        chosen = self.selected()
        if chosen and ContactDialog(self.book, self, contact=chosen[0]).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def delete_selected(self):
        chosen = self.selected()
        if not chosen:
            return
        names = "\n".join(f"{c.name}  ({c.number})" for c in chosen)
        answer = QMessageBox.question(self, self.tr("Address book"),
                                      self.tr("Delete these entries from the address book?\n\n%1").replace("%1", names))
        if answer != QMessageBox.StandardButton.Yes:
            return
        for c in chosen:
            try:
                self.book.delete(c.id)
            except KeyError:
                pass  # already gone
        self.refresh()

    def use_selected(self, *_):
        self.chosen = self.selected()
        if self.chosen:
            self.accept()

    def show_selected_history(self):
        chosen = self.selected()
        if chosen:
            show_history(self, self.book, chosen[0])


def show_history(parent: QWidget | None, book: AddressBook, contact: Contact) -> None:
    """The send log, only the faxes to this entry's number."""
    from .journal_dialog import HistoryDialog

    dlg = HistoryDialog(History(), parent, book=book, destinations=[contact.dial])
    dlg.setWindowTitle(tr("Faxes sent to %1").replace("%1", f"{contact.name} ({contact.number})"))
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.exec()
