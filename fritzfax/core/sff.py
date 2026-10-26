"""Structured Fax File (SFF) writer and reader.

The FRITZ!Box web interface renders a fax in the browser, encodes it as SFF with
ITU-T T.4 Modified Huffman (1D) rows and uploads it base64-encoded. This module
produces the same structure (FRITZ!OS 8.25, ``/js/sffcoder.js``):

* file header ``Sfff``, version 1, first page header at offset 28
* per page: ``FE 10`` page header, vertical resolution *fine*, 1728 pixels per line
* per row: MH-coded, LSB first, byte aligned; length prefix 1..216 or ``00 lo hi``
* end of document ``FE 00``

The MH code tables below are the ones from ITU-T T.4, not copied from AVM code.
"""
from __future__ import annotations

import re
import struct

from PIL import Image

LINE_WIDTH = 1728
FINE_LINES_PER_MM = 7.7
A4_LINES = round(297 * FINE_LINES_PER_MM)  # 2287

_WHITE_TERM = (
    "00110101 000111 0111 1000 1011 1100 1110 1111 10011 10100 00111 01000 001000 000011 "
    "110100 110101 101010 101011 0100111 0001100 0001000 0010111 0000011 0000100 0101000 "
    "0101011 0010011 0100100 0011000 00000010 00000011 00011010 00011011 00010010 00010011 "
    "00010100 00010101 00010110 00010111 00101000 00101001 00101010 00101011 00101100 "
    "00101101 00000100 00000101 00001010 00001011 01010010 01010011 01010100 01010101 "
    "00100100 00100101 01011000 01011001 01011010 01011011 01001010 01001011 00110010 "
    "00110011 00110100"
).split()
_WHITE_MAKEUP = (  # 64, 128, ..., 1728
    "11011 10010 010111 0110111 00110110 00110111 01100100 01100101 01101000 01100111 "
    "011001100 011001101 011010010 011010011 011010100 011010101 011010110 011010111 "
    "011011000 011011001 011011010 011011011 010011000 010011001 010011010 011000 010011011"
).split()
_BLACK_TERM = (
    "0000110111 010 11 10 011 0011 0010 00011 000101 000100 0000100 0000101 0000111 "
    "00000100 00000111 000011000 0000010111 0000011000 0000001000 00001100111 00001101000 "
    "00001101100 00000110111 00000101000 00000010111 00000011000 000011001010 000011001011 "
    "000011001100 000011001101 000001101000 000001101001 000001101010 000001101011 "
    "000011010010 000011010011 000011010100 000011010101 000011010110 000011010111 "
    "000001101100 000001101101 000011011010 000011011011 000001010100 000001010101 "
    "000001010110 000001010111 000001100100 000001100101 000001010010 000001010011 "
    "000000100100 000000110111 000000111000 000000100111 000000101000 000001011000 "
    "000001011001 000000101011 000000101100 000001011010 000001100110 000001100111"
).split()
_BLACK_MAKEUP = (
    "0000001111 000011001000 000011001001 000001011011 000000110011 000000110100 "
    "000000110101 0000001101100 0000001101101 0000001001010 0000001001011 0000001001100 "
    "0000001001101 0000001110010 0000001110011 0000001110100 0000001110101 0000001110110 "
    "0000001110111 0000001010010 0000001010011 0000001010100 0000001010101 0000001011010 "
    "0000001011011 0000001100100 0000001100101"
).split()
assert len(_WHITE_TERM) == len(_BLACK_TERM) == 64
assert len(_WHITE_MAKEUP) == len(_BLACK_MAKEUP) == LINE_WIDTH // 64

_FILE_HEADER = b"Sfff" + bytes([1, 0]) + struct.pack("<HHHII", 0, 0, 28, 0, 0) + b"fritzfax"
_PAGE_HEADER = bytes([254, 16, 1, 0, 0, 0]) + struct.pack("<HHII", LINE_WIDTH, 0, 1, 0)
_END = b"\xfe\x00"
_REVERSE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))
_BITS = [f"{i:08b}" for i in range(256)]
_RUNS = re.compile(r"1+|0+")


class SffError(ValueError):
    """Input is not a valid SFF document or page."""


