"""fritzfax.syslibs: load fallback libraries only for what the system cannot provide."""
import os
import sys
from pathlib import Path

import pytest

from fritzfax import syslibs


class Loader:
    """Fake dlopen: `system` names load by soname, fallback files load if their deps are loaded."""

    def __init__(self, system=(), deps=None, broken=()):
        self.system, self.deps, self.broken, self.loaded = set(system), deps or {}, set(broken), []

    def __call__(self, name):
        p = Path(name)
        if p.is_absolute():
            missing = [d for d in self.deps.get(p.name, ()) if d not in self.loaded and d not in self.system]
            if missing or p.name in self.broken:
                raise OSError(f"{p.name}: cannot load ({missing})")
        elif name not in self.system:
            raise OSError(f"{name}: cannot open shared object file")
        self.loaded.append(p.name)
        return object()


@pytest.fixture
def fallback(tmp_path):
    for name in ("libEGL.so.1", "libGL.so.1", "libGLX.so.0", "libGLdispatch.so.0", "libxcb.so.1", "README"):
        (tmp_path / name).write_bytes(b"")
    return tmp_path


def test_system_libraries_win(fallback):
    names = {"libEGL.so.1", "libGL.so.1", "libGLX.so.0", "libGLdispatch.so.0", "libxcb.so.1"}
    assert syslibs.preload(fallback, load=Loader(system=names)) == []


def test_missing_libraries_come_from_fallback_in_dependency_order(fallback):
    deps = {"libGL.so.1": ["libGLX.so.0", "libGLdispatch.so.0"], "libGLX.so.0": ["libGLdispatch.so.0", "libxcb.so.1"],
            "libEGL.so.1": ["libGLdispatch.so.0"]}
    load = Loader(system=["libxcb.so.1"], deps=deps)
    used = syslibs.preload(fallback, load=load)
    assert sorted(used) == ["libEGL.so.1", "libGL.so.1", "libGLX.so.0", "libGLdispatch.so.0"]
    assert load.loaded.index("libGLdispatch.so.0") < load.loaded.index("libGL.so.1")


def test_unloadable_fallback_is_skipped(fallback):
    load = Loader(system=["libGLdispatch.so.0", "libGLX.so.0", "libxcb.so.1", "libEGL.so.1"], broken=["libGL.so.1"])
    assert syslibs.preload(fallback, load=load) == []  # Qt then reports the real error itself


def test_missing_directory_is_a_no_op(tmp_path):
    assert syslibs.preload(tmp_path / "nope", load=Loader()) == []


def test_only_the_frozen_program_preloads(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert syslibs.fallback_dir() is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "fritzfax"))
    assert syslibs.fallback_dir() == tmp_path / "fallback-libs"


# --- environment of system programs started by the frozen binary (live 2026-10-26) --------------
# The frozen binary puts its own library directory on LD_LIBRARY_PATH. fc-match, a PDF viewer or
# LibreOffice inherited it and loaded the bundled fontconfig 2.14, which cannot read the newer
# configuration of the system (Arch, fontconfig 2.18: 90 "Fontconfig warning" lines, rules skipped).

def test_system_env_restores_the_original_library_path():
    env = syslibs.system_env({"LD_LIBRARY_PATH": "/opt/ff/_internal", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
                              "HOME": "/h"}, bundle="/opt/ff/_internal")
    assert env == {"LD_LIBRARY_PATH": "/usr/local/lib", "HOME": "/h"}


def test_system_env_drops_the_bundle_when_there_was_no_original():
    env = syslibs.system_env({"LD_LIBRARY_PATH": "/opt/ff/_internal"}, bundle="/opt/ff/_internal")
    assert "LD_LIBRARY_PATH" not in env


def test_system_env_keeps_other_entries_and_drops_bundle_subdirectories():
    sep = os.pathsep
    value = sep.join(["/opt/ff/_internal/PySide6/Qt/lib", "/usr/local/lib", "", "/opt/ff/_internal"])
    env = syslibs.system_env({"LD_LIBRARY_PATH": value}, bundle="/opt/ff/_internal")
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"


def test_system_env_is_idempotent_and_leaves_a_normal_run_alone():
    once = syslibs.system_env({"LD_LIBRARY_PATH": "/opt/ff/_internal", "LD_LIBRARY_PATH_ORIG": "/x"},
                              bundle="/opt/ff/_internal")
    assert syslibs.system_env(once, bundle="/opt/ff/_internal") == once
    plain = {"LD_LIBRARY_PATH": "/usr/local/lib", "PATH": "/bin"}
    assert syslibs.system_env(plain, bundle=None) == plain


