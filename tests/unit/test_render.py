"""Rendering tests: every source becomes 1728 x 2287 bilevel A4 fax pages."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fritzfax.core import render, sff
from fritzfax.core.errors import BoxError
from fritzfax.core.render import geometry as g

A4_150 = (1240, 1754)  # A4 at 150 dpi


def make_pdf(path: Path, pages=1, landscape=False, box_mm=(20, 20, 70, 40)):
    """Write a PDF (via Pillow) with a black rectangle at box_mm=(x0,y0,x1,y1) on every page."""
    size = A4_150[::-1] if landscape else A4_150
    imgs = []
    for n in range(pages):
        img = Image.new("RGB", size, "white")
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = (v * 150 / 25.4 for v in box_mm)
        d.rectangle([x0, y0, x1, y1], fill="black")
        d.text((size[0] // 2, size[1] - 100), f"page {n + 1}", fill="black")
        imgs.append(img)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=150)
    return path


# an A4 page (210 mm) is centered on the 215 mm fax line
A4_X_OFFSET_MM = (215 - 210) / 2


def black_at(img, x_mm, y_mm):
    """x_mm is measured on the A4 source page."""
    x = round((x_mm + A4_X_OFFSET_MM) * g.PX_PER_MM_X)
    return img.getpixel((x, round(y_mm * g.PX_PER_MM_Y))) == 0


# --- geometry ---------------------------------------------------------------------

def test_page_geometry_constants():
    assert g.PAGE_WIDTH == sff.LINE_WIDTH == 1728
    assert g.PAGE_HEIGHT == sff.A4_LINES == 2287


# --- document mode: PDF is the fax --------------------------------------------------

def test_one_page_pdf_becomes_one_fax_page(tmp_path):
    pdf = make_pdf(tmp_path / "letter.pdf")
    pages = render.render_document(render.Document(files=[pdf]))
    assert len(pages) == 1
    assert pages[0].mode == "1" and pages[0].size == (1728, 2287)
    # 1:1 placement: the rectangle stays at 20..70 mm x 20..40 mm
    assert black_at(pages[0], 45, 30)
    assert not black_at(pages[0], 15, 30)
    assert not black_at(pages[0], 45, 50)
    # transparent QtPdf background must end up white, not black
    assert not black_at(pages[0], 150, 200)


def test_multi_page_pdf_and_page_selection(tmp_path):
    pdf = make_pdf(tmp_path / "three.pdf", pages=3)
    assert len(render.render_document(render.Document(files=[pdf]))) == 3
    doc = render.Document(files=[render.FileSpec(pdf, pages=[0, 2])])
    assert len(render.render_document(doc)) == 2


def test_landscape_pdf_is_rotated_to_portrait(tmp_path):
    pdf = make_pdf(tmp_path / "land.pdf", landscape=True)
    (page,) = render.render_document(render.Document(files=[pdf]))
    assert page.size == (1728, 2287)
    assert page.histogram()[0] > 1000  # content survived the rotation


def test_parse_page_ranges():
    assert render.parse_page_ranges("1-3,5", 10) == [0, 1, 2, 4]
    assert render.parse_page_ranges("", 3) == [0, 1, 2]
    with pytest.raises(ValueError):
        render.parse_page_ranges("4", 3)
    with pytest.raises(ValueError):
        render.parse_page_ranges("x", 3)


# --- images ---------------------------------------------------------------------------

def test_png_with_alpha_gets_white_background(tmp_path):
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([10, 10, 100, 100], fill=(0, 0, 0, 255))
    img.save(tmp_path / "a.png")
    (page,) = render.render_document(render.Document(files=[tmp_path / "a.png"]))
    hist = page.histogram()
    assert hist[255] > hist[0] > 0


def test_multi_frame_tiff_gives_one_page_per_frame(tmp_path):
    frames = [Image.new("L", (800, 1100), v) for v in (255, 200, 255)]
    frames[0].save(tmp_path / "m.tif", save_all=True, append_images=frames[1:])
    assert len(render.render_document(render.Document(files=[tmp_path / "m.tif"]))) == 3


def test_image_with_dpi_keeps_physical_size(tmp_path):
    # 50 mm wide black bar at 300 dpi must stay 50 mm wide
    w = round(50 / 25.4 * 300)
    img = Image.new("L", (w, 100), 0)
    img.save(tmp_path / "bar.png", dpi=(300, 300))
    (page,) = render.render_document(render.Document(files=[tmp_path / "bar.png"]))
    row = [page.getpixel((x, 5)) for x in range(1728)]
    width_px = row.count(0)
    assert abs(width_px / g.PX_PER_MM_X - 50) < 1.5


def test_huge_image_is_scaled_to_fit(tmp_path):
    Image.new("L", (6000, 9000), 0).save(tmp_path / "big.png")
    (page,) = render.render_document(render.Document(files=[tmp_path / "big.png"]))
    assert page.size == (1728, 2287)


# --- cover letter mode ------------------------------------------------------------------

def test_text_pages_wrap_and_paginate():
    text = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 30 + "\n") * 12
    pages = render.render_document(render.Document(text=text, subject="Test"))
    assert len(pages) >= 2
    assert all(p.size == (1728, 2287) for p in pages)


def test_no_text_no_files_is_an_error():
    with pytest.raises(render.RenderError):
        render.render_document(render.Document())


def test_text_then_attachment(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf")
    pages = render.render_document(render.Document(text="Hallo", files=[pdf]))
    assert len(pages) == 2


def test_cover_page_is_optional(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf")
    doc = render.Document(files=[pdf], cover=render.Cover(to_name="Amt", to_number="0531 1", from_name="Ich",
                                                          from_number="030 1", subject="Antrag"))
    assert len(render.render_document(doc)) == 2


# --- header line --------------------------------------------------------------------------

def _strip_black(page, mm=4):
    h = round(mm * g.PX_PER_MM_Y)
    return page.crop((0, 0, 1728, h)).histogram()[0]


def test_header_overlay_off_shrink(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf")
    header = render.Header(left="25.10.2026 12:00  Test Sender", right="Fax 030 1")
    off = render.render_document(render.Document(files=[pdf]))[0]
    over = render.render_document(render.Document(files=[pdf], header=header, header_mode="overlay"))[0]
    shrink = render.render_document(render.Document(files=[pdf], header=header, header_mode="shrink"))[0]
    assert _strip_black(off) == 0
    assert _strip_black(over) > 0 and _strip_black(shrink) > 0
    # overlay keeps the content where it was, shrink moves it
    assert black_at(over, 21, 21) and black_at(over, 69, 39)
    assert not black_at(shrink, 20.2, 20.2)  # shrunk towards the center and moved down


def test_header_shows_page_numbers(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", pages=2)
    header = render.Header(left="L", right="R")
    pages = render.render_document(render.Document(files=[pdf], header=header))
    # page 1 and page 2 headers differ (n/N)
    assert pages[0].crop((0, 0, 1728, 30)).tobytes() != pages[1].crop((0, 0, 1728, 30)).tobytes()


# --- binarization -----------------------------------------------------------------------------

def test_binarize_modes():
    gray = Image.new("L", (1728, 200), 128)
    thr = render.binarize(gray, "threshold")
    dith = render.binarize(gray, "dither")
    assert thr.histogram()[255] in (0, 1728 * 200) or thr.histogram()[0] in (0, 1728 * 200)
    assert 0.3 < dith.histogram()[0] / (1728 * 200) < 0.7
    assert render.binarize(gray, "auto").histogram() == dith.histogram()
    lineart = Image.new("L", (1728, 200), 255)
    ImageDraw.Draw(lineart).text((10, 10), "abc", fill=0)
    assert render.binarize(lineart, "auto").histogram() == render.binarize(lineart, "threshold").histogram()
    with pytest.raises(ValueError):
        render.binarize(gray, "bogus")


# --- sad path ----------------------------------------------------------------------------------

def test_broken_pdf(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4 nonsense")
    with pytest.raises(render.RenderError, match="broken.pdf"):
        render.render_document(render.Document(files=[p]))


def test_missing_file(tmp_path):
    with pytest.raises(render.RenderError, match="nope.pdf"):
        render.render_document(render.Document(files=[tmp_path / "nope.pdf"]))


def test_unsupported_type(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(render.RenderError, match="unsupported"):
        render.render_document(render.Document(files=[p]))


def test_page_limit(tmp_path):
    pdf = make_pdf(tmp_path / "three.pdf", pages=3)
    with pytest.raises(render.RenderError, match="pages"):
        render.render_document(render.Document(files=[pdf], max_pages=2))


def test_render_error_is_box_independent():
    assert not issubclass(render.RenderError, BoxError)


def test_cover_labels_follow_language(monkeypatch):
    assert render.cover_labels("de")["to"] == "An"
    assert render.cover_labels("en")["to"] == "To"
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert render.cover_labels("auto")["subject"] == "Betreff"
    monkeypatch.setenv("LANG", "C")
    assert render.cover_labels("auto")["subject"] == "Subject"


def test_default_header_uses_display_number():
    from fritzfax.core import job

    h = job.default_header("Name", "87654321", display_number="+49 30 87654321", when=0)
    assert h.right == "Fax +49 30 87654321" and "Name" in h.left
    assert job.default_header("Name", "87654321", when=0).right == "Fax 87654321"


def test_fc_match_runs_with_the_system_library_path(monkeypatch, tmp_path):
    # the frozen binary's own fontconfig is older than the system's configuration (live 2026-10-26)
    import subprocess
    import sys

    from fritzfax.core.render import fonts

    bundle = tmp_path / "_internal"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr(fonts.shutil, "which", lambda name: "/usr/bin/fc-match")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(fonts.subprocess, "run", run)
    fonts.find_font_file(override="")
    assert "env" in seen and "LD_LIBRARY_PATH" not in seen["env"]


# --- where every page comes from (removing pages in the preview, operator 2026-10-26) --------------

def test_origins_name_the_source_of_every_page(tmp_path):
    from tests_helpers import one_page_pdf

    a = one_page_pdf(tmp_path / "a.pdf", pages=3)
    b = one_page_pdf(tmp_path / "b.pdf", pages=2)
    cover = render.Cover(to_name="X", to_number="0301", from_name="Y", from_number="1", subject="S",
                         date="1.1.2026", labels=render.cover_labels("en"))
    doc = render.Document(files=[render.FileSpec(a, [2, 0]), b], text="Hallo", cover=cover)
    pages, origins = render.render_document_with_origins(doc)
    assert len(pages) == len(origins) == 1 + 1 + 2 + 2
    assert [(o.kind, o.file_index, o.source_page) for o in origins] == [
        ("cover", None, None), ("text", None, None),
        ("file", 0, 2), ("file", 0, 0), ("file", 1, 0), ("file", 1, 1)]


def test_render_document_still_returns_only_the_pages(tmp_path):
    from tests_helpers import one_page_pdf

    pages = render.render_document(render.Document(files=[one_page_pdf(tmp_path / "a.pdf", pages=2)]))
    assert len(pages) == 2


@pytest.mark.parametrize("pages, text", [
    ([0, 1, 2, 4], "1-3,5"), ([0], "1"), ([3, 1], "4,2"), ([0, 1, 3, 4, 6], "1-2,4-5,7"), ([], "")])
def test_format_page_ranges(pages, text):
    assert render.format_page_ranges(pages) == text
    if pages:
        assert render.parse_page_ranges(text, 10) == pages


def test_page_ranges_without_a_known_page_count():
    # office documents: the page count is only known after LibreOffice converted them
    assert render.parse_page_ranges("2,4-5", None) == [1, 3, 4]
    with pytest.raises(ValueError):
        render.parse_page_ranges("0", None)
