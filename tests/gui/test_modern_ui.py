"""The modern main window: what is shown when, the send button, the connection chip, the page view."""
import pytest
from PySide6.QtWidgets import QMessageBox

from fritzfax.core import config
from fritzfax.gui import workers
from fritzfax.gui.main_window import MainWindow
from tests_helpers import one_page_pdf


@pytest.fixture
def quiet(monkeypatch, qtbot):
    shown = []
    for kind in ("question", "information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, kind=kind, **k: shown.append(kind)
                            or QMessageBox.StandardButton.Yes)
    yield shown
    workers.wait_all(10000)


def window(qtbot, tmp_path, box=None, url=None):
    s = config.Settings(box_url=url if url is not None else (box.url if box else "http://127.0.0.1:9"),
                        username=box.state.username if box else "", poll_interval=0.02)
    s.password = box.state.password if box else "x"
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    return w


# --- only what is needed is shown ----------------------------------------------------------------

def test_document_mode_hides_the_letter_fields(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    assert w.mode_document.isChecked()
    assert w.text_edit.isHidden() and w.subject_edit.isHidden()


def test_letter_mode_shows_text_and_subject(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.mode_letter.setChecked(True)
    assert not w.text_edit.isHidden() and not w.subject_edit.isHidden()


def test_cover_sheet_shows_the_subject_only(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.cover_check.setChecked(True)
    assert not w.subject_edit.isHidden() and w.text_edit.isHidden()


# --- the main action stands out ------------------------------------------------------------------

def test_send_is_the_primary_button(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    assert w.send_button.objectName() == "primary"


def test_settings_save_is_the_primary_button(qtbot, tmp_path):
    from fritzfax.gui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(config.Settings(), None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    assert [b.objectName() for b in dlg.findChildren(type(dlg.save_button)) if b.objectName() == "primary"]
    assert dlg.save_button.objectName() == "primary"


# --- connection chip -----------------------------------------------------------------------------

def test_chip_without_box_address(qtbot, tmp_path):
    w = window(qtbot, tmp_path, url="")
    assert w.connection_chip.property("state") == "offline"


def test_chip_while_connecting_and_when_connected(qtbot, tmp_path, fakebox, quiet):
    w = window(qtbot, tmp_path, fakebox)
    assert w.connection_chip.property("state") == "offline"
    w.connect_box()
    assert w.connection_chip.property("state") == "connecting"
    qtbot.waitUntil(lambda: w.connection_chip.property("state") == "connected", timeout=15000)
    assert "Connected" in w.connection_chip.text()


def test_chip_after_a_failed_connection(qtbot, tmp_path, quiet):
    w = window(qtbot, tmp_path)
    w.connect_box()
    qtbot.waitUntil(lambda: w.connection_chip.property("state") == "error", timeout=30000)
    assert quiet == ["warning"]


def test_chip_goes_offline_when_the_connection_settings_change(qtbot, tmp_path, fakebox, quiet, monkeypatch):
    from fritzfax.gui import settings_dialog

    w = window(qtbot, tmp_path, fakebox)
    w.connect_box()
    qtbot.waitUntil(lambda: w.connection_chip.property("state") == "connected", timeout=15000)
    monkeypatch.setattr(w, "auto_connect", lambda: None)

    def exec_(dlg):
        dlg.timeout_spin.setValue(31)
        dlg.save()
        return dlg.result()

    monkeypatch.setattr(settings_dialog.SettingsDialog, "exec", exec_)
    w.open_settings()
    assert w.connection_chip.property("state") == "offline"


# --- the page view shows the whole page --------------------------------------------------------------

def test_page_view_fits_the_whole_page(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None and w.thumbs.count() == 1, timeout=20000)
    qtbot.waitUntil(lambda: not w.page_view.pixmap().isNull(), timeout=5000)
    view = w.page_area.viewport().size()
    pix = w.page_view.pixmap()
    assert pix.height() <= view.height() and pix.width() <= view.width()
    assert pix.height() >= view.height() * 0.8 or pix.width() >= view.width() * 0.8  # and uses the space


def test_page_view_follows_a_resize(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None and not w.page_view.pixmap().isNull(), timeout=20000)
    before = w.page_view.pixmap().height()
    w.resize(1200, 600)
    qtbot.waitUntil(lambda: w.page_view.pixmap().height() < before, timeout=5000)


def test_selected_thumbnail_keeps_the_page_colors(qtbot, tmp_path):
    from PySide6.QtGui import QIcon

    w = window(qtbot, tmp_path)
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.thumbs.count() == 1, timeout=20000)
    icon = w.thumbs.item(0).icon()
    normal = icon.pixmap(96, 128, QIcon.Mode.Normal).toImage()
    selected = icon.pixmap(96, 128, QIcon.Mode.Selected).toImage()
    assert normal == selected  # a frame marks the selection, the page itself is not tinted


def test_empty_hints_take_no_room(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    assert w.to_hint.isHidden() and w.from_hint.isHidden()
    w.to_edit.setText("123456")  # no area code: a hint
    assert not w.to_hint.isHidden() and w.to_hint.text()
    w.to_edit.setText("0301234567")
    assert w.to_hint.isHidden()


def test_thumbnail_strip_scrolls_only_sideways(qtbot, tmp_path):
    from PySide6.QtCore import Qt

    w = window(qtbot, tmp_path)
    assert w.thumbs.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
