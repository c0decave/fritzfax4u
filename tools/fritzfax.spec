# PyInstaller spec for the fritzfax release binary (onedir).
#   Linux:   dist.sh inside tools/containers/builder.Containerfile (AlmaLinux 9, glibc 2.34): one
#            program "fritzfax" (install.sh links fritzfax-gui to it)
#   Windows: fritzfax.exe (console: command line) and fritzfax-gui.exe (no console window)
#   macOS:   fritzfax.app (the program inside also runs the command line with arguments)
# Windows and macOS are built by .github/workflows/release.yml on those systems.
import re
import sys
from PyInstaller.utils.hooks import collect_data_files

SRC = SPECPATH + "/.."

# Leave out what fritzfax does not use or what must come from the target system:
#  - libstdc++/libgcc_s: the system ones are newer; a bundled old libstdc++ breaks Mesa/LLVM
#  - the GTK3 platform theme: would load the system GTK next to our bundled glib
#  - Qml/Quick/virtual keyboard: pulled in by an input-method plugin, not used
#  - Qt's TIFF plugin (needs libtiff.so.5; fritzfax reads TIFF with Pillow)
#  - fontconfig: an old copy cannot read a newer system configuration (Arch 2026-10: 90 warnings,
#    rules skipped); collect_libs --qt-fallback puts it into fallback-libs for systems without one
DROP = re.compile(r"(^|/)(libstdc\+\+\.so|libgcc_s\.so|libfontconfig\.so|libqgtk3\.so|libqtvirtualkeyboardplugin\.so|"
                  r"libQt6(Qml|QmlMeta|QmlModels|QmlWorkerScript|Quick|VirtualKeyboard|VirtualKeyboardQml)\.so|"
                  r"libqtiff\.so)")

a = Analysis(
    [SRC + "/tools/pyinstaller_entry.py"],
    pathex=[SRC],
    datas=collect_data_files("fritzfax"),
    hiddenimports=["PySide6.QtPdf", "keyring.backends.SecretService", "keyring.backends.kwallet",
                   "keyring.backends.libsecret", "keyring.backends.fail", "keyring.backends.Windows",
                   "keyring.backends.macOS"],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtNetwork"],
    noarchive=False,
)
a.binaries = [b for b in a.binaries if not DROP.search(b[0])]
a.datas = [d for d in a.datas if not DROP.search(d[0])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="fritzfax", console=sys.platform != "darwin",
          strip=False, upx=False)
programs = [exe]
if sys.platform == "win32":
    programs.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="fritzfax-gui", console=False,
                        strip=False, upx=False))
coll = COLLECT(*programs, a.binaries, a.datas, strip=False, upx=False, name="fritzfax")
if sys.platform == "darwin":
    __version__ = open(SRC + "/VERSION").read().strip()
    app = BUNDLE(coll, name="fritzfax.app", bundle_identifier="io.github.fritzfax4u",
                 info_plist={"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                             "NSHighResolutionCapable": True, "LSApplicationCategoryType":
                             "public.app-category.productivity"})
