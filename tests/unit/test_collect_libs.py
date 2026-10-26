"""tools/collect_libs.py: runtime libraries for the bundled LibreOffice, including dlopen() companions."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="collects Linux libraries with ldd")

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("collect_libs", ROOT / "tools" / "collect_libs.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)

LIB_DIRS = [Path(d) for d in ("/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/lib", "/lib64")]


def system_lib(name: str) -> Path:
    for d in LIB_DIRS:
        if (d / name).is_file():
            return d / name
    pytest.skip(f"{name} not installed on this system")


@pytest.fixture
def lo_root(tmp_path):
    (tmp_path / "lo" / "program").mkdir(parents=True)
    return tmp_path / "lo"


def test_nss_softoken_is_collected_with_its_dependencies(lo_root, tmp_path):
    # PDF export initialises NSS, which dlopen()s libsoftokn3 from the directory of libnss3;
    # ldd cannot see that, and without it the export fails with "Io Write Code:16".
    shutil.copy2(system_lib("libsmime3.so"), lo_root / "program")
    system_lib("libsoftokn3.so")
    system_lib("libfreeblpriv3.so")
    out = tmp_path / "out"
    assert cl.main(str(lo_root), str(out)) == 0
    names = {p.name for p in out.iterdir()}
    assert {"libnss3.so", "libsoftokn3.so", "libfreeblpriv3.so"} <= names
    assert "libsqlite3.so.0" in names  # needed by libsoftokn3, not by anything in program/


def test_no_companions_without_nss(lo_root, tmp_path):
    shutil.copy2("/bin/true" if Path("/bin/true").is_file() else shutil.which("true"), lo_root / "program")
    out = tmp_path / "out"
    assert cl.main(str(lo_root), str(out)) == 0
    assert not any("softokn" in p.name for p in out.iterdir())


def test_missing_companion_fails_the_build(lo_root, tmp_path, monkeypatch, capsys):
    shutil.copy2(system_lib("libsmime3.so"), lo_root / "program")
    monkeypatch.setitem(cl.DLOPEN_COMPANIONS, "libnss3.so", ("libdoesnotexist3.so",))
    assert cl.main(str(lo_root), str(tmp_path / "out")) == 1
    assert "unresolved libdoesnotexist3.so (dlopen companion of libnss3.so)" in capsys.readouterr().err


# --- fallback libraries of the frozen Qt (libraries PyInstaller treats as "system") -------------

def pyside_lib(name: str) -> Path:
    pyside = pytest.importorskip("PySide6")
    hits = sorted(Path(pyside.__file__).parent.rglob(name))
    if not hits:
        pytest.skip(f"{name} not in this PySide6")
    return hits[0]


@pytest.fixture
def internal(tmp_path):
    # like the PyInstaller tree: the wheel's Qt libraries under _internal/PySide6/Qt/lib
    d = tmp_path / "_internal"
    lib = d / "PySide6" / "Qt" / "lib"
    lib.mkdir(parents=True)
    for f in pyside_lib("libQt6Gui.so.6").parent.iterdir():
        (lib / f.name).symlink_to(f)
    return d


def test_qt_fallback_contains_gl_but_not_glibc(internal, tmp_path):
    out = tmp_path / "fallback"
    assert cl.main_qt_fallback(str(internal), str(out)) == 0
    names = {p.name for p in out.iterdir()}
    assert {"libGL.so.1", "libEGL.so.1"} <= names
    assert not names & {"libc.so.6", "libm.so.6", "libstdc++.so.6", "libgcc_s.so.1"}
    assert "libQt6Core.so.6" not in names  # bundled already


def test_qt_fallback_needs_the_qt_libraries(tmp_path, capsys):
    (tmp_path / "_internal").mkdir()
    assert cl.main_qt_fallback(str(tmp_path / "_internal"), str(tmp_path / "out")) == 1
    assert "libQt6Gui.so.6 not found" in capsys.readouterr().err


# --- fontconfig: never in front of a newer system copy (live 2026-10-26) --------------------------

def test_fontconfig_of_the_libreoffice_runtime_goes_to_its_fallback_directory(lo_root, tmp_path):
    # stand-in for a LibreOffice binary that links fontconfig: the system's fc-match
    user = next((p for p in (Path("/usr/bin/fc-match"), Path("/usr/bin/fc-list")) if p.is_file()), None)
    if user is None:
        pytest.skip("no fontconfig tools on this system")
    shutil.copy2(user, lo_root / "program" / "fc-match")
    out = tmp_path / "runtime"
    assert cl.main(str(lo_root), str(out)) == 0
    assert (out / "fallback" / "libfontconfig.so.1").is_file()
    assert not (out / "libfontconfig.so.1").exists()


def test_the_frozen_program_does_not_bundle_fontconfig_in_front_of_the_system():
    # the spec drops it from _internal; collect_libs --qt-fallback then puts it into fallback-libs
    import ast
    import re

    spec = (ROOT / "tools" / "fritzfax.spec").read_text()
    node = next(n for n in ast.walk(ast.parse(spec)) if isinstance(n, ast.Assign) and
                getattr(n.targets[0], "id", "") == "DROP")
    drop = eval(compile(ast.Expression(node.value), "spec", "eval"), {"re": re})
    assert drop.search("_internal/libfontconfig.so.1")
    assert not drop.search("_internal/libfreetype.so.6") and not drop.search("_internal/libQt6Gui.so.6")
