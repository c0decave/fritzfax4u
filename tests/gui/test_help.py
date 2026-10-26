"""(?) help buttons: every field whose meaning is not obvious explains itself, in English and German."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QToolTip

from fritzfax.core import config
from fritzfax.gui import helptip, i18n
from fritzfax.gui.main_window import MainWindow
from fritzfax.gui.settings_dialog import SettingsDialog

ROOT = Path(__file__).parent.parent.parent
TABLE = json.loads((ROOT / "tools" / "i18n_de.json").read_text(encoding="utf-8"))

# The fields that must carry a (?) - a help button that disappears is a regression.
SETTINGS_KEYS = {
    "box_url", "username", "keyring", "certificate", "timeout",
    "sender_name", "sender_number", "country_code",
    "mode", "cover", "header", "dither", "max_pages", "start_timeout", "stall_timeout",
    "soffice", "font",
    "box_identifier", "box_header", "box_sender", "box_email", "box_store", "box_send_only",
    "theme", "history", "animation",
}
WINDOW_KEYS = {"to_numbers", "to_name", "from_name", "from_number", "mode", "files", "cover", "header", "preview"}


@pytest.fixture
def settings_dialog(qtbot, tmp_path):
    d = SettingsDialog(config.Settings(), config_path=tmp_path / "f.conf")
    qtbot.addWidget(d)
    return d


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(config.Settings(), tmp_path / "f.conf")
    qtbot.addWidget(w)
    return w


# --- the button itself --------------------------------------------------------------------

def test_help_button_shows_a_question_mark_and_the_text_on_hover(qtbot):
    b = helptip.HelpButton("Explains the field.")
    qtbot.addWidget(b)
    assert b.text() == "?"
    assert b.help_text == "Explains the field."
    assert "Explains the field." in b.toolTip()
    assert b.accessibleDescription() == "Explains the field."


def test_clicking_the_help_button_shows_the_text(qtbot):
    b = helptip.HelpButton("Shown on click.")
    qtbot.addWidget(b)
    b.show()
    qtbot.waitExposed(b)
    QToolTip.hideText()
    qtbot.mouseClick(b, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(QToolTip.isVisible, timeout=3000)
    assert "Shown on click." in QToolTip.text()
    QToolTip.hideText()


def test_help_button_works_with_the_keyboard(qtbot):
    b = helptip.HelpButton("Shown on Space.")
    qtbot.addWidget(b)
    b.show()
    qtbot.waitExposed(b)
    QToolTip.hideText()
    b.setFocus()
    qtbot.keyClick(b, Qt.Key.Key_Space)
    qtbot.waitUntil(QToolTip.isVisible, timeout=3000)
    assert "Shown on Space." in QToolTip.text()
    QToolTip.hideText()


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_help_text_is_refused(qtbot, text):
    with pytest.raises(ValueError):
        helptip.HelpButton(text)


def test_help_text_is_shown_as_text_not_as_markup(qtbot):
    b = helptip.HelpButton("a <b>x</b> & y\nnext line")
    qtbot.addWidget(b)
    tip = b.toolTip()
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in tip  # escaped, so Qt shows the characters
    assert "<br>" in tip  # line breaks survive
    assert tip.startswith("<p>")  # rich text: Qt wraps long tooltips instead of one endless line


def test_with_help_puts_the_field_and_the_button_side_by_side(qtbot):
    owner = type("Owner", (), {})()
    edit = QLineEdit()
    row = helptip.with_help(owner, "x", edit, "Field help.")
    qtbot.addWidget(row)
    assert owner.help["x"].help_text == "Field help."
    assert edit.parent() is row and owner.help["x"].parent() is row
    assert edit.toolTip() == owner.help["x"].toolTip()  # hovering the field explains it too


def test_with_help_keeps_an_existing_field_tooltip(qtbot):
    owner = type("Owner", (), {})()
    edit = QLineEdit()
    edit.setToolTip("own tooltip")
    row = helptip.with_help(owner, "x", edit, "Field help.")
    qtbot.addWidget(row)
    assert edit.toolTip() == "own tooltip"


def test_the_same_key_twice_is_a_programming_error(qtbot):
    owner = type("Owner", (), {})()
    helptip.with_help(owner, "x", QLineEdit(), "a")
    with pytest.raises(ValueError):
        helptip.with_help(owner, "x", QLineEdit(), "b")


# --- where the buttons are ------------------------------------------------------------------

def test_settings_dialog_explains_every_unclear_field(settings_dialog):
    assert set(settings_dialog.help) == SETTINGS_KEYS


def test_main_window_explains_every_unclear_field(window):
    assert set(window.help) == WINDOW_KEYS


def test_help_buttons_are_visible_in_their_tab(qtbot, settings_dialog):
    settings_dialog.show()
    qtbot.waitExposed(settings_dialog)
    for i in range(settings_dialog.tabs.count()):
        settings_dialog.tabs.setCurrentIndex(i)
        page = settings_dialog.tabs.widget(i)
        for key, b in settings_dialog.help.items():
            if page.isAncestorOf(b):
                assert b.isVisible(), key


def test_every_help_text_is_a_sentence(settings_dialog, window):
    for owner in (settings_dialog, window):
        for key, b in owner.help.items():
            t = b.help_text
            assert len(t) >= 25 and t.rstrip()[-1] in ".)", (key, t)


def test_no_private_box_address_in_help_texts(settings_dialog):
    import re

    for key, b in settings_dialog.help.items():
        assert not re.search(r"\b(10|172|192)\.\d+\.\d+\.\d+\b", b.help_text), key


# --- German -------------------------------------------------------------------------------------

def test_help_texts_are_german_in_the_german_ui(qtbot, tmp_path):
    app = QApplication.instance()
    english = {}
    for cls in (SettingsDialog, MainWindow):
        w = cls(config.Settings(), config_path=tmp_path / "f.conf") if cls is SettingsDialog \
            else cls(config.Settings(), tmp_path / "f.conf")
        qtbot.addWidget(w)
        raw = helptip.texts(raw=True)
        english[cls] = {k: raw[k] for k in w.help}
    try:
        assert i18n.install(app, "de") == "de"
        for cls in (SettingsDialog, MainWindow):
            w = cls(config.Settings(), config_path=tmp_path / "f.conf") if cls is SettingsDialog \
                else cls(config.Settings(), tmp_path / "f.conf")
            qtbot.addWidget(w)
            for key, b in w.help.items():
                src = english[cls][key]
                assert src in TABLE, (key, src)
                assert b.help_text == helptip.fill(key, TABLE[src]), key
                assert b.help_text != src, key
    finally:
        i18n.install(app, "en")


def test_check_boxes_and_compact_rows_keep_the_question_mark_next_to_their_text(qtbot, window):
    window.show()
    qtbot.waitExposed(window)
    for key, field in (("cover", window.cover_check), ("mode", window.mode_letter)):
        b = window.help[key]
        gap = b.mapTo(window, b.rect().topLeft()).x() - field.mapTo(window, field.rect().topRight()).x()
        assert 0 <= gap < 40, (key, gap)


def test_files_help_sits_next_to_the_files_heading(window):
    b = window.help["files"]
    assert any(isinstance(w, type(window.preview_label)) and "PDF" in w.text()
               for w in b.parent().findChildren(type(window.preview_label)))


def test_help_button_looks_like_a_round_badge(qtbot):
    b = helptip.HelpButton("Badge.")
    qtbot.addWidget(b)
    assert "border-radius" in b.styleSheet() and "palette(" in b.styleSheet()  # follows the theme


def test_timeout_help_states_the_budgets_the_code_uses(qtbot, monkeypatch):
    from fritzfax.core import faxapi, job
    from fritzfax.gui import helptip

    text = helptip.texts()["stall_timeout"]
    assert f"{job.SECONDS_PER_PAGE} seconds per page" in text
    assert f"{faxapi.OVERDUE_GRACE / 60:g} more minutes" in text
    monkeypatch.setattr(job, "SECONDS_PER_PAGE", 45)
    monkeypatch.setattr(faxapi, "OVERDUE_GRACE", 600.0)
    text = helptip.texts()["stall_timeout"]
    assert "45 seconds per page" in text and "10 more minutes" in text


def test_history_help_names_the_folder_of_this_system(qtbot):
    from fritzfax.core import paths

    assert str(paths.data_dir()) in helptip.texts()["history"]
