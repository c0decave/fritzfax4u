"""Where fritzfax keeps its files on Linux, macOS and Windows."""
from pathlib import Path

import pytest

from fritzfax.core import paths


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "APPDATA", "LOCALAPPDATA", "WINDIR"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_linux_follows_xdg(monkeypatch, home):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert paths.config_dir() == home / ".config" / "fritzfax"
    assert paths.data_dir() == home / ".local" / "share" / "fritzfax"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "d"))
    assert paths.config_dir() == home / "c" / "fritzfax" and paths.data_dir() == home / "d" / "fritzfax"


def test_macos_uses_application_support(monkeypatch, home):
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.config_dir() == paths.data_dir() == home / "Library" / "Application Support" / "fritzfax"
    assert "/System/Library/Fonts/Supplemental" in [d.as_posix() for d in paths.font_dirs()]


def test_windows_uses_appdata(monkeypatch, home):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(home / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "Local"))
    monkeypatch.setenv("WINDIR", str(home / "Windows"))
    assert paths.config_dir() == home / "Roaming" / "fritzfax"
    assert paths.data_dir() == home / "Local" / "fritzfax"
    assert home / "Windows" / "Fonts" in paths.font_dirs()


def test_windows_without_appdata_falls_back_to_the_home(monkeypatch, home):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    assert paths.config_dir() == home / "AppData" / "Roaming" / "fritzfax"
    assert paths.data_dir() == home / "AppData" / "Local" / "fritzfax"


def test_config_and_history_use_these_directories(monkeypatch, home):
    from fritzfax.core import config, history

    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert config.default_path() == home / "Library" / "Application Support" / "fritzfax" / "fritzfax.conf"
    assert history.default_paths()[0] == home / "Library" / "Application Support" / "fritzfax" / "history.db"


@pytest.mark.parametrize("platform, expected", [
    ("darwin", "/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ("win32", "LibreOffice/program/soffice.exe"),
])
def test_libreoffice_install_locations(monkeypatch, home, platform, expected):
    from fritzfax.core.render import office

    monkeypatch.setattr(paths.sys, "platform", platform)
    monkeypatch.setenv("PROGRAMFILES", str(home / "Programs"))
    found = [Path(p).as_posix() for p in office.platform_candidates()]
    assert any(p.endswith(expected) for p in found), found


def test_selftest_header_without_glibc(monkeypatch):
    # macOS has os.confstr but no CS_GNU_LIBC_VERSION: it raises ValueError
    from fritzfax import selftest

    def no_glibc(name):
        raise ValueError("unrecognized configuration name")

    monkeypatch.setattr(selftest.os, "confstr", no_glibc, raising=False)
    line = selftest.system_line()
    assert line.startswith("fritzfax ") and "glibc" not in line


def test_selftest_header_with_glibc(monkeypatch):
    from fritzfax import selftest

    monkeypatch.setattr(selftest.os, "confstr", lambda name: "glibc 2.41", raising=False)
    assert selftest.system_line().endswith("glibc 2.41")


def test_tests_never_write_into_the_real_folders(tmp_path):
    # conftest isolates every test; macOS and Windows ignore XDG (seen in CI 2026-10-26: 26 send log
    # entries in the runner's real ~/Library/Application Support)
    for folder in (paths.config_dir(), paths.data_dir()):
        assert folder.is_relative_to(tmp_path), folder
