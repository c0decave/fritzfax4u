"""Raster images (PNG, JPEG, TIFF incl. multi-page, ...) -> fax pages."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from . import geometry as g
from .errors import RenderError

Image.MAX_IMAGE_PIXELS = 200_000_000  # large scans are fine, decompression bombs are not


def _to_gray(frame: Image.Image) -> Image.Image:
    if frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info):
        rgba = frame.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, rgba).convert("L")
    return frame.convert("L")


def _physical_mm(frame: Image.Image) -> tuple[float, float] | None:
    dpi = frame.info.get("dpi")
    if not dpi:
        return None
    try:
        dx, dy = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError, IndexError):
        return None
    if dx < 50 or dy < 50:  # 72/96 "dpi" defaults are ignored only when absurdly low
        return None
    return frame.width / dx * 25.4, frame.height / dy * 25.4


def image_to_page(frame: Image.Image) -> Image.Image:
    gray = _to_gray(frame)
    size_mm = _physical_mm(frame)
    if size_mm is None:  # unknown size: fit the page
        size_mm = (g.PAGE_MM[0], g.PAGE_MM[0] * gray.height / gray.width)
        if size_mm[1] > g.PAGE_MM[1]:
            size_mm = (g.PAGE_MM[1] * gray.width / gray.height, g.PAGE_MM[1])
    w_mm, h_mm = size_mm
    if w_mm > h_mm and w_mm > g.PAGE_MM[0]:  # landscape larger than the page: turn it
        gray = gray.transpose(Image.Transpose.ROTATE_90)
        w_mm, h_mm = h_mm, w_mm
    scale = g.fit_scale(w_mm, h_mm)
    tw, th = g.target_pixels(w_mm, h_mm, scale)
    return g.place(gray.resize((tw, th), Image.Resampling.LANCZOS))


def frame_count(path: Path) -> int:
    """Pages of an image file (frames of a multi-page TIFF, else 1)."""
    try:
        with Image.open(path) as img:
            return getattr(img, "n_frames", 1)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RenderError(f"{path.name}: cannot read image ({exc})") from exc


def render_image(path: Path, pages: list[int] | None = None) -> list[Image.Image]:
    try:
        with Image.open(path) as img:
            frames = [ImageOps.exif_transpose(f.copy()) for f in ImageSequence.Iterator(img)]
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RenderError(f"{path.name}: cannot read image ({exc})") from exc
    if pages is not None:
        bad = [p for p in pages if not 0 <= p < len(frames)]
        if bad:
            raise RenderError(f"{path.name}: page {bad[0] + 1} does not exist (image has {len(frames)})")
        frames = [frames[p] for p in pages]
    return [image_to_page(f) for f in frames]
