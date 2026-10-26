"""Look of the GUI: a modern light and dark design.

Every theme uses Qt's Fusion style, a palette and a style sheet built from the same few colors
(LIGHT, DARK), so fritzfax looks the same on every desktop. "light" and "dark" choose the colors;
"system" follows the desktop's light/dark preference (and its changes while fritzfax runs), falling
back to the palette the platform gave the application when the desktop does not say.
"""
from __future__ import annotations

import atexit
import hashlib
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QFormLayout, QGroupBox, QWidget

__all__ = ["DARK", "LIGHT", "QColor", "apply", "dark_palette", "desktop_scheme", "hint_color", "icon_paths",
           "is_dark", "light_palette", "palette", "stylesheet", "tidy", "tokens"]

_start: dict = {}  # palette at the first apply(): decides "system" when the desktop has no preference
_current: dict = {"name": "system"}

LIGHT = {
    "window": "#f3f4f6", "card": "#ffffff", "base": "#ffffff", "border": "#d9dde3", "text": "#1f2328",
    "muted": "#5b6470", "accent": "#2563eb", "accent_hover": "#1d4ed8", "on_accent": "#ffffff",
    "canvas": "#e3e6ea", "success": "#1a7f37", "danger": "#c62828", "warning": "#a15c00",
    "selection": "#dbe7fd", "control": "#8a94a0",
}
DARK = {
    "window": "#16181b", "card": "#1f2226", "base": "#181a1e", "border": "#343941", "text": "#e6e8eb",
    "muted": "#9aa4ae", "accent": "#4c8dff", "accent_hover": "#6aa1ff", "on_accent": "#0b1220",
    "canvas": "#0f1113", "success": "#3fb950", "danger": "#ff6b6b", "warning": "#ffb454",
    "selection": "#1d3357", "control": "#7d8794",
}


def palette(tokens: dict[str, str]) -> QPalette:
    c = {k: QColor(v) for k, v in tokens.items()}
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: c["window"], QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["base"], QPalette.ColorRole.AlternateBase: c["card"],
        QPalette.ColorRole.ToolTipBase: c["card"], QPalette.ColorRole.ToolTipText: c["text"],
        QPalette.ColorRole.Text: c["text"], QPalette.ColorRole.Button: c["card"],
        QPalette.ColorRole.ButtonText: c["text"], QPalette.ColorRole.BrightText: c["danger"],
        QPalette.ColorRole.Link: c["accent"], QPalette.ColorRole.Highlight: c["accent"],
        QPalette.ColorRole.HighlightedText: c["on_accent"], QPalette.ColorRole.PlaceholderText: c["muted"],
        QPalette.ColorRole.Light: c["card"], QPalette.ColorRole.Midlight: c["border"],
        QPalette.ColorRole.Mid: c["border"], QPalette.ColorRole.Dark: c["canvas"],
        QPalette.ColorRole.Shadow: QColor("#000000"),
    }
    for role, color in roles.items():
        pal.setColor(role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c["muted"])
    return pal


def dark_palette() -> QPalette:
    return palette(DARK)


def light_palette() -> QPalette:
    return palette(LIGHT)


_icon_dir: list[Path] = []


