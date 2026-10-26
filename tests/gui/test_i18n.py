import importlib.util
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from fritzfax.core import config
from fritzfax.gui import i18n

ROOT = Path(__file__).parent.parent.parent
spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "tools" / "i18n.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_every_ui_string_has_a_german_translation():
    assert tool.missing() == []


def test_committed_ts_and_qm_are_up_to_date():
    assert tool.compiled_matches(), "run: tools/dev.sh run python tools/i18n.py build"


def test_placeholders_survive_translation():
    table = json.loads((ROOT / "tools" / "i18n_de.json").read_text(encoding="utf-8"))
    for src, de in table.items():
        for ph in ("%1", "%2"):
            assert (ph in src) == (ph in de), src


def test_no_germanized_it_terms():
    # operator rule: IT terms stay English (Timeout, not Zeitfenster; Backup, not Sicherung ...)
    banned = ["Zeitfenster", "Sicherung", "Vorlage", "Zeiger", "Warteschlange", "Zwischenspeicher",
              "Berechtigung", "Stellung", "Schale", "Momentaufnahme", "Lebenszeichen", "Leerlauf",
              "Sackgasse", "Abbau", "Sitzung", "Fingerabdruck", "Schlüsselbund", "Kopfzeile"]
    table = json.loads((ROOT / "tools" / "i18n_de.json").read_text(encoding="utf-8"))
    import re

    hits = [(src, de) for src, de in table.items() for b in banned
            if re.search(rf"\b{b}\b", de, re.IGNORECASE)]
    assert hits == []


@pytest.mark.parametrize("env, expected", [("de_DE.UTF-8", "de"), ("en_US.UTF-8", "en"), ("fr_FR", "en")])
def test_effective_language_from_environment(monkeypatch, env, expected):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", env)
    assert i18n.effective_language("auto") == expected
    assert i18n.effective_language("de") == "de"


def test_german_ui(qtbot, tmp_path):
    from fritzfax.gui.main_window import MainWindow

    app = QApplication.instance()
    try:
        assert i18n.install(app, "de") == "de"
        w = MainWindow(config.Settings(), tmp_path / "f.conf", "de")
        qtbot.addWidget(w)
        assert w.windowTitle() == "fritzfax - Faxen mit der FRITZ!Box"
        assert w.send_button.text() == "Fax senden"
        assert w.mode_document.text() == "Dokument: die Dateien sind das Fax"
    finally:
        i18n.install(app, "en")
    w2 = MainWindow(config.Settings(), tmp_path / "f.conf", "en")
    qtbot.addWidget(w2)
    assert w2.send_button.text() == "Send fax"


def test_german_texts_address_the_user_with_sie_throughout():
    import re

    table = json.loads((ROOT / "tools" / "i18n_de.json").read_text(encoding="utf-8"))
    informal = re.compile(r"\b(du|dich|dir|dein|deine|deinen|deinem|deiner|deines)\b", re.IGNORECASE)
    assert [de for de in table.values() if informal.search(de)] == []
