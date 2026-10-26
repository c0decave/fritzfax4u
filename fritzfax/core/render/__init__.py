"""Turn files and text into bilevel A4 fax pages (1728 x 2287, fine resolution).

Two modes:
  * document: the pages of the given files are the fax (a one-page PDF is a one-page fax)
  * letter:   text (and subject) pages first, files afterwards
An optional cover sheet and an optional header line can be added in both modes.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from . import geometry
from .errors import RenderError
from .fonts import font
from .image import render_image
from .office import EXTENSIONS as OFFICE_EXTENSIONS
from .office import convert_to_pdf
from .pdf import render_pdf
from .text import COVER_LABELS, Cover, cover_labels, cover_page, text_pages

__all__ = ["COVER_LABELS", "cover_labels", "Cover", "Document", "FileSpec", "Header", "PageOrigin", "RenderError",
           "binarize", "format_page_ranges", "parse_page_ranges", "render_document", "render_document_with_origins",
           "IMAGE_EXTENSIONS", "OFFICE_EXTENSIONS", "SUPPORTED_EXTENSIONS"]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".pbm", ".pgm", ".ppm"}
SUPPORTED_EXTENSIONS = {".pdf"} | IMAGE_EXTENSIONS | OFFICE_EXTENSIONS
HEADER_MODES = ("overlay", "shrink", "off")
DITHER_MODES = ("auto", "threshold", "dither")
HEADER_MM = 4.0


@dataclass
class FileSpec:
    path: Path
    pages: list[int] | None = None  # 0-based selection, None = all

    def __post_init__(self):
        self.path = Path(self.path)


@dataclass(frozen=True)
class PageOrigin:
    """Where a fax page comes from: the cover sheet, the letter text, or page `source_page` (0-based)
    of file number `file_index` of the document."""
    kind: str  # "cover" | "text" | "file"
    file_index: int | None = None
    source_page: int | None = None


@dataclass
class Header:
    left: str
    right: str = ""


@dataclass
class Document:
    files: list = field(default_factory=list)
    text: str = ""
    subject: str = ""
    cover: Cover | None = None
    header: Header | None = None
    header_mode: str = "overlay"
    dither: str = "auto"
    max_pages: int = 50
    font_file: str = ""
    font_size_pt: float = 11
    soffice: str = ""


def parse_page_ranges(spec: str, count: int | None) -> list[int]:
    """'1-3,5' -> [0, 1, 2, 4]; empty means all pages. count None: not known yet (an office document
    before conversion), the renderer then reports a page that does not exist."""
    spec = (spec or "").strip()
    if not spec:
        if count is None:
            raise ValueError("no page selection and no page count")
        return list(range(count))
    result: list[int] = []
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if not m:
            raise ValueError(f"invalid page range: {part!r}")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a < 1 or b < a or (count is not None and b > count):
            raise ValueError(f"page range {part.strip()!r} outside 1-{count}")
        result.extend(i - 1 for i in range(a, b + 1) if i - 1 not in result)
    return result


def format_page_ranges(pages: list[int]) -> str:
    """[0, 1, 2, 4] -> '1-3,5' (0-based pages, kept in their order); the inverse of parse_page_ranges."""
    parts: list[str] = []
    i = 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        parts.append(f"{pages[i] + 1}-{pages[j] + 1}" if j > i else f"{pages[i] + 1}")
        i = j + 1
    return ",".join(parts)


def binarize(page: Image.Image, mode: str = "auto") -> Image.Image:
    if mode not in DITHER_MODES:
        raise ValueError(f"unknown dither mode {mode!r}")
    if mode == "auto":
        hist = page.histogram()
        mid = sum(hist[32:224]) / max(1, page.width * page.height)
        mode = "dither" if mid > 0.05 else "threshold"
    if mode == "dither":
        return page.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return page.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)


def _source_pages(spec: FileSpec, workdir: Path, doc: Document) -> list[Image.Image]:
    path = spec.path
    if not path.is_file():
        raise RenderError(f"{path.name}: file not found")
    ext = path.suffix.lower()
    if ext == ".pdf":
        return render_pdf(path, spec.pages)
    if ext in IMAGE_EXTENSIONS:
        return render_image(path, spec.pages)
    if ext in OFFICE_EXTENSIONS:
        return render_pdf(convert_to_pdf(path, workdir, doc.soffice), spec.pages)
    raise RenderError(f"{path.name}: unsupported file type {ext or '(none)'}")


def _apply_header(page: Image.Image, header: Header, mode: str, number: int, total: int) -> Image.Image:
    if mode == "off":
        return page
    strip = round(HEADER_MM * geometry.PX_PER_MM_Y)
    if mode == "shrink":
        factor = (geometry.PAGE_HEIGHT - strip) / geometry.PAGE_HEIGHT
        small = page.resize((round(page.width * factor), geometry.PAGE_HEIGHT - strip), Image.Resampling.LANCZOS)
        page = geometry.place(small, top=strip)
    else:
        page = page.copy()
    draw = ImageDraw.Draw(page)
    draw.rectangle([0, 0, geometry.PAGE_WIDTH, strip - 1], fill=255)
    fnt = font(22)
    margin = round(8 * geometry.PX_PER_MM_X)
    y = 2
    draw.text((margin, y), header.left, font=fnt, fill=0)
    right = f"{header.right}  {number}/{total}".strip()
    draw.text((geometry.PAGE_WIDTH - margin - draw.textlength(right, font=fnt), y), right, font=fnt, fill=0)
    return page


def render_document(doc: Document) -> list[Image.Image]:
    """Render a document into bilevel fax pages, ready for sff.encode_pages()."""
    return render_document_with_origins(doc)[0]


def render_document_with_origins(doc: Document) -> tuple[list[Image.Image], list[PageOrigin]]:
    """render_document() plus where every page comes from (to remove single pages in the preview)."""
    if doc.header_mode not in HEADER_MODES:
        raise ValueError(f"unknown header mode {doc.header_mode!r}")
    files = [f if isinstance(f, FileSpec) else FileSpec(f) for f in doc.files]
    if not files and not doc.text.strip() and not doc.subject.strip() and doc.cover is None:
        raise RenderError("nothing to send: no text and no files")
    pages: list[Image.Image] = []
    origins: list[PageOrigin] = []
    if doc.text.strip() or doc.subject.strip():
        pages += text_pages(doc.text, doc.subject, doc.font_size_pt, doc.font_file)
        origins += [PageOrigin("text")] * len(pages)
    with tempfile.TemporaryDirectory(prefix="fritzfax-") as tmp:
        for index, spec in enumerate(files):
            rendered = _source_pages(spec, Path(tmp), doc)
            sources = spec.pages if spec.pages is not None else range(len(rendered))
            pages += rendered
            origins += [PageOrigin("file", index, p) for p in sources]
            if len(pages) > doc.max_pages:
                break
    total = len(pages) + (1 if doc.cover else 0)
    if total > doc.max_pages:
        raise RenderError(f"the fax would have {total} pages, more than the limit of {doc.max_pages} pages")
    if doc.cover is not None:
        pages.insert(0, cover_page(doc.cover, total, doc.font_file))
        origins.insert(0, PageOrigin("cover"))
    if doc.header is not None:
        pages = [_apply_header(p, doc.header, doc.header_mode, i, total) for i, p in enumerate(pages, 1)]
    return [binarize(p, doc.dither) for p in pages], origins
