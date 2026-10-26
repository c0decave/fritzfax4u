"""Fax page geometry: ITU-T T.4 line of 1728 pixels across 215 mm, fine resolution 7.7 lines/mm.

Measured on FRITZ!OS 8.25: the box records pages of the web interface (1728 x 2444) as
595 x 897 pt (210 x 316 mm), i.e. about 6.5 % longer than A4. We use the correct A4 height
of 297 mm x 7.7 = 2287 lines instead.
"""
from __future__ import annotations

from PIL import Image

from ..sff import A4_LINES, FINE_LINES_PER_MM, LINE_WIDTH

LINE_MM = 215.0
PAGE_WIDTH = LINE_WIDTH
PAGE_HEIGHT = A4_LINES
PAGE_MM = (LINE_MM, PAGE_HEIGHT / FINE_LINES_PER_MM)  # 215 x 297 mm
PX_PER_MM_X = PAGE_WIDTH / LINE_MM
PX_PER_MM_Y = FINE_LINES_PER_MM
# square-pixel canvas for drawing text (x resolution in both directions)
SQUARE_HEIGHT = round(PAGE_MM[1] * PX_PER_MM_X)


def fit_scale(width_mm: float, height_mm: float, page_mm=PAGE_MM) -> float:
    """Scale factor that keeps the physical size unless the source is larger than the page."""
    return min(1.0, page_mm[0] / width_mm, page_mm[1] / height_mm)


def target_pixels(width_mm: float, height_mm: float, scale: float) -> tuple[int, int]:
    return (max(1, round(width_mm * scale * PX_PER_MM_X)), max(1, round(height_mm * scale * PX_PER_MM_Y)))


def blank_page() -> Image.Image:
    return Image.new("L", (PAGE_WIDTH, PAGE_HEIGHT), 255)


def place(content: Image.Image, top: int = 0) -> Image.Image:
    """Paste a grayscale raster on a blank page, centered horizontally."""
    page = blank_page()
    x = max(0, (PAGE_WIDTH - content.width) // 2)
    page.paste(content.crop((0, 0, min(content.width, PAGE_WIDTH), min(content.height, PAGE_HEIGHT - top))),
               (x, top))
    return page


def square_to_fax(img: Image.Image) -> Image.Image:
    """Convert a square-pixel A4 canvas (1728 x SQUARE_HEIGHT) to fax geometry."""
    return img.resize((PAGE_WIDTH, PAGE_HEIGHT), Image.Resampling.LANCZOS)
