import os
import subprocess
import sys
from pathlib import Path

import pytest

from fritzfax import __main__ as launcher
from fritzfax import cli

ROOT = Path(__file__).parent.parent.parent


@pytest.mark.parametrize("argv0, args, expected", [
    ("fritzfax", [], "gui"),
    ("fritzfax", ["gui", "a.pdf"], "gui"),
    ("fritzfax-gui", ["a.pdf"], "gui"),
    ("/opt/x/fritzfax", ["send", "--to", "1"], "cli"),
    ("fritzfax", ["--version"], "cli"),
    # Windows: a windowed fritzfax-gui.exe next to the console fritzfax.exe
    ("C:\\Program Files\\fritzfax\\fritzfax-gui.exe", ["a.pdf"], "gui"),
    ("fritzfax-gui.EXE", [], "gui"),
    ("C:\\fritzfax\\fritzfax.exe", ["send", "--to", "1"], "cli"),
])
def test_launcher_dispatch(argv0, args, expected):
    assert launcher.choose(argv0, args)[0] == expected


def test_launcher_strips_gui_word():
    assert launcher.choose("fritzfax", ["gui", "a.pdf"]) == ("gui", ["a.pdf"])


def test_selftest_passes_here(capsys):
    assert cli.main(["selftest"]) == 0
    out = capsys.readouterr().out
    for check in ("Qt", "PDF rendering", "fax encoding", "archive comparison", "font"):
        assert check in out
    assert "FAIL" not in out


def test_selftest_without_libreoffice_only_warns(capsys, monkeypatch):
    from fritzfax.core.render import office

    monkeypatch.setattr(office, "find_soffice", lambda override="": None)
    assert cli.main(["selftest"]) == 0
    out = capsys.readouterr().out
    assert "WARN" in out and "LibreOffice" in out


def test_selftest_with_gui(capsys):
    assert cli.main(["selftest", "--gui"]) == 0
    out = capsys.readouterr().out
    assert "OK    GUI: main window opened, preview of 1 page shown, closed" in out


def test_module_entry_point_runs():
    proc = subprocess.run([sys.executable, "-m", "fritzfax", "--version"], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=60)
    assert proc.returncode == 0 and proc.stdout.startswith("fritzfax ")



def test_selftest_gui_when_the_preview_timer_fires_late(capsys, monkeypatch):
    # seen on Debian 12 under Xvfb (2 of 5 runs): the window's own preview timer fired right after
    # the selftest page was shown and replaced it by the window's real (empty) document
    from fritzfax.gui.main_window import MainWindow

    original = MainWindow._preview_ready

    def ready_then_timer(self, generation, prepared):
        original(self, generation, prepared)
        self.update_preview()  # what the timer does when it fires now

    monkeypatch.setattr(MainWindow, "_preview_ready", ready_then_timer)
    assert cli.main(["selftest", "--gui"]) == 0
    assert "OK    GUI: main window opened, preview of 1 page shown, closed" in capsys.readouterr().out


def test_selftest_cleans_up_its_temp_directory(capsys, monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert cli.main(["selftest"]) == 0
    assert list(tmp_path.glob("fritzfax-selftest-*")) == []
