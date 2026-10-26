"""Fallback libraries for the frozen release on minimal systems.

PyInstaller leaves out libraries it treats as part of every system (libGL, libEGL, libxcb ...),
but container images and servers often lack them, and then Qt cannot even be imported. The release
ships them in <program>/fallback-libs. Before Qt is imported, every library the system cannot load
is loaded from there by full path; the dynamic loader then reuses it (matched by soname) when Qt
needs it. A system that has a library always keeps its own copy (its GL drivers, for example).

The frozen binary also puts its own library directory on LD_LIBRARY_PATH. Programs of the system that
fritzfax starts (fc-match, the PDF viewer, LibreOffice) must not inherit it: they would load bundled
libraries older than the system's, for example a fontconfig that cannot read the system configuration.
"""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

DIR_NAME = "fallback-libs"
SYSTEM_FONTS_CONF = Path("/etc/fonts/fonts.conf")
LOADED: list[str] = []  # names loaded from the fallback directory (shown by the self test)
_handles = []  # keep the libraries loaded


def fallback_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / DIR_NAME


def preload(directory: Path, load: Callable[[str], object] = ctypes.CDLL) -> list[str]:
    """Load from directory what the system lacks; returns the names loaded from directory."""
    if not directory.is_dir():
        return []
    pending = []
    for lib in sorted(p for p in directory.iterdir() if ".so" in p.name):
        try:
            _handles.append(load(lib.name))
        except OSError:
            pending.append(lib)
    used = []
    progress = True
    while pending and progress:  # dependencies first: retry until nothing more can be loaded
        progress = False
        for lib in list(pending):
            try:
                _handles.append(load(str(lib)))
            except OSError:
                continue
            pending.remove(lib)
            used.append(lib.name)
            progress = True
    LOADED.extend(used)
    return used


def system_lacks(soname: str) -> bool:
    """True if this process had to load `soname` from the fallback directory."""
    return soname in LOADED


def _inside(path: str, root: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(root)
    except OSError:
        return False


def system_env(env: Mapping[str, str] | None = None, bundle: str | None = "") -> dict[str, str]:
    """Environment for a program of the system: the library path without the frozen binary's own.

    `bundle` is the frozen binary's library directory (default: sys._MEIPASS, None when not frozen).
    PyInstaller keeps the original library path in LD_LIBRARY_PATH_ORIG."""
    env = dict(os.environ if env is None else env)
    if bundle == "":
        bundle = getattr(sys, "_MEIPASS", None)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    if bundle and "LD_LIBRARY_PATH" in env:
        root = Path(bundle).resolve()
        keep = [p for p in env["LD_LIBRARY_PATH"].split(os.pathsep) if p and not _inside(p, root)]
        if keep:
            env["LD_LIBRARY_PATH"] = os.pathsep.join(keep)
        else:
            del env["LD_LIBRARY_PATH"]
    return env


def restore_system_env() -> None:
    """Give every child process the system's library path.

    The dynamic loader read LD_LIBRARY_PATH when this process started, so this process keeps
    finding its own libraries; only programs started from now on see the system's path."""
    wanted = system_env()
    for key in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG"):
        if key in wanted:
            os.environ[key] = wanted[key]
        else:
            os.environ.pop(key, None)


def mapped_path(soname: str, maps: str | None = None) -> str | None:
    """File of the loaded library `soname` (from /proc/self/maps), None if it is not loaded."""
    if maps is None:
        try:
            maps = Path("/proc/self/maps").read_text()
        except OSError:
            return None
    for line in maps.splitlines():
        path = line.split(maxsplit=5)[5] if len(line.split(maxsplit=5)) == 6 else ""
        if Path(path).name.startswith(soname):
            return path
    return None


def check_system_libraries(maps: str | None = None, env: Mapping[str, str] | None = None,
                           bundle: str | None = "") -> str:
    """Self test of the frozen binary: system programs get the system's libraries, and fontconfig is
    the system's own when there is one. Raises RuntimeError otherwise."""
    if bundle == "":
        bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return "not a frozen build"
    root = Path(bundle).resolve()
    env = os.environ if env is None else env
    leaked = [p for p in env.get("LD_LIBRARY_PATH", "").split(os.pathsep) if p and _inside(p, root)]
    if leaked:
        raise RuntimeError(f"programs started by fritzfax would inherit the bundled library path {leaked[0]}")
    fc = mapped_path("libfontconfig.so.1", maps)
    if fc is None:
        return "child processes get the system library path; fontconfig not loaded"
    if _inside(fc, root):
        raise RuntimeError(f"the bundled fontconfig {fc} is used instead of the system's")
    where = "fontconfig from fallback-libs" if Path(fc).parent.name == DIR_NAME else "fontconfig of the system"
    conf = env.get("FONTCONFIG_FILE", "")
    if conf and Path(conf).parent.name == DIR_NAME:
        where += " with fritzfax's fonts.conf (the system has none)"
    return f"child processes get the system library path; {where} ({fc})"


def use_fallback_fonts_conf(directory: Path, env=None, system_conf: Path | None = None) -> bool:
    """Point fontconfig at the minimal fonts.conf shipped in `directory` when the system has none
    (otherwise every start prints "Fontconfig error: Cannot load default config file")."""
    env = os.environ if env is None else env
    system_conf = SYSTEM_FONTS_CONF if system_conf is None else system_conf
    conf = directory / "fonts.conf"
    if "FONTCONFIG_FILE" in env or "FONTCONFIG_PATH" in env or system_conf.exists() or not conf.is_file():
        return False
    env["FONTCONFIG_FILE"] = str(conf)
    return True


def preload_frozen() -> list[str]:
    directory = fallback_dir()
    if not directory:
        return []
    use_fallback_fonts_conf(directory)
    return preload(directory)
