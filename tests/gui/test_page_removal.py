"""Removing single pages in the preview: Del key or context menu, always after a question."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from fritzfax.core import config
from fritzfax.gui import workers
from fritzfax.gui.main_window import MainWindow
from tests_helpers import one_page_pdf


@pytest.fixture
def dialogs(monkeypatch, qtbot):
    """Records message boxes; dialogs.answer is what QMessageBox.question returns."""
    class Recorder(list):
        answer = QMessageBox.StandardButton.Yes

    seen = Recorder()

    def record(kind):
        def f(parent, title, text, *a, **k):
            seen.append((kind, text))
            return seen.answer if kind == "question" else None
        return f

    for kind in ("question", "information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, record(kind))
    yield seen
    workers.wait_all(10000)


def window(qtbot, tmp_path):
    w = MainWindow(config.Settings(box_url="http://127.0.0.1:9"), tmp_path / "f.conf")
    qtbot.addWidget(w)
    return w


def wait_pages(qtbot, w, n):
    qtbot.waitUntil(lambda: not w.preview_pending and not w.preview_timer.isActive() and w.prepared is not None
                    and w.prepared.page_count == n and w.thumbs.count() == n, timeout=20000)


def with_pdf(qtbot, w, tmp_path, pages, name="a.pdf"):
    w.add_files([str(one_page_pdf(tmp_path / name, pages=pages))])


def select(w, *rows):
    w.thumbs.clearSelection()
    for r in rows:
        w.thumbs.item(r).setSelected(True)
    w.thumbs.setCurrentRow(rows[0], w.thumbs.selectionCommand(w.thumbs.model().index(rows[0], 0)))
    for r in rows:
        w.thumbs.item(r).setSelected(True)


def pages_of(w, row=0):
    return w.file_list.item(row).data(Qt.ItemDataRole.UserRole)["pages"]


# --- happy path ---------------------------------------------------------------------------------------

def test_delete_key_removes_the_selected_page_after_asking(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 3)
    wait_pages(qtbot, w, 3)
    select(w, 1)
    w.thumbs.setFocus()
    QTest.keyClick(w.thumbs, Qt.Key.Key_Delete)
    assert [k for k, _ in dialogs] == ["question"] and "page 2" in dialogs[0][1]
    assert pages_of(w) == "1,3"
    assert "a.pdf  [1,3]" == w.file_list.item(0).text()
    wait_pages(qtbot, w, 2)


def test_context_menu_offers_the_removal(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 3)
    wait_pages(qtbot, w, 3)
    select(w, 0)
    menu = w.thumbs_menu()
    (action,) = [a for a in menu.actions() if "Remove" in a.text()]
    assert action.isEnabled()
    action.trigger()
    assert pages_of(w) == "2-3"
    wait_pages(qtbot, w, 2)


def test_several_selected_pages_are_removed_at_once(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 4)
    wait_pages(qtbot, w, 4)
    select(w, 0, 2)
    w.remove_selected_pages()
    assert "2 pages" in dialogs[0][1]
    assert pages_of(w) == "2,4"
    wait_pages(qtbot, w, 2)


def test_pages_of_the_second_file_and_an_existing_selection(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 1, "a.pdf")
    with_pdf(qtbot, w, tmp_path, 5, "b.pdf")
    item = w.file_list.item(1)
    item.setData(Qt.ItemDataRole.UserRole, {"path": str(tmp_path / "b.pdf"), "pages": "2-4"})
    w.schedule_preview()
    wait_pages(qtbot, w, 4)
    select(w, 2)  # a.pdf p1, b.pdf p2, b.pdf p3, b.pdf p4 -> b.pdf page 3
    w.remove_selected_pages()
    assert pages_of(w, 0) == "" and pages_of(w, 1) == "2,4"
    wait_pages(qtbot, w, 3)


def test_removing_the_cover_sheet_turns_it_off(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 1)
    w.cover_check.setChecked(True)
    wait_pages(qtbot, w, 2)
    select(w, 0)
    w.remove_selected_pages()
    assert "cover sheet" in dialogs[0][1]
    assert not w.cover_check.isChecked()
    wait_pages(qtbot, w, 1)


# --- sad path -----------------------------------------------------------------------------------------

def test_no_means_nothing_is_removed(qtbot, tmp_path, dialogs):
    dialogs.answer = QMessageBox.StandardButton.No
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 3)
    wait_pages(qtbot, w, 3)
    select(w, 1)
    w.remove_selected_pages()
    assert pages_of(w) == "" and w.prepared.page_count == 3 and not w.preview_timer.isActive()


def test_letter_text_pages_are_not_removed_one_by_one(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    w.mode_letter.setChecked(True)
    w.text_edit.setPlainText("Hallo")
    with_pdf(qtbot, w, tmp_path, 1)
    wait_pages(qtbot, w, 2)
    select(w, 0)
    w.remove_selected_pages()
    assert [k for k, _ in dialogs] == ["information"] and "letter text" in dialogs[0][1]
    assert w.text_edit.toPlainText() == "Hallo" and w.prepared.page_count == 2


def test_nothing_is_removed_while_the_preview_is_being_updated(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 3)
    wait_pages(qtbot, w, 3)
    select(w, 1)
    w.schedule_preview()  # e.g. a file was just moved: the thumbnails may not match the files any more
    w.remove_selected_pages()
    assert [k for k, _ in dialogs] == ["information"] and pages_of(w) == ""


def test_without_selection_nothing_happens(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    w.remove_selected_pages()
    assert dialogs == []
    assert not [a for a in w.thumbs_menu().actions() if a.isEnabled()]


# --- edge cases ---------------------------------------------------------------------------------------

def test_removing_the_last_page_of_a_file_removes_the_file(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 1, "a.pdf")
    with_pdf(qtbot, w, tmp_path, 2, "b.pdf")
    wait_pages(qtbot, w, 3)
    select(w, 0)
    w.remove_selected_pages()
    assert "a.pdf" in dialogs[0][1] and "removed from the fax" in dialogs[0][1]
    assert w.file_list.count() == 1 and Path(w.file_list.item(0).data(Qt.ItemDataRole.UserRole)["path"]).name == "b.pdf"
    wait_pages(qtbot, w, 2)


def test_removing_every_page_empties_the_fax(qtbot, tmp_path, dialogs):
    w = window(qtbot, tmp_path)
    with_pdf(qtbot, w, tmp_path, 2)
    wait_pages(qtbot, w, 2)
    select(w, 0, 1)
    w.remove_selected_pages()
    assert w.file_list.count() == 0
    qtbot.waitUntil(lambda: w.prepared is None and w.thumbs.count() == 0, timeout=20000)


def test_office_file_pages_can_be_selected_without_opening_the_file(qtbot, tmp_path):
    # the page count of an office document is unknown before conversion; it must not be opened as image
    w = window(qtbot, tmp_path)
    doc = tmp_path / "brief.odt"
    doc.write_bytes(b"not really odt")
    w.add_files([str(doc)])
    w.preview_timer.stop()
    w.file_list.item(0).setData(Qt.ItemDataRole.UserRole, {"path": str(doc), "pages": "1,3"})
    (spec,) = w.file_specs()
    assert spec.pages == [0, 2]
