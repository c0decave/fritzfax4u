import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakebox import FakeBox  # noqa: E402


@pytest.fixture
def fakebox():
    with FakeBox() as box:
        yield box


@pytest.fixture
def fakebox_factory(tmp_path):
    boxes = []

    def make(**kw):
        kw.setdefault("cert_dir", tmp_path) if kw.get("tls") else None
        box = FakeBox(**kw).__enter__()
        boxes.append(box)
        return box

    yield make
    for box in boxes:
        box.__exit__(None, None, None)


@pytest.fixture
def client_for():
    from fritzfax.core.session import BoxClient

    def make(box, **kw):
        kw.setdefault("username", box.state.username)
        kw.setdefault("password", box.state.password)
        return BoxClient(box.url, **kw)

    return make


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real user config/data dirs from tests: XDG on Linux, the home folder on macOS,
    APPDATA/LOCALAPPDATA on Windows (fritzfax/core/paths.py). Path.home is patched instead of HOME so
    that fontconfig keeps its cache."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False) if not os.environ.get("FRITZFAX_LIVE") else None


def pytest_configure(config):
    """Create the QApplication first so that QtPdf (used by the renderer) and the GUI share it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        return
    if QApplication.instance() is None:
        config._fritzfax_qapp = QApplication([])
