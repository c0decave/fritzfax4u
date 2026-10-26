"""Text pages (cover letter mode) and the optional cover sheet.

Text is drawn on a square-pixel A4 canvas and then resampled to the fax line spacing,
so letters are not stretched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from . import geometry as g
from .fonts import font

MARGIN_X_MM = 20
MARGIN_TOP_MM = 20
MARGIN_BOTTOM_MM = 20


def _mm(v: float) -> int:
    return round(v * g.PX_PER_MM_X)


def _pt(points: float) -> int:
    return round(points * 25.4 / 72 * g.PX_PER_MM_X)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if draw.textlength(candidate, font=fnt) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # hard-break words longer than a line
            while draw.textlength(word, font=fnt) > width:
                cut = len(word)
                while cut > 1 and draw.textlength(word[:cut], font=fnt) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _canvas():
    img = Image.new("L", (g.PAGE_WIDTH, g.SQUARE_HEIGHT), 255)
    return img, ImageDraw.Draw(img)


def text_pages(text: str, subject: str = "", font_size_pt: float = 11, font_file: str = "") -> list[Image.Image]:
    body_font = font(_pt(font_size_pt), override=font_file)
    title_font = font(_pt(font_size_pt + 3), bold=True, override=font_file)
    line_h = round(_pt(font_size_pt) * 1.35)
    width = g.PAGE_WIDTH - 2 * _mm(MARGIN_X_MM)
    bottom = g.SQUARE_HEIGHT - _mm(MARGIN_BOTTOM_MM)
    img, draw = _canvas()
    lines = wrap(draw, text, body_font, width)
    pages = []
    y = _mm(MARGIN_TOP_MM)
    if subject:
        for line in wrap(draw, subject, title_font, width):
            draw.text((_mm(MARGIN_X_MM), y), line, font=title_font, fill=0)
            y += round(line_h * 1.3)
        y += line_h
    for line in lines:
        if y + line_h > bottom:
            pages.append(g.square_to_fax(img))
            img, draw = _canvas()
            y = _mm(MARGIN_TOP_MM)
        draw.text((_mm(MARGIN_X_MM), y), line, font=body_font, fill=0)
        y += line_h
    pages.append(g.square_to_fax(img))
    return pages


COVER_LABELS = {
    "en": {"title": "FAX", "to": "To", "fax": "Fax", "from": "From", "date": "Date", "pages": "Pages",
           "subject": "Subject"},
    "de": {"title": "TELEFAX", "to": "An", "fax": "Fax", "from": "Von", "date": "Datum", "pages": "Seiten",
           "subject": "Betreff"},
}


def cover_labels(language: str) -> dict[str, str]:
    """Labels for 'de'/'en'; 'auto' follows $LANG."""
    import os

    if language not in COVER_LABELS:
        language = "de" if os.environ.get("LANG", "").startswith("de") else "en"
    return dict(COVER_LABELS[language])


@dataclass
class Cover:
    to_name: str = ""
    to_number: str = ""
    from_name: str = ""
    from_number: str = ""
    subject: str = ""
    message: str = ""
    date: str = ""
    labels: dict[str, str] = field(default_factory=lambda: dict(COVER_LABELS["en"]))


def cover_page(cover: Cover, total_pages: int, font_file: str = "") -> Image.Image:
    img, draw = _canvas()
    lab = cover.labels
    x = _mm(MARGIN_X_MM)
    y = _mm(MARGIN_TOP_MM)
    draw.text((x, y), lab.get("title", "FAX"), font=font(_pt(28), bold=True, override=font_file), fill=0)
    y += _pt(28) * 2
    key_font, val_font = font(_pt(12), bold=True, override=font_file), font(_pt(12), override=font_file)
    rows = [(lab["to"], cover.to_name), (lab["fax"], cover.to_number), ("", ""),
            (lab["from"], cover.from_name), (lab["fax"], cover.from_number), ("", ""),
            (lab["date"], cover.date), (lab["pages"], str(total_pages)), (lab["subject"], cover.subject)]
    for key, value in rows:
        if key:
            draw.text((x, y), key, font=key_font, fill=0)
            draw.text((x + _mm(35), y), value, font=val_font, fill=0)
        y += round(_pt(12) * 1.6)
    y += _pt(12)
    draw.line([(x, y), (g.PAGE_WIDTH - x, y)], fill=0, width=3)
    y += _pt(12) * 2
    width = g.PAGE_WIDTH - 2 * x
    for line in wrap(draw, cover.message, val_font, width):
        if y > g.SQUARE_HEIGHT - _mm(MARGIN_BOTTOM_MM):
            break
        draw.text((x, y), line, font=val_font, fill=0)
        y += round(_pt(12) * 1.35)
    return g.square_to_fax(img)
