"""PDF pages -> grayscale rasters via QtPdf (part of PySide6, no poppler needed).

QPdfDocument.render() returns a transparent background; it is composited onto white
(measured: without that every pixel reads as black).
"""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image

from . import geometry as g
from .errors import RenderError

MM_PER_PT = 25.4 / 72
# pdfium (behind QtPdf) is not thread-safe; previews and sends may render concurrently
_LOCK = threading.RLock()


def _ensure_qt_app():
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.instance() or QCoreApplication([])


def page_count(path: Path) -> int:
    with _LOCK:
        doc = _load(path)
        try:
            return doc.pageCount()
        finally:
            doc.close()


def _load(path: Path):
    _ensure_qt_app()
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    err = doc.load(str(path))
    if err != QPdfDocument.Error.None_ or doc.pageCount() < 1:
        name = getattr(err, "name", str(err))
        if name == "IncorrectPassword":
            raise RenderError(f"{path.name}: the PDF is password protected")
        raise RenderError(f"{path.name}: cannot read PDF ({name})")
    return doc


def _qimage_to_gray(qimg) -> Image.Image:
    from PySide6.QtGui import QImage

    qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = qimg.width(), qimg.height()
    rgba = Image.frombuffer("RGBA", (w, h), bytes(qimg.constBits()), "raw", "RGBA", qimg.bytesPerLine(), 1)
    white = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert("L")


def render_pdf(path: Path, pages: list[int] | None = None) -> list[Image.Image]:
    """Render the selected pages (0-based) at fax resolution, each placed on an A4 fax page."""
    with _LOCK:
        return _render_locked(path, pages)


def _render_locked(path: Path, pages: list[int] | None) -> list[Image.Image]:
    from PySide6.QtCore import QSize

    doc = _load(path)
    try:
        count = doc.pageCount()
        selected = pages if pages is not None else list(range(count))
        out = []
        for index in selected:
            if not 0 <= index < count:
                raise RenderError(f"{path.name}: page {index + 1} does not exist (document has {count})")
            size = doc.pagePointSize(index)
            w_mm, h_mm = size.width() * MM_PER_PT, size.height() * MM_PER_PT
            landscape = w_mm > h_mm
            pw, ph = (h_mm, w_mm) if landscape else (w_mm, h_mm)  # portrait dimensions on the fax
            scale = g.fit_scale(pw, ph)
            tw, th = g.target_pixels(pw, ph, scale)
            # render unrotated at the size that becomes (tw, th) after a 90 degree turn
            qsize = QSize(th, tw) if landscape else QSize(tw, th)
            raster = _qimage_to_gray(doc.render(index, qsize))
            if landscape:
                raster = raster.transpose(Image.Transpose.ROTATE_90)
            out.append(g.place(raster))
        return out
    finally:
        doc.close()
