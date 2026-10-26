"""tools/collect_licenses.py: the license texts of the bundled Python packages (Windows/macOS packages)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("collect_licenses", ROOT / "tools" / "collect_licenses.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


def test_collects_the_licenses_of_the_installed_packages(tmp_path):
    names = cl.collect(tmp_path, ["Pillow", "pytest"])
    files = {p.name for p in tmp_path.iterdir()}
    assert files == set(names) and files
    assert any(n.lower().startswith("pillow") for n in files) and any(n.lower().startswith("pytest") for n in files)
    assert all((tmp_path / n).stat().st_size > 0 for n in files)


def test_qt_for_python_gets_the_lgpl_texts(tmp_path):
    # its wheels carry no license files (measured with PySide6 6.11.1): without this, no Qt license
    names = cl.collect(tmp_path, ["PySide6", "shiboken6"])
    assert {"LGPL-3.0.txt", "GPL-3.0.txt", "Qt-PySide6-NOTICE.txt"} <= set(names)
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (tmp_path / "LGPL-3.0.txt").read_text()
    assert len(names) == len(set(names))  # shared texts only once


def test_all_installed_packages(tmp_path):
    assert "Pillow" in cl.installed() or "pillow" in cl.installed()


def test_unknown_package_is_an_error(tmp_path):
    import pytest

    with pytest.raises(SystemExit, match="no-such-package"):
        cl.collect(tmp_path, ["no-such-package"])


def test_package_without_license_file_is_an_error(tmp_path, monkeypatch):
    import pytest

    class Dist:
        files = []
        metadata = {"Name": "bare", "Version": "1"}

    monkeypatch.setattr(cl.metadata, "distribution", lambda name: Dist())
    with pytest.raises(SystemExit, match="bare"):
        cl.collect(tmp_path, ["bare"])


def test_qt_for_python_keeps_its_own_files_and_gets_the_texts(tmp_path, monkeypatch):
    # Windows wheels carry license files of their own (CI 2026-10-26): both go into the package
    own = tmp_path / "src" / "PySide6-6.11.1.dist-info" / "LICENSE.LGPL3.txt"
    own.parent.mkdir(parents=True)
    own.write_text("own text")

    class File:
        def __str__(self):
            return "PySide6-6.11.1.dist-info/LICENSE.LGPL3.txt"

        def locate(self):
            return own

    class Dist:
        files = [File()]
        metadata = {"Name": "PySide6", "Version": "6.11.1"}

    monkeypatch.setattr(cl.metadata, "distribution", lambda name: Dist())
    names = cl.collect(tmp_path / "out", ["PySide6"])
    assert "PySide6-6.11.1-LICENSE.LGPL3.txt" in names and "Qt-PySide6-NOTICE.txt" in names
