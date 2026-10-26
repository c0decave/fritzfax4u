"""Main window: destinations, sender, mode, files, preview, send."""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from PIL import Image

from .. import __version__
from ..core import boxconfig, config, faxapi, job, render
from ..core.errors import FaxResultUnknown, LoginBlocked, LoginFailed, UntrustedCertificate
from ..core.history import FAX_DPI, History
from ..core.addressbook import AddressBook, resolve
from ..core.numbers import (
    InvalidNumber,
    box_number_display,
    is_international,
    looks_local_without_area_code,
    printed_number,
)
from . import connection, theme
from .helptip import with_help
from .images import to_qimage
from .twofa_dialog import TwoFactorDialog
from .workers import SendEvents, run_async, wait_all

FILE_FILTER_EXT = sorted(render.SUPPORTED_EXTENSIONS)
MENU_CONTACTS = 25  # address book entries in the "Numbers..." menu; the rest in the address book window


def _connection_key(s: config.Settings) -> tuple:
    return s.box_url, s.username, s.password, s.cert_sha256, s.timeout


class FileList(QListWidget):
    """File list that accepts drag & drop from the file manager."""

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self.on_drop = on_drop
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.on_drop([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, settings: config.Settings, config_path: Path | None = None, language: str = "en"):
        super().__init__()
        self.settings = settings
        self.config_path = config_path
        self.language = language
        self.client = None
        self.box_info: faxapi.FaxInfo | None = None
        self.box_location: tuple[str, str] | None = None  # (country code, area code) of the box
        self.prepared: job.Prepared | None = None
        self._render_generation = 0
        self.sending = False
        self.overdue_dest: str | None = None  # destination whose call outlasted the time budget
        self.events: SendEvents | None = None
        self.twofa_dialog: TwoFactorDialog | None = None
        self.last_results: list[job.Result] = []
        self.closing = False
        self.preview_pending = False
        self.send_after_preview = False
        self.connecting = False  # a login is running; a second one on the same client would collide
        self.send_client = None  # the client a running send uses (self.client may be replaced meanwhile)
        self.password_rejected = False  # ask for a new password instead of using the stored one
        self._book: AddressBook | None = None  # see address_book()
        self.reset_client_after_send = False  # connection settings changed during a send
        self.send_after_connect = False  # "send" was clicked while a login was running
        self.celebration = None  # the send animation of the running send (Settings - Interface)
        self.setWindowTitle(self.tr("fritzfax - fax with the FRITZ!Box"))
        self.setWindowIcon(QIcon.fromTheme("printer"))
        self.resize(1100, 760)
        self._build()
        self._build_menu()
        theme.tidy(self)
        self.preview_timer = QTimer(self, singleShot=True, interval=500)
        self.preview_timer.timeout.connect(self.update_preview)
        self._apply_settings()

    # --- layout ------------------------------------------------------------------------
    def _build(self):
        form_box = QWidget()
        form = QVBoxLayout(form_box)
        form.setContentsMargins(4, 0, 4, 0)
        form.setSpacing(4)
        title = QLabel(self.tr("New fax"))
        title.setObjectName("title")
        form.addWidget(title)

        to_group = QGroupBox(self.tr("Recipient"))
        to_form = QFormLayout(to_group)
        to_row = QHBoxLayout()
        self.to_edit = QLineEdit()
        self.to_edit.setPlaceholderText(self.tr("e.g. 0531 49059113, 030 1234567"))
        self.to_button = QPushButton(self.tr("Numbers..."))
        self.to_menu = QMenu(self)
        self.to_button.setMenu(self.to_menu)
        self.to_menu.aboutToShow.connect(self._fill_number_menu)
        to_row.addWidget(self.to_edit, 1)
        to_row.addWidget(self.to_button)
        to_form.addRow(self.tr("Fax number(s):"), with_help(self, "to_numbers", to_row))
        self.to_name_edit = QLineEdit()
        self.to_name_edit.setPlaceholderText(self.tr("only used on the cover sheet"))
        to_form.addRow(self.tr("Recipient name:"), with_help(self, "to_name", self.to_name_edit))
        self.to_hint = QLabel()
        self.to_hint.setVisible(False)
        self.to_hint.setWordWrap(True)
        to_form.addRow(self.to_hint)
        form.addWidget(to_group)

        from_group = QGroupBox(self.tr("Sender"))
        from_form = QFormLayout(from_group)
        self.from_name_edit = QLineEdit()
        self.from_name_edit.setMaxLength(200)
        from_form.addRow(self.tr("Sender name:"), with_help(self, "from_name", self.from_name_edit))
        self.from_number_combo = QComboBox()
        self.from_number_combo.setEditable(False)
        from_form.addRow(self.tr("Send from:"), with_help(self, "from_number", self.from_number_combo))
        self.from_hint = QLabel()
        self.from_hint.setVisible(False)
        self.from_hint.setWordWrap(True)
        from_form.addRow(self.from_hint)
        form.addWidget(from_group)

        content_group = QGroupBox(self.tr("Content"))
        content = QVBoxLayout(content_group)
        mode_row = QHBoxLayout()
        self.mode_document = QRadioButton(self.tr("Document: the files are the fax"))
        self.mode_letter = QRadioButton(self.tr("Letter: text first, files after it"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_document)
        self.mode_group.addButton(self.mode_letter)
        mode_row.addWidget(self.mode_document)
        mode_row.addWidget(self.mode_letter)
        content.addWidget(with_help(self, "mode", mode_row, compact=True))
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText(self.tr("Subject"))
        content.addWidget(self.subject_edit)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(self.tr("Letter text"))
        content.addWidget(self.text_edit, 1)

        files_row = QHBoxLayout()
        self.file_list = FileList(self.add_files)
        self.file_list.itemDoubleClicked.connect(self._edit_pages)
        files_row.addWidget(self.file_list, 1)
        buttons = QVBoxLayout()
        self.add_button = QPushButton(self.tr("Add..."))
        self.remove_button = QPushButton(self.tr("Remove"))
        self.up_button = QPushButton(self.tr("Up"))
        self.down_button = QPushButton(self.tr("Down"))
        for b in (self.add_button, self.remove_button, self.up_button, self.down_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        files_row.addLayout(buttons)
        content.addWidget(with_help(self, "files", QLabel(self.tr("Files (PDF, images, office documents):")),
                                    compact=True))
        content.addLayout(files_row, 1)

        options_row = QHBoxLayout()
        self.cover_check = QCheckBox(self.tr("Cover sheet"))
        self.header_combo = QComboBox()
        for key, label in (("overlay", self.tr("Header line on top")), ("shrink", self.tr("Header line, page shrunk")),
                           ("off", self.tr("No header line"))):
            self.header_combo.addItem(label, key)
        options_row.addWidget(with_help(self, "cover", self.cover_check))
        options_row.addWidget(with_help(self, "header", self.header_combo))
        options_row.addStretch(1)
        content.addLayout(options_row)
        form.addWidget(content_group, 1)

        # preview
        preview_box = QWidget()
        pv = QVBoxLayout(preview_box)
        self.preview_label = QLabel(self.tr("Preview"))
        pv.addWidget(with_help(self, "preview", self.preview_label, compact=True))
        self.thumbs = QListWidget()
        self.thumbs.setObjectName("thumbs")
        self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setIconSize(QSize(96, 128))
        self.thumbs.setFlow(QListWidget.Flow.LeftToRight)
        self.thumbs.setWrapping(False)
        self.thumbs.setSpacing(4)
        self.thumbs.setFixedHeight(196)
        self.thumbs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumbs.currentRowChanged.connect(self._show_page)
        # remove single pages: Del or the context menu, always after a question
        self.thumbs.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.thumbs.setToolTip(self.tr("Del or right click: remove the selected page(s) from the fax"))
        self.thumbs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(
            lambda pos: self.thumbs_menu().exec(self.thumbs.viewport().mapToGlobal(pos)))
        self.thumbs.installEventFilter(self)  # Del, see eventFilter()
        pv.addWidget(self.thumbs)
        self.page_view = QLabel()
        self.page_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_area = QScrollArea()  # the whole page fits; scroll bars only for tiny windows
        self.page_area.setObjectName("pageArea")
        self.page_area.setWidgetResizable(True)
        self.page_area.setWidget(self.page_view)
        self.page_area.viewport().installEventFilter(self)  # resize: fit the page again
        pv.addWidget(self.page_area, 1)

        splitter = QSplitter()
        splitter.addWidget(form_box)
        splitter.addWidget(preview_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(4, 4, 4, 0)
        self.connection_chip = QLabel()
        self.connection_chip.setObjectName("chip")
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self._show_idle_status()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.cancel_button = QPushButton(self.tr("Cancel"))
        self.cancel_button.setVisible(False)
        self.send_button = QPushButton(self.tr("Send fax"))
        self.send_button.setObjectName("primary")
        self.send_button.setDefault(True)
        self.send_button.setMinimumWidth(160)
        bottom.addWidget(self.connection_chip)
        bottom.addSpacing(6)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.send_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

        # signals
        self.add_button.clicked.connect(self.choose_files)
        self.remove_button.clicked.connect(self._remove_selected)
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.file_list.model().rowsMoved.connect(self.schedule_preview)
        self.mode_group.buttonToggled.connect(self._mode_changed)
        for w in (self.subject_edit, self.from_name_edit, self.to_name_edit):
            w.textChanged.connect(self.schedule_preview)
        self.text_edit.textChanged.connect(self.schedule_preview)
        self.to_edit.textChanged.connect(self._check_numbers)
        self.cover_check.toggled.connect(self._mode_changed)  # the cover sheet has a subject line
        self.header_combo.currentIndexChanged.connect(self.schedule_preview)
        self.from_number_combo.currentIndexChanged.connect(self.schedule_preview)
        self.send_button.clicked.connect(self.send)
        self.cancel_button.clicked.connect(self.cancel)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu(self.tr("&File"))
        act = QAction(self.tr("Add files..."), self, shortcut=QKeySequence.StandardKey.Open)
        act.triggered.connect(self.choose_files)
        file_menu.addAction(act)
        act = QAction(self.tr("Address book..."), self)
        act.triggered.connect(self.open_address_book)
        file_menu.addAction(act)
        act = QAction(self.tr("Export preview..."), self)
        act.triggered.connect(self.export_preview)
        file_menu.addAction(act)
        file_menu.addSeparator()
        act = QAction(self.tr("Quit"), self, shortcut=QKeySequence.StandardKey.Quit)
        act.triggered.connect(self.close)
        file_menu.addAction(act)
        box_menu = self.menuBar().addMenu(self.tr("&Box"))
        act = QAction(self.tr("Connect"), self)
        act.triggered.connect(self.connect_box)
        box_menu.addAction(act)
        act = QAction(self.tr("Fax journal..."), self)
        act.triggered.connect(self.open_journal)
        box_menu.addAction(act)
        act = QAction(self.tr("Send log..."), self)
        act.triggered.connect(self.open_history)
        box_menu.addAction(act)
        settings_action = QAction(self.tr("&Settings..."), self, shortcut=QKeySequence.StandardKey.Preferences)
        settings_action.triggered.connect(self.open_settings)
        self.menuBar().addAction(settings_action)
        help_menu = self.menuBar().addMenu(self.tr("&Help"))
        act = QAction(self.tr("About fritzfax"), self)
        act.triggered.connect(self.about)
        help_menu.addAction(act)

    # --- settings -> widgets --------------------------------------------------------------
    def _update_hint_color(self):
        style = f"color: {theme.hint_color(QApplication.instance()).name()};"
        self.to_hint.setStyleSheet(style)
        self.from_hint.setStyleSheet(style)

    def changeEvent(self, event):
        # theme "system": the desktop may switch between light and dark while fritzfax runs
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.PaletteChange):
            self._update_hint_color()
        super().changeEvent(event)

    def _apply_settings(self, old: config.Settings | None = None):
        """Put the settings into the form. After a settings change (`old` = the settings before), a
        field only follows the new default if the user left it at the old default: what was chosen
        for this fax stays."""
        s = self.settings
        self._update_hint_color()

        def untouched(current, before):
            return old is None or current == before

        if untouched(self.from_name_edit.text(), old.sender_name if old else None) or \
                not self.from_name_edit.text():
            self.from_name_edit.setText(s.sender_name)
        mode = "letter" if self.mode_letter.isChecked() else "document"
        if untouched(mode, old.default_mode if old else None):
            (self.mode_letter if s.default_mode == "letter" else self.mode_document).setChecked(True)
        if untouched(self.cover_check.isChecked(), old.cover if old else None):
            self.cover_check.setChecked(s.cover)
        if untouched(self.header_combo.currentData(), old.header_mode if old else None):
            self.header_combo.setCurrentIndex(max(0, self.header_combo.findData(s.header_mode)))
        self._fill_sender_numbers()
        self._mode_changed()

    def _fill_sender_numbers(self):
        current = self.from_number_combo.currentData() or self.settings.sender_number
        numbers = self.box_info.src_numbers if self.box_info else ([self.settings.sender_number]
                                                                   if self.settings.sender_number else [])
        self.from_number_combo.blockSignals(True)
        self.from_number_combo.clear()
        for n in numbers:
            self.from_number_combo.addItem(box_number_display(n, self.box_location), n)
        idx = self.from_number_combo.findData(current)
        self.from_number_combo.setCurrentIndex(max(0, idx))
        self.from_number_combo.blockSignals(False)
        wanted = self.settings.sender_number
        if self.box_info and numbers and wanted and wanted not in numbers:
            self._set_hint(self.from_hint, self.tr("The sender number %1 from the settings is not a fax number of the box; "
                                           "the fax is sent from %2.").replace("%1", wanted)
                                   .replace("%2", self.from_number_combo.currentText()))
        else:
            self._set_hint(self.from_hint, "")

    def _mode_changed(self, *_):
        letter = self.mode_letter.isChecked()
        subject = letter or self.cover_check.isChecked()
        self.text_edit.setEnabled(letter)
        self.subject_edit.setEnabled(subject)
        # show only what the chosen content uses; the files get the room otherwise
        self.text_edit.setVisible(letter)
        self.subject_edit.setVisible(subject)
        self.schedule_preview()

    def _fill_number_menu(self):
        self.to_menu.clear()
        if self.box_info and self.box_info.phonebook:
            for book_id, entries in self.box_info.phonebook.items():
                sub = self.to_menu.addMenu(self.box_info.phonebook_names.get(book_id, self.tr("Phone book")))
                for e in entries or []:
                    a = sub.addAction(f"{e.get('name', '')}  ({e.get('number', '')})")
                    a.triggered.connect(lambda _=False, n=e.get("number", ""), nm=e.get("name", ""): self._add_number(n, nm))
        book = self.address_book(quiet=True)
        contacts = book.contacts() if book else []
        self.to_menu.addSection(self.tr("Address book"))
        for c in contacts[:MENU_CONTACTS]:
            a = self.to_menu.addAction(f"{c.name}  ({c.number})")
            a.triggered.connect(lambda _=False, c=c: self._add_number(c.number, c.name))
        self.to_menu.addAction(self.tr("Address book...") if len(contacts) <= MENU_CONTACTS else
                               self.tr("More in the address book...")).triggered.connect(self.open_address_book)
        self.to_menu.addAction(self.tr("Save recipient in address book...")).triggered.connect(self.save_recipient)
        recent = self.settings.recent_numbers
        if recent:
            self.to_menu.addSection(self.tr("Recent numbers"))
            for n in recent:
                a = self.to_menu.addAction(n)
                a.triggered.connect(lambda _=False, n=n: self._add_number(n))
        if not (self.box_info and self.box_info.phonebook):
            a = self.to_menu.addAction(self.tr("The phone books of the box appear here once connected"))
            a.setEnabled(False)

    # --- address book ----------------------------------------------------------------------
    def address_book(self, quiet: bool = False) -> AddressBook | None:
        """The address book (addressbook.db next to the send log); None if it cannot be opened."""
        if self._book is None or self._book.country_code != self.settings.country_code:
            try:
                self._book = AddressBook(country_code=self.settings.country_code)
            except (OSError, sqlite3.Error) as exc:
                if not quiet:
                    QMessageBox.warning(self, self.tr("Address book"), self.tr(
                        "The address book cannot be opened: %1").replace("%1", str(exc)))
                return None
        return self._book

    def open_address_book(self):
        from .addressbook_dialog import AddressBookDialog

        book = self.address_book()
        if book is None:
            return
        dlg = AddressBookDialog(book, self, choose=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for c in dlg.chosen:
                self._add_number(c.number, c.name)

    def save_recipient(self):
        """Keep the number in the recipient field (and the recipient name) in the address book."""
        from .addressbook_dialog import ContactDialog

        book = self.address_book()
        if book is None:
            return
        entries = [e.strip() for e in re.split(r"[,;\n]+", self.to_edit.text()) if e.strip()]
        ContactDialog(book, self, name=self.to_name_edit.text().strip(),
                      number=entries[0] if len(entries) == 1 else "").exec()

    def destinations(self) -> list[str]:
        """The numbers in the recipient field, names from the address book resolved; InvalidNumber if bad."""
        return resolve(self.to_edit.text(), self.settings.country_code, self.address_book(quiet=True))

    def _add_number(self, number: str, name: str = ""):
        text = self.to_edit.text().strip().rstrip(",")
        self.to_edit.setText(f"{text}, {number}" if text else number)
        if name and not self.to_name_edit.text():
            self.to_name_edit.setText(name)

    def _check_numbers(self):
        text = self.to_edit.text()
        if not text.strip():
            self._set_hint(self.to_hint, "")
            return
        try:
            numbers = self.destinations()
        except InvalidNumber as exc:
            self._set_hint(self.to_hint, connection.describe_number_error(exc))
            return
        hints = []
        local = [n for n in numbers if looks_local_without_area_code(n)]
        if local:
            hints.append(self.tr("Without area code the box asks for a confirmation (button on the box). "
                                 "Better dial with area code: %1").replace("%1", ", ".join(local)))
        abroad = [n for n in numbers if is_international(n)]
        if abroad:
            hints.append(self.tr("For international numbers the box asks for a confirmation (button on the "
                                 "box): %1").replace("%1", ", ".join(abroad)))
        self._set_hint(self.to_hint, "\n".join(hints))
        if self.cover_check.isChecked():
            self.schedule_preview()

    # --- files -------------------------------------------------------------------------------
    def choose_files(self):
        patterns = " ".join(f"*{e}" for e in FILE_FILTER_EXT)
        files, _ = QFileDialog.getOpenFileNames(self, self.tr("Add files"), str(Path.home()),
                                                self.tr("Documents and images (%1)").replace("%1", patterns))
        self.add_files(files)

    def add_files(self, paths):
        added = False
        for p in paths:
            path = Path(p)
            if not path.is_file():
                continue
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, {"path": str(path), "pages": ""})
            item.setToolTip(str(path))
            self.file_list.addItem(item)
            added = True
        if added:
            self.schedule_preview()

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self.schedule_preview()

    def _move_selected(self, delta):
        row = self.file_list.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < self.file_list.count():
            return
        item = self.file_list.takeItem(row)
        self.file_list.insertItem(new, item)
        self.file_list.setCurrentRow(new)
        self.schedule_preview()

    def _edit_pages(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        text, ok = QInputDialog.getText(self, self.tr("Pages"),
                                        self.tr("Pages to send (e.g. 1-2,4; empty = all):"), text=data["pages"])
        if ok:
            self._set_file_pages(item, text.strip())
            self.schedule_preview()

    @staticmethod
    def _set_file_pages(item, pages: str) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        data["pages"] = pages
        item.setData(Qt.ItemDataRole.UserRole, data)
        name = Path(data["path"]).name
        item.setText(f"{name}  [{pages}]" if pages else name)

    def file_specs(self) -> list[render.FileSpec]:
        specs = []
        for i in range(self.file_list.count()):
            data = self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            path = Path(data["path"])
            pages = None
            if data["pages"]:
                count = self._page_count(path)
                pages = render.parse_page_ranges(data["pages"], count)
            specs.append(render.FileSpec(path, pages))
        return specs

    @staticmethod
    def _page_count(path: Path) -> int | None:
        """None for office documents: their pages are only known after the conversion."""
        if path.suffix.lower() == ".pdf":
            from ..core.render.pdf import page_count

            return page_count(path)
        if path.suffix.lower() not in render.IMAGE_EXTENSIONS:
            return None
        with Image.open(path) as img:
            return getattr(img, "n_frames", 1)

    # --- document & preview -----------------------------------------------------------------------
    def sender_number(self) -> str:
        return self.from_number_combo.currentData() or self.settings.sender_number

    def header_number(self) -> str:
        """The fax number printed on the fax (header line, cover sheet): the one chosen in the settings,
        else the sending number, in international form when known."""
        return printed_number(self.settings.shown_number, self.sender_number(), self.box_location)

    def build_document(self) -> render.Document:
        s = self.settings
        letter = self.mode_letter.isChecked()
        cover = None
        if self.cover_check.isChecked():
            try:  # names from the address book as their numbers
                to_number = ", ".join(self.destinations())
            except InvalidNumber:
                to_number = self.to_edit.text()
            cover = render.Cover(to_name=self.to_name_edit.text(), to_number=to_number,
                                 from_name=self.from_name_edit.text(), from_number=self.header_number(),
                                 subject=self.subject_edit.text(), date=time.strftime("%d.%m.%Y %H:%M"),
                                 labels=render.cover_labels(self.language))
        return render.Document(files=self.file_specs(), text=self.text_edit.toPlainText() if letter else "",
                               subject=self.subject_edit.text() if letter else "", cover=cover,
                               header_mode=self.header_combo.currentData(), dither=s.dither, max_pages=s.max_pages,
                               font_file=s.font_file, font_size_pt=s.font_size, soffice=s.soffice)

    def schedule_preview(self, *_):
        if not self.closing:
            self.preview_timer.start()

    def update_preview(self):
        if self.closing:
            return
        self._render_generation += 1
        generation = self._render_generation
        try:
            doc = self.build_document()
        except ValueError as exc:
            self._preview_failed(generation, exc)
            return
        except Exception as exc:  # noqa: BLE001 - e.g. an unreadable file when counting pages
            self._preview_failed(generation, exc)
            return
        if not doc.files and not doc.text.strip() and not doc.subject.strip() and doc.cover is None:
            self.prepared = None
            self.thumbs.clear()
            self.page_view.clear()
            self.preview_label.setText(self.tr("Preview: add files or write a letter"))
            self.preview_pending = False
            if self.send_after_preview:
                self.send_after_preview = False
                self.send()  # reports that there is nothing to send
            return
        self.preview_label.setText(self.tr("Preview: rendering..."))
        self.preview_pending = True
        self.prepared_doc = doc
        name, number, shown = self.from_name_edit.text(), self.sender_number(), self.header_number()
        run_async(lambda: job.prepare(doc, sender_name=name, sender_number=number, display_number=shown),
                  lambda prepared: self._preview_ready(generation, prepared),
                  lambda exc: self._preview_failed(generation, exc))

    def _preview_ready(self, generation, prepared: job.Prepared):
        if generation != self._render_generation:
            return
        self.preview_pending = False
        self.prepared = prepared
        self.thumbs.clear()
        for i, page in enumerate(prepared.pages, 1):
            thumb = QPixmap.fromImage(to_qimage(page.convert("L").resize((96, 127), Image.Resampling.LANCZOS)))
            icon = QIcon(thumb)
            icon.addPixmap(thumb, QIcon.Mode.Selected)  # a frame marks the selection, the page keeps its colors
            item = QListWidgetItem(icon, str(i))
            self.thumbs.addItem(item)
        self.preview_label.setText(self.tr("Preview: %1 page(s), exactly as they will be sent").replace(
            "%1", str(prepared.page_count)))
        self.thumbs.setCurrentRow(0)
        if self.send_after_preview:
            self.send_after_preview = False
            self.send()

    def _preview_failed(self, generation, exc):
        if generation != self._render_generation:
            return
        self.preview_pending = False
        self.send_after_preview = False
        self.prepared = None
        self.thumbs.clear()
        self.page_view.clear()
        self.preview_label.setText(self.tr("Preview not possible: %1").replace("%1", connection.describe(exc)))

    def eventFilter(self, obj, event):
        area = getattr(self, "page_area", None)  # events arrive while the window is still being built
        if area is not None and obj is area.viewport() and event.type() == QEvent.Type.Resize:
            self._show_page(self.thumbs.currentRow())
            return False
        if (obj is self.thumbs and event.type() == QEvent.Type.KeyPress
                and (event.matches(QKeySequence.StandardKey.Delete) or event.key() == Qt.Key.Key_Delete)):
            self.remove_selected_pages()
            return True
        return super().eventFilter(obj, event)

    def thumbs_menu(self) -> QMenu:
        menu = QMenu(self)
        count = len(self.thumbs.selectedIndexes())
        action = menu.addAction(self.tr("Remove page(s) from the fax...") if count != 1
                                else self.tr("Remove page from the fax..."))
        action.setEnabled(count > 0)
        action.triggered.connect(self.remove_selected_pages)
        return menu

    def remove_selected_pages(self):
        rows = sorted({index.row() for index in self.thumbs.selectedIndexes()})
        if not rows:
            return
        prepared = self.prepared
        if (self.preview_pending or self.preview_timer.isActive() or prepared is None
                or len(prepared.origins) != self.thumbs.count()):
            # the thumbnails may not match the files any more (one was just moved, for example)
            QMessageBox.information(self, self.tr("Remove pages"), self.tr(
                "The preview is being updated. Please try again in a moment."))
            return
        chosen = [prepared.origins[r] for r in rows]
        if any(o.kind == "text" for o in chosen):
            QMessageBox.information(self, self.tr("Remove pages"), self.tr(
                "Pages of the letter text cannot be removed one by one. Shorten the text, or choose "
                "\"Document\" to send only the files."))
            return
        remove_cover = any(o.kind == "cover" for o in chosen)
        removed = {(o.file_index, o.source_page) for o in chosen if o.kind == "file"}
        remaining: dict[int, list[int]] = {}
        for o in prepared.origins:
            if o.kind == "file" and o.file_index in {i for i, _ in removed}:
                remaining.setdefault(o.file_index, [])
                if (o.file_index, o.source_page) not in removed:
                    remaining[o.file_index].append(o.source_page)
        emptied = sorted(i for i, pages in remaining.items() if not pages)

        if len(rows) == 1:
            question = self.tr("Remove page %1 from the fax?").replace("%1", str(rows[0] + 1))
        else:
            question = self.tr("Remove %1 pages from the fax?").replace("%1", str(len(rows)))
        if remove_cover:
            question += "\n\n" + self.tr("The cover sheet is turned off.")
        for i in emptied:
            name = Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole)["path"]).name
            question += "\n\n" + self.tr("%1 has no page left and is removed from the fax.").replace("%1", name)
        if QMessageBox.question(self, self.tr("Remove pages"), question) != QMessageBox.StandardButton.Yes:
            return

        for i, pages in remaining.items():
            if pages:
                self._set_file_pages(self.file_list.item(i), render.format_page_ranges(pages))
        for i in reversed(emptied):
            self.file_list.takeItem(i)
        if remove_cover:
            self.cover_check.setChecked(False)
        self.schedule_preview()

    def _show_page(self, row):
        if not self.prepared or not 0 <= row < len(self.prepared.pages):
            return
        page = self.prepared.pages[row].convert("L")
        # the whole page, as large as the view allows, like a sheet of paper on the table
        view = self.page_area.viewport().size()
        scale = min((view.width() - 24) / page.width, (view.height() - 24) / page.height)
        width = max(60, round(page.width * scale))
        height = max(80, round(page.height * scale))
        img = page.resize((width, height), Image.Resampling.LANCZOS)
        sheet = QPixmap(width + 2, height + 2)
        sheet.fill(QColor(theme.tokens(QApplication.instance())["border"]))
        painter = QPainter(sheet)
        painter.drawImage(1, 1, to_qimage(img))
        painter.end()
        self.page_view.setPixmap(sheet)

    def export_preview(self):
        if not self.prepared:
            QMessageBox.information(self, self.tr("Export"), self.tr("Nothing to export yet."))
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Export preview"), str(Path.home() / "fax.pdf"),
                                              self.tr("PDF (*.pdf);;SFF fax file (*.sff)"))
        if not path:
            return
        try:
            if path.lower().endswith(".sff"):
                Path(path).write_bytes(self.prepared.sff)
            else:
                pages = [p.convert("L") for p in self.prepared.pages]
                # the fax resolution (about 204 x 196 dpi) keeps the physical size of the page
                pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], dpi=FAX_DPI)
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Export"), self.tr("%1 could not be saved: %2").replace(
                "%1", path).replace("%2", exc.strerror or str(exc)))

    # --- box connection -------------------------------------------------------------------------
    def _show_idle_status(self):
        self._set_chip("offline")
        if self.settings.box_url.strip():
            self.status_label.setText(self.tr("Not connected"))
        else:
            self.status_label.setText(self.tr("No FRITZ!Box set up yet: enter its address in the settings."))

    @staticmethod
    def _set_hint(label: QLabel, text: str) -> None:
        """A hint line takes room only while it says something."""
        label.setText(text)
        label.setVisible(bool(text))

    def _set_chip(self, state: str) -> None:
        """The connection at a glance: offline, connecting, connected or error (colored by the theme)."""
        texts = {"offline": self.tr("Not connected"), "connecting": self.tr("Connecting..."),
                 "connected": self.tr("Connected"), "error": self.tr("Connection failed")}
        self.connection_chip.setText("\u25CF " + texts[state])
        self.connection_chip.setProperty("state", state)
        self.connection_chip.style().unpolish(self.connection_chip)
        self.connection_chip.style().polish(self.connection_chip)

    def ensure_client(self):
        if self.client is None:
            self.client = connection.make_client(self.settings, self, retry=self.password_rejected)
            if self.client is not None:
                self.password_rejected = False
        return self.client

    def auto_connect(self):
        """Connect when a box is set up: at start and after the connection settings changed."""
        if self.settings.box_url.strip() and self.client is None and not self.sending:
            self.connect_box()

    def connect_box(self, then=None):
        if self.connecting or self.sending:  # a second login would replace the session of the running one
            return
        client = self.ensure_client()
        if client is None:
            return
        self.connecting = True
        self._set_chip("connecting")
        self.status_label.setText(self.tr("Connecting to %1 ...").replace("%1", self.settings.box_url))

        def work():
            client.login()
            info = faxapi.FaxApi(client).read_info()
            return info, boxconfig.read_location(client)  # header line and how the numbers are shown

        def done(result):
            self.connecting = False
            if client is not self.client:  # the settings changed meanwhile: this login is outdated
                self.auto_connect()  # now with the new settings
                return
            if connection.store_pending_password(self.settings, self, self.config_path):
                self._save_settings()  # the password may be in them now
            self._set_chip("connected")
            info, self.box_location = result
            self.box_info = info
            if not self.from_name_edit.text():
                self.from_name_edit.setText(self.settings.sender_name or info.sender_long)
            self._fill_sender_numbers()
            self.schedule_preview()
            state = self.tr("fax ready") if info.configured else self.tr("fax NOT set up on the box")
            self.status_label.setText(self.tr("Connected to %1 as %2 - %3").replace("%1", self.settings.box_url)
                                      .replace("%2", client.username).replace("%3", state))
            pending, self.send_after_connect = self.send_after_connect, False
            if then:
                then()
            if pending and then != self.send:  # never twice
                self.send()

        def failed(exc):
            self.connecting = False
            if client is not self.client:  # outdated login, see done()
                self.auto_connect()
                return
            if isinstance(exc, UntrustedCertificate) and connection.ask_trust(self, exc):
                self.settings.cert_sha256 = exc.fingerprint
                self._save_settings()
                self.client = None
                self.connect_box(then)
                return
            self.send_after_connect = False  # the error below says why nothing was sent
            if isinstance(exc, (LoginFailed, LoginBlocked)):
                connection.forget_rejected_password(self.settings)
                self.password_rejected = True
            self.client = None
            self.status_label.setText(self.tr("Not connected"))
            self._set_chip("error")
            QMessageBox.warning(self, self.tr("Connection"), connection.describe(exc))

        run_async(work, done, failed)

    # --- sending ----------------------------------------------------------------------------------
    def send(self):
        if self.connecting and not self.sending:
            # a login is running (at start, for example): send once it is done, not twice
            self.send_after_connect = True
            self.status_label.setText(self.tr("Connecting to the box - the fax is sent once connected."))
            return
        if self.sending:
            return
        try:
            numbers = self.destinations()
        except InvalidNumber as exc:
            QMessageBox.warning(self, self.tr("Recipient"), connection.describe_number_error(exc))
            return
        if self.preview_timer.isActive() or self.preview_pending:
            # the preview is the fax: never send a stale one
            self.send_after_preview = True
            if self.preview_timer.isActive():
                self.preview_timer.stop()
                self.update_preview()
            return
        if not self.prepared:
            QMessageBox.warning(self, self.tr("Send fax"), self.tr("There is nothing to send yet. Add files or "
                                                                   "write a letter, and wait for the preview."))
            return
        if self.box_info is None:
            self.connect_box(then=self.send)
            return
        pages = self.prepared.page_count
        question = self.tr("Send %1 page(s) to %2?").replace("%1", str(pages)).replace("%2", ", ".join(numbers))
        if QMessageBox.question(self, self.tr("Send fax"), question) != QMessageBox.StandardButton.Yes:
            return
        if self.sending:  # another click was confirmed while this question was open
            return
        self._start_send(numbers)

    def _start_send(self, numbers):
        self.sending = True
        self.send_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.events = SendEvents()
        self.events.phase.connect(self._on_phase)
        self.events.status.connect(self._on_status)
        self.events.two_factor.connect(self._on_two_factor)
        s = self.settings
        data, name, number, client = self.prepared.sff, self.from_name_edit.text(), self.sender_number(), self.client
        self.send_client = client
        history = History() if s.keep_history else None
        content = job.describe(self.prepared_doc, sender_name=name, sender_number=number)
        images = self.prepared.pages
        events = self.events
        run_async(lambda: job.send_sff(client, data, numbers, name, number, events, history=history,
                                       content=content, pages_images=images, report_language=self.language,
                                       start_timeout=s.start_timeout, stall_timeout=s.stall_timeout,
                                       poll_interval=s.poll_interval),
                  lambda results: self._send_finished(numbers, results),
                  self._send_failed)

    def cancel(self):
        if self.events:
            self.events.cancel_event.set()
            self.status_label.setText(self.tr("Cancelling..."))

    def _on_phase(self, dest, phase, detail):
        texts = {"precheck": self.tr("%1: checking"), "confirm": self.tr("%1: waiting for confirmation on the box"),
                 "upload": self.tr("%1: uploading"), "sending": self.tr("%1: sending"),
                 "verify": self.tr("%1: collecting proof from the box"),
                 "done": self.tr("%1: sent"), "failed": self.tr("%1: failed"),
                 "overdue": self._overdue_text()}
        self.overdue_dest = dest if phase == "overdue" else None
        self.status_label.setText(texts.get(phase, "%1: " + phase).replace("%1", dest))
        if phase == "sending" and self.settings.animation and self.celebration is None:
            from .celebration import Celebration

            self.celebration = Celebration(self.centralWidget())
            self.celebration.start()
        if phase in ("upload", "done", "failed") and self.twofa_dialog:
            self.twofa_dialog.accept()
            self.twofa_dialog = None

    def _overdue_text(self) -> str:
        return self.tr("%1: no result yet, the box is still in the call - waiting for its result")

    def _on_status(self, dest, st):
        if st.status == 4 and dest == self.overdue_dest:
            self.status_label.setText(self._overdue_text().replace("%1", dest))
        elif st.status == 4:
            self.status_label.setText(self.tr("%1: transmitting (%2 %)").replace("%1", dest)
                                      .replace("%2", str(st.progress)))
        elif st.status in (2, 3):
            self.status_label.setText(self.tr("%1: dialing").replace("%1", dest))

    def _on_two_factor(self, dest, challenge):
        self.twofa_dialog = TwoFactorDialog(self.send_client, dest, challenge, self.events.cancel_event, self)
        self.twofa_dialog.show()

    def _finish_ui(self):
        self.sending = False
        self.send_client = None
        if self.reset_client_after_send:
            self.reset_client_after_send = False
            self.client = None
            self.box_info = None
        self.send_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.progress.setVisible(False)
        if self.twofa_dialog:
            self.twofa_dialog.close()
            self.twofa_dialog = None

    def _send_finished(self, numbers, results):
        self._finish_ui()
        self.last_results = results
        for n in numbers:
            self.settings.remember_number(n)
        self._save_settings()
        lines = []
        verdicts = {"verified": self.tr("verified (confirmed by the receiving fax, box copy identical)"),
                    "unverified": self.tr("NOT verified"), "failed": self.tr("failed")}
        for r in results:
            if r.ok:
                line = self.tr("%1: sent").replace("%1", r.number)
                if r.evidence is not None:
                    line += " - " + verdicts.get(r.evidence.verdict, r.evidence.verdict)
                    for problem in r.evidence.problems:
                        line += "\n    " + connection.describe_problem(problem)
                lines.append(line)
            elif r.error == "skipped":
                lines.append(self.tr("%1: skipped").replace("%1", r.number))
            elif isinstance(r.exception, FaxResultUnknown):
                lines.append(self.tr("%1: result UNKNOWN - %2").replace("%1", r.number)
                             .replace("%2", connection.describe(r.exception)))
            else:
                error = connection.describe(r.exception) if r.exception is not None else r.error
                lines.append(self.tr("%1: FAILED - %2").replace("%1", r.number).replace("%2", error))
        ok = all(r.ok for r in results)
        self._celebrate(ok and not any(r.evidence is not None and r.evidence.verdict == "failed" for r in results))
        self.status_label.setText(self.tr("Fax sent.") if ok else self.tr("Fax NOT (completely) sent."))
        box = QMessageBox.information if ok else QMessageBox.warning
        box(self, self.tr("Result"), "\n".join(lines))

    def _celebrate(self, ok: bool) -> None:
        """Tell the animation of this send how it ended (the fanfare only for a fax that went through)."""
        if self.celebration is not None:
            self.celebration.outcome(ok)
            self.celebration = None

    def _send_failed(self, exc):
        self._finish_ui()
        self._celebrate(False)
        self.status_label.setText(self.tr("Fax NOT sent."))
        QMessageBox.warning(self, self.tr("Send fax"), connection.describe(exc))

    # --- dialogs --------------------------------------------------------------------------------------
    def open_settings(self):
        from .settings_dialog import SettingsDialog

        old = replace(self.settings)
        old_connection = _connection_key(self.settings)
        dlg = SettingsDialog(self.settings, self, self.config_path)
        if not dlg.exec():
            return
        if _connection_key(self.settings) != old_connection:  # address, user, password, certificate ...
            if self.sending:
                self.reset_client_after_send = True  # the running send keeps its client
            else:
                self.client = None  # an outdated login still running is ignored (connect_box)
                self.box_info = None
                self._show_idle_status()
        self._apply_settings(old)
        if _connection_key(self.settings) != old_connection and not self.sending:
            self.auto_connect()

    def open_journal(self):
        from .journal_dialog import JournalDialog

        if self.connecting:
            QMessageBox.information(self, self.tr("Fax journal"), self.tr(
                "fritzfax is still connecting to the box. Please try again in a moment."))
            return
        if self.ensure_client() is None:
            return
        JournalDialog(self.client, self).exec()

    def report_load_problem(self):
        if self.settings.load_problem:
            QMessageBox.warning(self, self.tr("Settings"), self.tr(
                "The settings file could not be read, so fritzfax starts with default settings. The old file "
                "was kept as %1.").replace("%1", self.settings.load_backup))

    def _save_settings(self) -> bool:
        try:
            config.save(self.settings, self.config_path)
            return True
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Settings"), self.tr("The settings could not be saved: %1").replace(
                "%1", exc.strerror or str(exc)))
            return False

    def open_history(self):
        from .journal_dialog import HistoryDialog

        HistoryDialog(History(), self, book=self.address_book(quiet=True)).exec()

    def about(self):
        QMessageBox.about(self, self.tr("About fritzfax"),
                          self.tr("fritzfax %1\n\nSends faxes through an AVM FRITZ!Box using the box's own web "
                                  "interface protocol. No CAPI, no SIP account needed.").replace("%1", __version__))

    def closeEvent(self, event):
        if self.sending:
            answer = QMessageBox.question(self, self.tr("Quit"), self.tr(
                "A fax is being sent. If you quit now, fritzfax stops watching it (the box may still send it). "
                "Quit anyway?"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.events:
                self.events.cancel_event.set()
        self.closing = True
        self.preview_timer.stop()
        # renders and requests run in pool threads; let them end before Qt objects go away
        wait_all(30000)
        if self.client is not None and not self.sending:
            client = self.client
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - best effort
                pass
        event.accept()

    def open_url(self, url: str):
        QDesktopServices.openUrl(QUrl(url))
