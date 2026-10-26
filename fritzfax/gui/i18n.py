"""German/English UI. Source strings are English; German comes from i18n/fritzfax_de.qm."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

I18N_DIR = Path(__file__).parent / "i18n"
_installed: list[QTranslator] = []


def effective_language(setting: str) -> str:
    if setting in ("de", "en"):
        return setting
    env = os.environ.get("LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG")
    if env:
        return "de" if env.startswith("de") else "en"
    return "de" if QLocale.system().name().startswith("de") else "en"


def install(app: QCoreApplication, setting: str) -> str:
    """Install translators for the chosen language; returns 'de' or 'en'."""
    for tr in _installed:
        app.removeTranslator(tr)
    _installed.clear()
    lang = effective_language(setting)
    if lang == "de":
        ours = QTranslator(app)
        if ours.load(str(I18N_DIR / "fritzfax_de.qm")):
            app.installTranslator(ours)
            _installed.append(ours)
        qt = QTranslator(app)  # standard dialogs (OK/Cancel, file dialog)
        if qt.load("qtbase_de", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(qt)
            _installed.append(qt)
    return lang
