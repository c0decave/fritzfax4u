"""SFF writer tests.

Two independent checks of the Modified Huffman coding:
  * round trip through our own decoder (fritzfax.core.sff.decode)
  * decoding the very same coded rows with libtiff (via Pillow): one SFF row is a
    byte-aligned MH line without EOL, which is exactly TIFF Compression=2 (CCITT RLE)
    with FillOrder=2 (LSB first). libtiff shares no code with us.
"""
import io
import random
import struct

import pytest
from PIL import Image, ImageChops

from fritzfax.core import sff

W = sff.LINE_WIDTH


def page(height=8, fill=255):
    return Image.new("1", (W, height), fill)


def rows_to_tiff(rows, width=W):
    data = b"".join(rows)
    height = len(rows)
    entries = [
        (256, 4, 1, width),          # ImageWidth
        (257, 4, 1, height),         # ImageLength
        (258, 3, 1, 1),              # BitsPerSample
        (259, 3, 1, 2),              # Compression = CCITT RLE (MH, byte aligned rows)
        (262, 3, 1, 0),              # Photometric = WhiteIsZero
        (266, 3, 1, 2),              # FillOrder = LSB first
        (273, 4, 1, 0),              # StripOffsets (patched below)
        (277, 3, 1, 1),              # SamplesPerPixel
        (278, 4, 1, height),         # RowsPerStrip
        (279, 4, 1, len(data)),      # StripByteCounts
    ]
    ifd_offset = 8
    ifd_size = 2 + len(entries) * 12 + 4
    data_offset = ifd_offset + ifd_size
    out = io.BytesIO()
    out.write(b"II*\x00" + struct.pack("<I", ifd_offset))
    out.write(struct.pack("<H", len(entries)))
    for tag, typ, count, value in entries:
        if tag == 273:
            value = data_offset
        if typ == 3:
            out.write(struct.pack("<HHIHH", tag, typ, count, value, 0))
        else:
            out.write(struct.pack("<HHII", tag, typ, count, value))
    out.write(struct.pack("<I", 0))
    out.write(data)
    out.seek(0)
    img = Image.open(out)
    img.load()
    return img.convert("1")


def assert_same(a, b):
    assert a.size == b.size
    assert ImageChops.difference(a.convert("L"), b.convert("L")).getbbox() is None


def patterns():
    rnd = random.Random(1234)
    white = page()
    black = page(fill=0)
    noise = Image.frombytes("1", (W, 16), bytes(rnd.getrandbits(8) for _ in range(W // 8 * 16)))
    checker = page(4)
    px = checker.load()
    for y in range(4):
        for x in range(W):
            px[x, y] = 0 if (x + y) % 2 else 255
    runs = page(6)
    px = runs.load()
    for y, length in enumerate([1, 63, 64, 65, 1664, 1727]):
        for x in range(length):
            px[x, y] = 0
    return {"white": white, "black": black, "noise": noise, "checker": checker, "runs": runs}


# --- happy path -----------------------------------------------------------------

def test_file_and_page_header_layout():
    data = sff.encode_pages([page(3)])
    assert data[:4] == b"Sfff"
    assert data[4] == 1  # version
    first_page = struct.unpack_from("<H", data, 10)[0]
    assert first_page == 28
    hdr = data[first_page:first_page + 18]
    assert hdr[0] == 254 and hdr[1] == 16
    assert hdr[2] == 1  # vertical resolution: fine
    assert hdr[3] == 0  # horizontal resolution: 203 dpi
    assert hdr[4] == 0  # coding: MH
    assert struct.unpack_from("<H", hdr, 6)[0] == W
    assert data[-2:] == b"\xfe\x00"


@pytest.mark.parametrize("name", list(patterns()))
def test_round_trip_with_own_decoder(name):
    src = patterns()[name]
    (decoded,) = sff.decode(sff.encode_pages([src]))
    assert_same(decoded, src)


@pytest.mark.parametrize("name", list(patterns()))
def test_independent_libtiff_decode(name):
    src = patterns()[name]
    (rows,) = sff.page_rows(sff.encode_pages([src]))
    assert len(rows) == src.height
    assert_same(rows_to_tiff(rows), src)


def test_multiple_pages_keep_order():
    pages = [page(2, 255), page(3, 0), page(1, 255)]
    decoded = sff.decode(sff.encode_pages(pages))
    assert [p.height for p in decoded] == [2, 3, 1]
    assert_same(decoded[1], pages[1])


# --- sad path -------------------------------------------------------------------

def test_wrong_width_is_rejected():
    with pytest.raises(sff.SffError):
        sff.encode_pages([Image.new("1", (1727, 2), 255)])


def test_no_pages_is_rejected():
    with pytest.raises(sff.SffError):
        sff.encode_pages([])


def test_non_bilevel_page_is_rejected():
    with pytest.raises(sff.SffError):
        sff.encode_pages([Image.new("L", (W, 2), 255)])


@pytest.mark.parametrize(
    "blob",
    [b"", b"PK\x03\x04garbage", b"Sfff\x01\x00", sff.encode_pages([page(2)])[:-30]],
)
def test_decode_rejects_broken_input(blob):
    with pytest.raises(sff.SffError):
        sff.decode(blob)


# --- edge cases -----------------------------------------------------------------

def test_long_rows_use_escape_length_prefix():
    checker = patterns()["checker"]
    data = sff.encode_pages([checker])
    (rows,) = sff.page_rows(data)
    assert all(len(r) > 216 for r in rows)
    # the escape prefix 0x00 + uint16 must occur right after the page header
    start = 28 + 18
    assert data[start] == 0
    assert struct.unpack_from("<H", data, start + 1)[0] == len(rows[0])


def test_short_rows_use_single_byte_prefix():
    data = sff.encode_pages([page(1)])
    (rows,) = sff.page_rows(data)
    assert 0 < len(rows[0]) <= 216
    assert data[28 + 18] == len(rows[0])


def test_rows_start_with_white_run_even_if_first_pixel_is_black():
    img = page(1)
    img.putpixel((0, 0), 0)
    (decoded,) = sff.decode(sff.encode_pages([img]))
    assert decoded.getpixel((0, 0)) == 0
    assert decoded.getpixel((1, 0)) == 255


def test_full_a4_page_encodes_quickly():
    import time

    a4 = Image.new("1", (W, sff.A4_LINES), 255)
    t = time.perf_counter()
    sff.encode_pages([a4])
    assert time.perf_counter() - t < 2.0


# --- page count (review 2026-10-25: a header line at 22:12 made "2 pages" out of 1) --------------

def _one_page_with_marker_bytes_in_a_row():
    from PIL import Image

    data = sff.encode_pages([Image.new("1", (sff.LINE_WIDTH, 1), 1)])
    assert data.endswith(b"\xfe\x00")
    return data[:-2] + b"\x02\xfe\x10" + b"\xfe\x00"  # a coded row whose bytes look like a page header


def test_page_count_parses_instead_of_searching_bytes():
    data = _one_page_with_marker_bytes_in_a_row()
    assert data.count(b"\xfe\x10") == 2  # what a byte search would say
    assert sff.page_count(data) == 1


def test_page_count_of_several_pages():
    from PIL import Image

    pages = [Image.new("1", (sff.LINE_WIDTH, 3), 1) for _ in range(3)]
    assert sff.page_count(sff.encode_pages(pages)) == 3


def test_page_count_of_garbage_is_an_error():
    with pytest.raises(sff.SffError):
        sff.page_count(b"not an sff document at all")
