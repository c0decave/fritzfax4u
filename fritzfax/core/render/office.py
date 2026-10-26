"""Office documents -> PDF via LibreOffice (soffice --headless --convert-to pdf).

LibreOffice is optional. It is looked up in this order: the configured path, soffice in PATH,
then a LibreOffice bundled with the fritzfax release (next to the program or installed to
~/.local/share/fritzfax/libreoffice). Without it, PDFs and images still work; office files
raise OfficeUnavailable, which the GUI and CLI turn into a clear hint.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ... import syslibs
from .. import paths
from .errors import RenderError

EXTENSIONS = {".odt", ".ott", ".doc", ".docx", ".rtf", ".txt", ".ods", ".xls", ".xlsx", ".odp", ".ppt", ".pptx",
              ".odg", ".html", ".htm"}


SYSTEM_FONTS_CONF = syslibs.SYSTEM_FONTS_CONF
RUNTIME_DIR = "fritzfax-runtime"  # libraries + fonts.conf shipped with the bundled LibreOffice
RUNTIME_FALLBACK = "fallback"  # runtime libraries that must not hide a system copy (fontconfig)


class OfficeUnavailable(RenderError):
    """No working LibreOffice: office documents cannot be converted (PDF and images still work)."""


def bundled_candidates() -> list[Path]:
    exe_dir = Path(sys.executable).resolve().parent
    data = paths.data_dir()
    roots = [Path(os.environ["FRITZFAX_LIBREOFFICE"])] if os.environ.get("FRITZFAX_LIBREOFFICE") else []
    roots += [exe_dir / "libreoffice", exe_dir.parent / "libreoffice", data / "libreoffice"]
    found = []
    for root in roots:
        found += [root / "program" / "soffice"] + sorted(root.glob("opt/libreoffice*/program/soffice"))
    return found


def platform_candidates() -> list[Path]:
    """Where the LibreOffice installers of macOS and Windows put soffice."""
    if sys.platform == "darwin":
        return [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
                Path.home() / "Applications" / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"]
    if sys.platform == "win32":
        bases = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432")]
        return [Path(b) / "LibreOffice" / "program" / "soffice.exe" for b in dict.fromkeys(bases) if b]
    return []


def _executable(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


def find_soffice(override: str = "") -> str | None:
    if override:
        return override if _executable(Path(override)) else None
    system = shutil.which("soffice") or shutil.which("libreoffice")
    if system:
        return system
    for candidate in bundled_candidates() + platform_candidates():
        if _executable(candidate):
            return str(candidate)
    return None


def soffice_env(binary: str) -> dict[str, str]:
    """Environment for starting LibreOffice.

    The frozen fritzfax binary (PyInstaller) points LD_LIBRARY_PATH at its own libraries; a
    LibreOffice must not inherit that (syslibs.system_env). A LibreOffice shipped with fritzfax gets
    its runtime libraries (for systems without X11/cairo/NSS ...) and, if the system has no fontconfig
    configuration, a minimal one. Its fontconfig is only used when the system has none: an older copy
    in front of the system's cannot read the newer system configuration (live 2026-10-26).
    """
    env = syslibs.system_env()
    runtime = Path(binary).resolve().parent.parent / RUNTIME_DIR
    if runtime.is_dir():
        dirs = [str(runtime)]
        fallback = runtime / RUNTIME_FALLBACK
        if fallback.is_dir() and any(syslibs.system_lacks(p.name) for p in fallback.iterdir()):
            dirs.append(str(fallback))
        env["LD_LIBRARY_PATH"] = os.pathsep.join([*dirs, *[x for x in [env.get("LD_LIBRARY_PATH", "")] if x]])
        conf = runtime / "fonts.conf"
        if not SYSTEM_FONTS_CONF.exists() and conf.exists():
            env["FONTCONFIG_FILE"] = str(conf)
    return env


def status(override: str = "", timeout: float = 60) -> tuple[bool, str]:
    """(usable, description) - used by the self test and the settings dialog."""
    binary = find_soffice(override)
    if not binary:
        return False, "LibreOffice not found"
    try:
        proc = subprocess.run([binary, "--headless", "--version"], capture_output=True, text=True, timeout=timeout,
                              env=soffice_env(binary))
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{binary}: {exc}"
    text = (proc.stdout or proc.stderr).strip().splitlines()
    return proc.returncode == 0, f"{binary}: {text[0] if text else 'exit ' + str(proc.returncode)}"


def convert_to_pdf(path: Path, workdir: Path, soffice: str = "", timeout: float = 180) -> Path:
    binary = find_soffice(soffice)
    if not binary:
        raise OfficeUnavailable(f"{path.name}: LibreOffice is needed for this file type but is not available")
    profile = Path(tempfile.mkdtemp(prefix="lo-profile-", dir=workdir))
    # an own output directory per conversion: two files with the same name stem (brief.docx,
    # brief.odt) must never pick up each other's PDF
    outdir = Path(tempfile.mkdtemp(prefix="lo-out-", dir=workdir))
    cmd = [binary, "--headless", "--norestore", "--nologo", f"-env:UserInstallation={profile.as_uri()}",
           "--convert-to", "pdf", "--outdir", str(outdir), str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=soffice_env(binary))
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"{path.name}: LibreOffice did not finish within {timeout:.0f} s") from exc
    except OSError as exc:
        raise OfficeUnavailable(f"{path.name}: cannot start LibreOffice ({exc})") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdf = outdir / (path.stem + ".pdf")
    if proc.returncode != 0 or not pdf.is_file():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        if any(word in detail[0] for word in ("GLIBC", "error while loading shared libraries", "not found")):
            raise OfficeUnavailable(f"{path.name}: LibreOffice cannot run on this system ({detail[0]})")
        raise RenderError(f"{path.name}: LibreOffice conversion failed ({detail[0]})")
    return pdf