def icon_paths(t: dict[str, str]) -> dict[str, str]:
    """Check mark, radio dot and arrows in the colors of `t`, drawn once per process as PNG files.

    A style sheet that styles check boxes, combo boxes and spin boxes replaces what Fusion draws, so
    it must bring these images itself; style sheets can only load them from files."""
    if not _icon_dir:
        _icon_dir.append(Path(tempfile.mkdtemp(prefix="fritzfax-ui-")))
        atexit.register(shutil.rmtree, _icon_dir[0], True)
    shapes = {
        "check": (t["on_accent"], [(8, 17), (14, 23), (25, 10)]),
        "dot": (t["on_accent"], None),
        "down": (t["muted"], [(9, 13), (16, 20), (23, 13)]),
        "up": (t["muted"], [(9, 19), (16, 12), (23, 19)]),
    }
    paths = {}
    for name, (color, points) in shapes.items():
        tag = hashlib.sha1(f"{name}{color}".encode()).hexdigest()[:10]
        path = _icon_dir[0] / f"{name}-{tag}.png"
        if not path.exists():
            img = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if points is None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawEllipse(QPointF(16, 16), 6.5, 6.5)
            else:
                pen = QPen(QColor(color), 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                           Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPolyline([QPointF(x, y) for x, y in points])
            painter.end()
            img.save(str(path), "PNG")
        paths[name] = path.as_posix()
    return paths


def tidy(widget: QWidget) -> None:
    """Labels on the line of their field, and the card padding of the style sheet as the only margin."""
    for form in widget.findChildren(QFormLayout):
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label is None or field is None or label.widget() is None:
                continue
            # QFormLayout puts the label at the top of the row: as high as its field it is centered
            target = field.widget() or field.layout()
            label.widget().setMinimumHeight(target.sizeHint().height())
            label.widget().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for box in widget.findChildren(QGroupBox):
        if box.layout() is not None:
            box.layout().setContentsMargins(0, 0, 0, 0)


def stylesheet(t: dict[str, str]) -> str:
    """The style sheet of the design; object names: primary (main button), thumbs, pageArea, chip."""
    i = icon_paths(t)
    return f"""
QMainWindow, QDialog {{ background: {t['window']}; }}
QMenuBar {{ background: {t['window']}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {t['selection']}; }}
QMenu {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t['accent']}; color: {t['on_accent']}; }}
QMenu::item:disabled {{ color: {t['muted']}; }}
QGroupBox {{
    background: {t['card']}; border: 1px solid {t['border']}; border-radius: 12px;
    margin-top: 4px; padding: 34px 14px 12px 14px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 14px; top: 14px; padding: 0;
    color: {t['muted']};
}}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{ font-weight: normal; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget, QTreeWidget {{
    background: {t['base']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 7px;
    padding: 5px 8px; selection-background-color: {t['accent']}; selection-color: {t['on_accent']};
    font-weight: normal;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QListWidget:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {t['window']}; color: {t['muted']};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({i['down']}); width: 12px; height: 12px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; background: transparent; width: 20px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({i['up']}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({i['down']}); width: 10px; height: 10px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {t['control']}; background: {t['base']};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({i['check']}); }}
QRadioButton::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({i['dot']}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {t['window']}; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QComboBox QAbstractItemView {{
    background: {t['card']}; border: 1px solid {t['border']}; selection-background-color: {t['accent']};
    selection-color: {t['on_accent']}; padding: 4px;
}}
QListWidget::item {{ padding: 4px 6px; border-radius: 5px; }}
QListWidget::item:selected {{ background: {t['selection']}; color: {t['text']}; }}
QPushButton, QToolButton#plain {{
    background: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 7px;
    padding: 6px 14px; font-weight: normal;
}}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['selection']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['window']}; }}
QPushButton#primary {{
    background: {t['accent']}; color: {t['on_accent']}; border: none; border-radius: 9px;
    padding: 9px 26px; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {t['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {t['border']}; color: {t['muted']}; }}
QTabWidget::pane {{
    background: {t['card']}; border: 1px solid {t['border']}; border-radius: 12px; top: -1px; padding: 8px;
}}
QTabBar::tab {{
    background: transparent; color: {t['muted']}; padding: 8px 14px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; }}
QTabBar::tab:hover {{ color: {t['text']}; }}
QListWidget#thumbs {{ background: {t['canvas']}; border: none; border-radius: 12px; padding: 8px; }}
QListWidget#thumbs::item {{
    color: {t['muted']}; background: transparent; border: 2px solid transparent; border-radius: 6px;
    padding: 4px; margin: 2px;
}}
QListWidget#thumbs::item:selected {{ border: 2px solid {t['accent']}; color: {t['text']}; }}
QScrollArea#pageArea {{ background: {t['canvas']}; border: none; border-radius: 12px; }}
QScrollArea#pageArea > QWidget > QWidget {{ background: {t['canvas']}; }}
QLabel#title {{ font-size: 15pt; font-weight: 600; color: {t['text']}; }}
QLabel#muted {{ color: {t['muted']}; }}
QLabel#chip {{
    border-radius: 11px; padding: 3px 10px; font-weight: 600; background: {t['card']};
    border: 1px solid {t['border']}; color: {t['muted']};
}}
QLabel#chip[state="connected"] {{ color: {t['success']}; border-color: {t['success']}; }}
QLabel#chip[state="connecting"] {{ color: {t['accent']}; border-color: {t['accent']}; }}
QLabel#chip[state="error"] {{ color: {t['danger']}; border-color: {t['danger']}; }}
QProgressBar {{
    background: {t['card']}; border: 1px solid {t['border']}; border-radius: 6px; text-align: center;
    min-height: 12px; max-height: 12px;
}}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 5px; }}
QToolTip {{
    background: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 6px;
    padding: 6px 8px;
}}
QSplitter::handle {{ background: transparent; width: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {t['border']}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; width: 0; height: 0;
}}
"""


def desktop_scheme(app: QApplication):
    """The desktop's light/dark preference (Qt.ColorScheme.Unknown when it does not tell)."""
    hints = app.styleHints()
    return hints.colorScheme() if hasattr(hints, "colorScheme") else Qt.ColorScheme.Unknown


def _color_scheme(app: QApplication, scheme) -> None:
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8: also tells the platform (window decorations)
        hints.setColorScheme(scheme)


def _follow_desktop(app: QApplication) -> None:
    """Re-apply "system" when the desktop switches between light and dark while fritzfax runs."""
    hints = app.styleHints()
    if _start.get("watching") or not hasattr(hints, "colorSchemeChanged"):
        return
    _start["watching"] = True
    hints.colorSchemeChanged.connect(lambda *_: _current["name"] in ("system", None) and apply(app, "system"))


def apply(app: QApplication, name: str) -> None:
    if not _start:
        _start["palette"] = QPalette(app.palette())
    if name not in ("light", "dark"):
        name = "system"
    _current["name"] = name
    if name == "system":
        scheme = desktop_scheme(app)
        if scheme == Qt.ColorScheme.Dark:
            dark = True
        elif scheme == Qt.ColorScheme.Light:
            dark = False
        else:
            dark = _start["palette"].color(QPalette.ColorRole.Window).lightness() < 128
        _color_scheme(app, Qt.ColorScheme.Unknown)
        _follow_desktop(app)
    else:
        dark = name == "dark"
        _color_scheme(app, Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    tokens = DARK if dark else LIGHT
    app.setStyle("Fusion")
    app.setPalette(palette(tokens))
    app.setStyleSheet(stylesheet(tokens))


def tokens(app: QApplication) -> dict[str, str]:
    return DARK if is_dark(app) else LIGHT


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def hint_color(app: QApplication) -> QColor:
    """Orange for hints, readable on the current background."""
    return QColor(tokens(app)["warning"])