def _code(table_term, table_makeup, length):
    if length < 64:
        return table_term[length]
    return table_makeup[length // 64 - 1] + table_term[length % 64]


_WHITE_CODES = [_code(_WHITE_TERM, _WHITE_MAKEUP, n) for n in range(LINE_WIDTH + 1)]
_BLACK_CODES = [_code(_BLACK_TERM, _BLACK_MAKEUP, n) for n in range(LINE_WIDTH + 1)]


def _encode_row(bits: str) -> bytes:
    """MH-encode one row given as a string of '1' (white) / '0' (black)."""
    parts = []
    white = True
    if bits[0] == "0":
        parts.append(_WHITE_CODES[0])
        white = False
    for run in _RUNS.findall(bits):
        parts.append((_WHITE_CODES if white else _BLACK_CODES)[len(run)])
        white = not white
    code = "".join(parts)
    code += "0" * (-len(code) % 8)
    return int(code, 2).to_bytes(len(code) // 8, "big").translate(_REVERSE)


def _length_prefix(n: int) -> bytes:
    return bytes([n]) if n <= 216 else b"\x00" + struct.pack("<H", n)


def encode_pages(pages: list[Image.Image]) -> bytes:
    """Encode bilevel pages (mode "1", 1728 pixels wide) into one SFF document."""
    if not pages:
        raise SffError("a fax needs at least one page")
    out = bytearray(_FILE_HEADER)
    cache: dict[str, bytes] = {}
    for number, img in enumerate(pages, 1):
        if img.mode != "1":
            raise SffError(f"page {number}: expected a bilevel image, got mode {img.mode}")
        if img.width != LINE_WIDTH:
            raise SffError(f"page {number}: width must be {LINE_WIDTH}, got {img.width}")
        out += _PAGE_HEADER
        raw = img.tobytes()
        stride = LINE_WIDTH // 8
        for y in range(img.height):
            row = raw[y * stride:(y + 1) * stride]
            bits = "".join(_BITS[b] for b in row)
            coded = cache.get(bits)
            if coded is None:
                coded = cache[bits] = _length_prefix(len(_encode_row(bits))) + _encode_row(bits)
            out += coded
    out += _END
    return bytes(out)


def page_count(data: bytes) -> int:
    """Number of pages, found by parsing the records.

    Never count the page header bytes FE 10 with a byte search: the same two bytes occur in coded
    rows (reproduced with a header line reading 25.10.2026 22:12)."""
    return len(page_rows(data))


def page_rows(data: bytes) -> list[list[bytes]]:
    """Return the coded rows of every page (white skip records expanded as None)."""
    if len(data) < 20 or data[:4] != b"Sfff":
        raise SffError("not an SFF document")
    pos = struct.unpack_from("<H", data, 10)[0]
    pages: list[list[bytes]] = []
    current: list[bytes] | None = None
    while True:
        if pos >= len(data):
            raise SffError("SFF document is truncated")
        rec = data[pos]
        if rec == 254:
            if pos + 1 >= len(data):
                raise SffError("SFF document is truncated")
            size = data[pos + 1]
            if size == 0:
                if current is not None:
                    pages.append(current)
                break
            if current is not None:
                pages.append(current)
            line_len = struct.unpack_from("<H", data, pos + 6)[0] if pos + 8 <= len(data) else 0
            if line_len != LINE_WIDTH:
                raise SffError(f"unsupported line length {line_len}")
            current = []
            pos += 2 + size
            continue
        if current is None:
            raise SffError("row data before first page header")
        if rec == 0:
            if pos + 3 > len(data):
                raise SffError("SFF document is truncated")
            n = struct.unpack_from("<H", data, pos + 1)[0]
            pos += 3
        elif rec <= 216:
            n = rec
            pos += 1
        elif rec <= 253:
            current.extend([None] * (rec - 216))  # type: ignore[list-item]
            pos += 1
            continue
        else:  # 255: illegal line / user info record
            if pos + 1 >= len(data):
                raise SffError("SFF document is truncated")
            pos += 2 + (data[pos + 1] if data[pos + 1] else 0)
            continue
        if pos + n > len(data):
            raise SffError("SFF document is truncated")
        current.append(data[pos:pos + n])
        pos += n
    if not pages:
        raise SffError("SFF document has no pages")
    return pages


def _decode_tables():
    white = {c: (n, n >= 64) for n, c in enumerate(_WHITE_TERM)}
    white.update({c: (64 * (i + 1), True) for i, c in enumerate(_WHITE_MAKEUP)})
    black = {c: (n, False) for n, c in enumerate(_BLACK_TERM)}
    black.update({c: (64 * (i + 1), True) for i, c in enumerate(_BLACK_MAKEUP)})
    return white, black


_DEC_WHITE, _DEC_BLACK = _decode_tables()


def _decode_row(coded: bytes) -> str:
    bits = "".join(_BITS[b] for b in coded.translate(_REVERSE))
    out = []
    total = 0
    white = True
    pos = 0
    while total < LINE_WIDTH:
        table = _DEC_WHITE if white else _DEC_BLACK
        for length in range(2, 14):
            entry = table.get(bits[pos:pos + length])
            if entry is not None:
                break
        else:
            raise SffError("invalid Modified Huffman code")
        run, is_makeup = entry
        pos += length
        out.append(("1" if white else "0") * run)
        total += run
        if not is_makeup:
            white = not white
    if total != LINE_WIDTH:
        raise SffError(f"row has {total} pixels instead of {LINE_WIDTH}")
    return "".join(out)


def decode(data: bytes) -> list[Image.Image]:
    """Decode an SFF document into bilevel pages."""
    images = []
    blank = "1" * LINE_WIDTH
    for rows in page_rows(data):
        bits = "".join(blank if r is None else _decode_row(r) for r in rows)
        packed = int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""
        images.append(Image.frombytes("1", (LINE_WIDTH, len(rows)), packed))
    return images
