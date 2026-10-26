"""`fritzfax selftest`: does everything fritzfax needs work on this system?"""
from __future__ import annotations

import io
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import __version__


def system_line() -> str:
    """'fritzfax 0.1.x on Linux x86_64, glibc 2.41' (macOS/Windows: their release instead of glibc)."""
    try:
        libc = os.confstr("CS_GNU_LIBC_VERSION") if hasattr(os, "confstr") else None
    except (ValueError, OSError):
        libc = None
    return f"fritzfax {__version__} on {platform.system()} {platform.machine()}, {libc or platform.release()}"


def run(gui: bool = False, soffice: str = "", out=print) -> bool:
    results: list[tuple[str, str, str]] = []

    def check(name, fn, warn_only=False):
        t = time.monotonic()
        try:
            detail = fn() or ""
            results.append(("OK", name, detail))
        except Exception as exc:  # noqa: BLE001 - every failure is reported
            results.append(("WARN" if warn_only else "FAIL", name, str(exc) or exc.__class__.__name__))
        out(f"{results[-1][0]:4}  {name}: {results[-1][2]}  ({time.monotonic() - t:.1f} s)")

    out(system_line())
    app_holder = []
    if gui:
        def make_app():
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication(["fritzfax"])
            app_holder.append(app)
            return f"platform {app.platformName()}"
        check("GUI toolkit", make_app)

    def qt():
        import PySide6
        from PySide6.QtCore import qVersion

        from . import syslibs

        extra = f" (fallback libraries: {', '.join(syslibs.LOADED)})" if syslibs.LOADED else ""
        return f"PySide6 {PySide6.__version__}, Qt {qVersion()}{extra}"
    check("Qt", qt)

    tmp = Path(tempfile.mkdtemp(prefix="fritzfax-selftest-"))
    pdf = tmp / "test.pdf"
    page = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(page)
    d.rectangle([118, 118, 413, 236], fill="black")
    d.text((118, 300), "fritzfax selftest", fill="black")
    page.save(pdf, resolution=150)
    state = {}

    def render_pdf():
        from .core import job, render

        state["prepared"] = job.prepare(render.Document(files=[pdf]), sender_name="selftest", sender_number="0")
        pages = state["prepared"].pages
        if len(pages) != 1 or pages[0].size != (1728, 2287) or pages[0].histogram()[0] < 1000:
            raise RuntimeError("unexpected page")
        return "1 page, 1728 x 2287"
    check("PDF rendering", render_pdf)

    def sff_round_trip():
        from .core import sff

        data = state["prepared"].sff
        back = sff.decode(data)
        if back[0].tobytes() != state["prepared"].pages[0].tobytes():
            raise RuntimeError("decoded page differs")
        return f"{len(data)} bytes SFF"
    check("fax encoding (SFF)", sff_round_trip)

    def archive_compare():
        from .core import pdfccitt, verify

        buf = io.BytesIO()
        state["prepared"].pages[0].save(buf, "PDF")
        same, diffs = verify.compare_pages(state["prepared"].pages, pdfccitt.extract_pages(buf.getvalue()))
        if not same:
            raise RuntimeError(f"{diffs} pixels differ")
        return "libtiff CCITT decode bit-exact"
    check("archive comparison (verification)", archive_compare)

    def fonts():
        from .core.render.fonts import find_font_file

        return find_font_file() or "no TrueType font found, using the built-in font"
    check("font", fonts)

    def office():
        from .core import render
        from .core.render import office as off

        ok, detail = off.status(soffice)
        if not ok:
            raise RuntimeError(f"{detail} - office documents cannot be converted, PDF and images work")
        rtf = tmp / "test.rtf"
        rtf.write_text(r"{\rtf1\ansi\f0\fs24 fritzfax selftest\par}")
        pages = render.render_document(render.Document(files=[rtf], soffice=soffice))
        return f"{detail}; converted 1 document to {len(pages)} page(s)"
    check("LibreOffice (optional)", office, warn_only=True)

    if getattr(sys, "frozen", False):
        from . import syslibs

        check("system libraries", syslibs.check_system_libraries)

    if gui:
        def window():
            from .core import config
            from .gui.main_window import MainWindow

            w = MainWindow(config.Settings(), tmp / "selftest.conf", "en")
            w.show()
            try:
                # the way a user does it: add the file, the window renders its own preview
                w.add_files([str(pdf)])
                deadline = time.monotonic() + 30
                while True:
                    app_holder[0].processEvents()
                    if w.prepared is not None and w.thumbs.count() == 1 and not w.page_view.pixmap().isNull():
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"preview not shown (thumbnails: {w.thumbs.count()}, "
                                           f"document prepared: {w.prepared is not None})")
                    time.sleep(0.02)
            finally:
                w.close()
            return "main window opened, preview of 1 page shown, closed"
        check("GUI", window)

    shutil.rmtree(tmp, ignore_errors=True)  # every check catches its own errors, so this is reached
    failed = [r for r in results if r[0] == "FAIL"]
    out("selftest " + ("FAILED" if failed else "passed"))
    return not failed
