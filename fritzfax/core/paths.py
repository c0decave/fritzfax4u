"""Where fritzfax keeps its files, per platform.

Linux (and other Unix): XDG (~/.config/fritzfax, ~/.local/share/fritzfax), as before.
macOS: ~/Library/Application Support/fritzfax for both.
Windows: %APPDATA%\\fritzfax (settings) and %LOCALAPPDATA%\\fritzfax (send log).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

NAME = "fritzfax"


def _windows_dir(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / "AppData" / fallback)


def config_dir() -> Path:
    if sys.platform == "win32":
        return _windows_dir("APPDATA", "Roaming") / NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / NAME


def data_dir() -> Path:
    if sys.platform == "win32":
        return _windows_dir("LOCALAPPDATA", "Local") / NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / NAME


def font_dirs() -> list[Path]:
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR") or "C:/Windows")
        return [windir / "Fonts", _windows_dir("LOCALAPPDATA", "Local") / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                Path.home() / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local" / "share" / "fonts"]


def private_dir(path: Path) -> Path:
    """Create `path` (and missing parents); the directory itself is only for the own user (mode 700).

    It holds sent faxes, the send log and the address book. Windows: the user's AppData is private by
    its access rights already; chmod has no effect there."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def private_file(path: Path) -> Path:
    """Create `path` if missing; only the own user may read or write it (mode 600)."""
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return path