def test_system_env_does_not_mistake_a_sibling_directory_for_the_bundle():
    env = syslibs.system_env({"LD_LIBRARY_PATH": "/opt/ff/_internal2"}, bundle="/opt/ff/_internal")
    assert env["LD_LIBRARY_PATH"] == "/opt/ff/_internal2"


def test_restore_system_env_changes_what_child_processes_inherit(monkeypatch, tmp_path):
    import subprocess

    bundle = tmp_path / "_internal"
    bundle.mkdir()
    monkeypatch.setattr(syslibs.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    syslibs.restore_system_env()
    child = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('LD_LIBRARY_PATH'), "
                            "os.environ.get('LD_LIBRARY_PATH_ORIG'))"], capture_output=True, text=True, check=True)
    assert child.stdout.split() == ["/usr/local/lib", "None"]


def test_system_lacks_reports_what_came_from_the_fallback(monkeypatch):
    monkeypatch.setattr(syslibs, "LOADED", ["libxcb.so.1"])
    assert syslibs.system_lacks("libxcb.so.1") is True
    assert syslibs.system_lacks("libfontconfig.so.1") is False


MAPS = """7f00-7f01 r--p 00000000 08:01 1 /usr/lib/libc.so.6
7f02-7f03 r-xp 00001000 08:01 2 {fc}
7f04-7f05 r--p 00000000 08:01 3 /opt/ff/_internal/libQt6Gui.so.6
"""


def test_mapped_path_finds_the_loaded_copy():
    maps = MAPS.format(fc="/usr/lib/libfontconfig.so.1.17.1")
    assert syslibs.mapped_path("libfontconfig.so.1", maps) == "/usr/lib/libfontconfig.so.1.17.1"
    assert syslibs.mapped_path("libxcb.so.1", maps) is None


@pytest.mark.parametrize("fc, verdict", [
    ("/usr/lib/libfontconfig.so.1.17.1", "fontconfig of the system"),
    ("/opt/ff/fallback-libs/libfontconfig.so.1", "fontconfig from fallback-libs"),
])
def test_library_report_names_where_fontconfig_came_from(fc, verdict):
    detail = syslibs.check_system_libraries(MAPS.format(fc=fc), env={}, bundle="/opt/ff/_internal")
    assert verdict in detail


def test_library_report_fails_for_a_bundled_fontconfig_in_front_of_the_system():
    with pytest.raises(RuntimeError, match="fontconfig"):
        syslibs.check_system_libraries(MAPS.format(fc="/opt/ff/_internal/libfontconfig.so.1"), env={},
                                       bundle="/opt/ff/_internal")


def test_library_report_fails_when_children_would_inherit_the_bundle():
    with pytest.raises(RuntimeError, match="library path"):
        syslibs.check_system_libraries(MAPS.format(fc="/usr/lib/libfontconfig.so.1"),
                                       env={"LD_LIBRARY_PATH": "/opt/ff/_internal"}, bundle="/opt/ff/_internal")


def test_library_report_without_fontconfig_loaded():
    detail = syslibs.check_system_libraries("", env={}, bundle="/opt/ff/_internal")
    assert "fontconfig not loaded" in detail


# --- a fontconfig configuration for systems without one (dist test 2026-10-26) ------------------
# Minimal images had no /etc/fonts/fonts.conf: "Fontconfig error: Cannot load default config file".

@pytest.fixture
def conf_dir(tmp_path):
    (tmp_path / "fonts.conf").write_text("<fontconfig/>")
    return tmp_path


def test_fallback_fonts_conf_is_used_when_the_system_has_none(conf_dir, tmp_path):
    env = {}
    assert syslibs.use_fallback_fonts_conf(conf_dir, env, system_conf=tmp_path / "missing.conf") is True
    assert env == {"FONTCONFIG_FILE": str(conf_dir / "fonts.conf")}


def test_system_fonts_conf_wins(conf_dir, tmp_path):
    system = tmp_path / "etc-fonts.conf"
    system.write_text("<fontconfig/>")
    env = {}
    assert syslibs.use_fallback_fonts_conf(conf_dir, env, system_conf=system) is False
    assert env == {}


@pytest.mark.parametrize("key", ["FONTCONFIG_FILE", "FONTCONFIG_PATH"])
def test_a_configuration_chosen_by_the_user_wins(conf_dir, tmp_path, key):
    env = {key: "/srv/fonts/user.conf"}
    assert syslibs.use_fallback_fonts_conf(conf_dir, env, system_conf=tmp_path / "missing.conf") is False
    assert env == {key: "/srv/fonts/user.conf"}


def test_no_fallback_fonts_conf_shipped(tmp_path):
    env = {}
    assert syslibs.use_fallback_fonts_conf(tmp_path, env, system_conf=tmp_path / "missing.conf") is False
    assert env == {}
