#!/usr/bin/env python3
"""Collect the system libraries a bundled LibreOffice needs on minimal systems.

    python3 collect_libs.py LO_ROOT OUT_DIR

Runs inside the release builder (AlmaLinux 9, glibc 2.34). Every ELF file of LO_ROOT/program is
inspected with ldd; libraries outside LO_ROOT are copied to OUT_DIR, except the glibc family
(always from the target system), graphics drivers (must match the system) and the libstdc++
runtime (the system one is newer). LibreOffice's optional desktop integration plugins
(gtk3/gtk4/kf5/kf6/qt5/qt6) are skipped: headless conversion does not load them.
Libraries that are loaded with dlopen() from the directory of another library are invisible to
ldd; DLOPEN_COMPANIONS adds them (with their own dependencies) next to that library.
Fails if a library of a needed file cannot be resolved in the builder.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

# always from the target system: the glibc family and the C++ runtime (the system one is newer)
SYSTEM_SONAMES = re.compile(r"^(ld-linux.*|libc|libm|libdl|libpthread|librt|libutil|libresolv|libanl|libnsl|"
                            r"libcrypt|libBrokenLocale|libmvec|libthread_db|libnss_.*|libstdc\+\+|libgcc_s)\.so")
# for LibreOffice also the graphics stack (its runtime directory is always on LD_LIBRARY_PATH, so a
# bundled copy would replace the system's drivers)
SKIP_SONAMES = re.compile(SYSTEM_SONAMES.pattern + r"|^(libGL|libEGL|libGLX|libGLdispatch|libOpenGL|libgbm|"
                          r"libdrm.*|libvulkan)\.so")
# Qt files of the frozen program whose dependencies go into the fallback directory (fritzfax/syslibs.py)
QT_CORE = ("libQt6Gui.so.6", "libQt6Widgets.so.6", "libQt6Pdf.so.6", "libqoffscreen.so")
OPTIONAL_PLUGINS = re.compile(r"libvclplug_(gtk3|gtk4|kf5|kf6|qt5|qt6|gtk3_kde5|gtk3kde5)|"
                              r"lib(kf5|kf6|qt5|qt6|gtk3|gtk4).*be1lo|libavmedia(gst|gtk|qt5|qt6|vlc)|liblibreofficekitgtk|"
                              r"libofficebean|libpdffilterlo_?gtk|lo_kde5filepicker|lo_kf6filepicker")
# NSS (PDF export initialises it) loads its softoken from the directory of libnss3.so; without
# it the export fails with "SfxBaseModel::impl_store ... Io Write Code:16".
DLOPEN_COMPANIONS = {"libnss3.so": ("libsoftokn3.so", "libfreeblpriv3.so")}
# Libraries that read a system configuration whose format grows (fontconfig 2.18 on Arch writes rules
# fontconfig 2.14 rejects): they go to <out>/fallback, which LibreOffice only gets when the system has
# no copy of its own (fritzfax/core/render/office.py).
SYSTEM_FIRST = re.compile(r"^libfontconfig\.so")


def elf_files(program: Path):
    for f in sorted(program.iterdir()):
        if f.is_file() and not f.is_symlink() and not OPTIONAL_PLUGINS.search(f.name):
            with open(f, "rb") as fh:
                if fh.read(4) == b"\x7fELF":
                    yield f


def resolve(f: Path, env: dict[str, str], root: Path, needed: dict[str, Path], missing: dict[str, list[str]],
            skip: re.Pattern = SKIP_SONAMES, resolve_links: bool = True):
    res = subprocess.run(["ldd", str(f)], capture_output=True, text=True, env=env)
    for line in res.stdout.splitlines():
        m = re.match(r"\s*(\S+) => (not found|(\S+) \(0x)", line)
        if not m:
            continue
        soname, target = m.group(1), m.group(3)
        if skip.match(soname):
            continue
        if target is None:
            missing.setdefault(soname, []).append(f"needed by {f.name}")
        elif not (Path(target).resolve() if resolve_links else Path(target)).is_relative_to(root):
            needed[soname] = Path(target)


def main(lo_root: str, out_dir: str) -> int:
    root, out = Path(lo_root).resolve(), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    env = {"LD_LIBRARY_PATH": str(root / "program"), "PATH": "/usr/bin:/bin"}
    needed: dict[str, Path] = {}
    missing: dict[str, list[str]] = {}
    for f in elf_files(root / "program"):
        resolve(f, env, root, needed, missing)
    checksums = []
    for soname, companions in DLOPEN_COMPANIONS.items():
        if soname not in needed:
            continue
        for name in companions:
            src = needed[soname].parent / name
            if not src.is_file():
                missing.setdefault(name, []).append(f"dlopen companion of {soname}")
                continue
            needed[name] = src
            resolve(src, env, root, needed, missing)
            checksums += [c for c in [src.with_suffix(".chk")] if c.is_file()]  # NSS FIPS self-check
    if missing:
        for soname, users in sorted(missing.items()):
            print(f"unresolved {soname} ({', '.join(users[:3])})", file=sys.stderr)
        return 1
    for soname, src in sorted(needed.items()):
        target = out / "fallback" if SYSTEM_FIRST.match(soname) else out
        target.mkdir(exist_ok=True)
        shutil.copy2(src.resolve(), target / soname)
    for chk in checksums:
        shutil.copy2(chk.resolve(), out / chk.name)
    print(f"collected {len(needed)} libraries into {out}")
    return 0


def main_qt_fallback(internal_dir: str, out_dir: str) -> int:
    """Libraries the frozen Qt needs from outside the PyInstaller tree (except the glibc family)."""
    root, out = Path(internal_dir).absolute(), Path(out_dir)
    lib_dirs = sorted({str(p.parent) for p in root.rglob("*.so*") if p.is_file()})
    env = {"LD_LIBRARY_PATH": ":".join(lib_dirs), "PATH": "/usr/bin:/bin"}
    files = {name: sorted(root.rglob(name)) for name in QT_CORE}
    if not files["libQt6Gui.so.6"]:
        print(f"libQt6Gui.so.6 not found in {root}", file=sys.stderr)
        return 1
    needed: dict[str, Path] = {}
    missing: dict[str, list[str]] = {}
    for hits in files.values():
        for f in hits[:1]:
            resolve(f, env, root, needed, missing, skip=SYSTEM_SONAMES, resolve_links=False)
    if missing:
        for soname, users in sorted(missing.items()):
            print(f"unresolved {soname} ({', '.join(users[:3])})", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for soname, src in sorted(needed.items()):
        shutil.copy2(src.resolve(), out / soname)
    print(f"collected {len(needed)} fallback libraries into {out}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--qt-fallback"]:
        sys.exit(main_qt_fallback(*sys.argv[2:4]))
    sys.exit(main(*sys.argv[1:3]))
