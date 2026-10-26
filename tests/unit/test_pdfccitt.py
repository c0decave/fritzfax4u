"""Extracting CCITT fax images from PDFs without pdfium (decoded by libtiff).

Measured: pdfium's rendering of a CCITT G4 image differed in 246 pixels from the source,
while libtiff decoded the same stream bit-exactly. The verification therefore never renders.
"""
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fritzfax.core import pdfccitt, sff, verify

FIX = Path(__file__).parent.parent / "fixtures"


def test_real_box_archive_equals_the_sent_sff():
    # live FRITZ!OS 8.25 archive copy (G3 1D, no DecodeParms) of the fax uploaded as box_archive_1page.sff
    pages = pdfccitt.extract_pages((FIX / "box_archive_1page.pdf").read_bytes())
    sent = sff.decode((FIX / "box_archive_1page.sff").read_bytes())
    assert [p.size for p in pages] == [(1728, 2287)]
    assert verify.compare_pages(sent, pages) == (True, [0])


def test_real_received_fax_has_two_a4_pages():
    # measured: the receiving box records 2289 lines for our 2287-line pages
    pages = pdfccitt.extract_pages((FIX / "box_received_2pages.pdf").read_bytes())
    assert len(pages) == 2
    assert all(p.width == 1728 and abs(p.height - sff.A4_LINES) <= 5 for p in pages)
    assert all(p.histogram()[0] > 1000 for p in pages)  # black content present


def test_pillow_g4_pdf_multi_page_round_trip():
    pages = []
    for i in range(3):
        img = Image.new("1", (1728, 400), 1)
        ImageDraw.Draw(img).text((100 + i * 50, 100), f"Seite {i}", fill=0)
        pages.append(img)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    assert verify.compare_pages(pages, pdfccitt.extract_pages(buf.getvalue())) == (True, [0, 0, 0])


def test_negative_control_detects_a_changed_archive():
    pages = pdfccitt.extract_pages((FIX / "box_archive_1page.pdf").read_bytes())
    sent = sff.decode((FIX / "box_archive_1page.sff").read_bytes())
    sent[0].paste(0, (10, 10, 20, 20))
    same, diffs = verify.compare_pages(sent, pages)
    assert same is False and diffs[0] == 100


@pytest.mark.parametrize("blob", [b"", b"%PDF-1.4\n%%EOF", b"not a pdf at all"])
def test_no_images_is_an_error(blob):
    with pytest.raises(pdfccitt.PdfImageError):
        pdfccitt.extract_pages(blob)


def test_non_ccitt_image_is_an_error():
    buf = io.BytesIO()
    Image.new("L", (100, 100), 128).save(buf, "PDF")  # DCTDecode
    with pytest.raises(pdfccitt.PdfImageError, match="DCTDecode"):
        pdfccitt.extract_pages(buf.getvalue())
