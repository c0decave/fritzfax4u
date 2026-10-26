"""PIL image -> QImage for the preview.

PIL.ImageQt is not used: it decides at import time which Qt binding is installed and leaves its
ImageQt class undefined if that probe fails, which is hard to diagnose in the frozen release.
"""
from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage


def to_qimage(img: Image.Image) -> QImage:
    """8-bit grayscale copy of img (fax pages are black and white)."""
    if img.width == 0 or img.height == 0:
        raise ValueError("empty image")
    gray = img.convert("L")
    # rows of gray.tobytes() are packed (stride = width); copy() detaches from the Python buffer
    return QImage(gray.tobytes(), gray.width, gray.height, gray.width, QImage.Format.Format_Grayscale8).copy()
