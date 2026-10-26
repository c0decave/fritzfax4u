"""Light/dark theme of the GUI."""
import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from fritzfax.core import config
from fritzfax.gui import theme


@pytest.fixture
def app(qapp):
    yield qapp
    theme.apply(qapp, "system")  # leave the shared QApplication as it was


def lightness(app, role=QPalette.ColorRole.Window):
    return app.palette().color(role).lightness()


def test_dark_theme(app):
    theme.apply(app, "dark")
    assert lightness(app) < 100
    assert lightness(app, QPalette.ColorRole.WindowText) > 180
    assert lightness(app, QPalette.ColorRole.Base) < 100
    assert theme.is_dark(app)


def test_light_theme(app):
    theme.apply(app, "light")
    assert lightness(app) > 180 and not theme.is_dark(app)


# "system" follows the desktop's light/dark preference with fritzfax's own design (operator 2026-10-26:
# "ein hübsches modernes interface"); the bare platform look was plain Fusion in the release package

@pytest.mark.parametrize("scheme, dark", [("Dark", True), ("Light", False)])
def test_system_follows_the_desktop_color_scheme(app, monkeypatch, scheme, dark):
    from PySide6.QtCore import Qt

    monkeypatch.setattr(theme, "desktop_scheme", lambda app: getattr(Qt.ColorScheme, scheme))
    theme.apply(app, "system")
    assert theme.is_dark(app) is dark


def test_system_without_a_desktop_preference_uses_the_start_palette(app, monkeypatch):
    from PySide6.QtCore import Qt

    monkeypatch.setattr(theme, "desktop_scheme", lambda app: Qt.ColorScheme.Unknown)
    start_dark = theme._start["palette"].color(QPalette.ColorRole.Window).lightness() < 128 \
        if theme._start else app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    theme.apply(app, "system")
    assert theme.is_dark(app) is start_dark


def test_unknown_theme_is_treated_as_system(app, monkeypatch):
    from PySide6.QtCore import Qt

    monkeypatch.setattr(theme, "desktop_scheme", lambda app: Qt.ColorScheme.Dark)
    theme.apply(app, "neon")
    assert theme.is_dark(app)


@pytest.mark.parametrize("name", ["light", "dark", "system"])
def test_every_theme_has_the_modern_style_sheet(app, name):
    theme.apply(app, name)
    sheet = app.styleSheet()
    tokens = theme.DARK if theme.is_dark(app) else theme.LIGHT
    assert "QPushButton#primary" in sheet and tokens["accent"] in sheet and "border-radius" in sheet


def test_light_and_dark_style_sheets_differ_and_are_readable(app):
    for tokens in (theme.LIGHT, theme.DARK):
        text, card = (theme.QColor(tokens[k]) for k in ("text", "card"))
        assert abs(text.lightness() - card.lightness()) > 150  # strong contrast for the main text
    assert theme.stylesheet(theme.LIGHT) != theme.stylesheet(theme.DARK)


def test_palette_highlight_is_the_accent(app):
    theme.apply(app, "dark")
    assert app.palette().color(QPalette.ColorRole.Highlight).name() == theme.DARK["accent"]


def test_hint_color_is_readable_in_both_themes(app):
    theme.apply(app, "dark")
    dark = theme.hint_color(app)
    theme.apply(app, "light")
    light = theme.hint_color(app)
    assert dark.lightness() > 130 and light.lightness() < 110


def test_settings_dialog_applies_theme(app, qtbot, tmp_path):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings()
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    dlg.theme_combo.setCurrentIndex(dlg.theme_combo.findData("dark"))
    dlg.save()
    assert s.theme == "dark" and config.load(tmp_path / "f.conf").theme == "dark"
    assert theme.is_dark(QApplication.instance())


@pytest.mark.parametrize("name", ["light", "dark"])
def test_check_marks_and_arrows_are_drawn_for_the_theme(app, name):
    # without them a styled check box, combo box or spin box shows no state and no arrow (2026-10-26)
    from pathlib import Path

    from PySide6.QtGui import QImage

    theme.apply(app, name)
    sheet = app.styleSheet()
    for rule in ("QCheckBox::indicator:checked", "QRadioButton::indicator:checked", "QComboBox::down-arrow",
                 "QSpinBox::up-arrow", "QSpinBox::down-arrow"):
        assert rule in sheet, rule
    paths = theme.icon_paths(theme.DARK if name == "dark" else theme.LIGHT)
    assert set(paths) == {"check", "dot", "up", "down"}
    for path in paths.values():
        assert Path(path).is_file() and not QImage(path).isNull()
        assert f"url({path})" in sheet


def test_tidy_puts_labels_on_the_line_of_their_field(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFormLayout, QGroupBox, QLineEdit, QWidget

    w = QWidget()
    qtbot.addWidget(w)
    box = QGroupBox("Box", w)
    form = QFormLayout(box)
    field = QLineEdit()
    form.addRow("Label:", field)
    theme.tidy(w)
    label = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()
    # QFormLayout puts a label at the top of its row; as high as the field, it is centered on its line
    assert label.minimumHeight() == field.sizeHint().height()
    assert label.alignment() & Qt.AlignmentFlag.AlignVCenter
    assert form.contentsMargins().top() == 0
