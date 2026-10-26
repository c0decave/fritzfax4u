"""Extract CCITT fax images from a PDF and decode them with libtiff (via Pillow).

Used to compare the box's archive copy with the pages that were sent. Deliberately no
rendering: pdfium (QtPdf) changed 246 pixels of a CCITT G4 page in a measurement, libtiff
decoded the same stream exactly. Handles the PDFs of the FRITZ!Box (CCITT G3 1D, no
DecodeParms) and of Pillow (G4, BlackIs1). Only what fax PDFs use is supported.
"""
from __future__ import annotations

import io
import re
import struct

from PIL import Image


class PdfImageError(ValueError):
    pass


_OBJ = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_REF = re.compile(rb"(\d+)\s+\d+\s+R")


def _objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    ints = {int(m.group(1)): int(m.group(2))
            for m in re.finditer(rb"(\d+)\s+0\s+obj\s*(\d+)\s*endobj", data)}
    objs: dict[int, tuple[bytes, bytes | None]] = {}
    pos = 0
    while True:
        m = _OBJ.search(data, pos)
        if not m:
            break
        num = int(m.group(1))
        body_start = m.end()
        end = data.find(b"endobj", body_start)
        stream_at = data.find(b"stream", body_start)
        if stream_at != -1 and (end == -1 or stream_at < end):
            head = data[body_start:stream_at]
            length_m = re.search(rb"/Length\s+(\d+)(\s+\d+\s+R)?", head)
            if not length_m:
                raise PdfImageError(f"object {num}: stream without /Length")
            length = int(length_m.group(1))
            if length_m.group(2):
                if length not in ints:
                    raise PdfImageError(f"object {num}: unresolved /Length reference")
                length = ints[length]
            start = stream_at + len(b"stream")
            if data[start:start + 2] == b"\r\n":
                start += 2
            elif data[start:start + 1] in (b"\n", b"\r"):
                start += 1
            objs[num] = (head, data[start:start + length])
            pos = start + length
        else:
            if end == -1:
                break
            objs[num] = (data[body_start:end], None)
            pos = end + 6
    return objs


def _resolve_dict(objs, text: bytes, key: bytes) -> bytes:
    """Value of /key in text: an inline <<...>> dict or the body of a referenced object."""
    m = re.search(rb"/" + key + rb"\s*(<<|\d+\s+\d+\s+R)", text)
    if not m:
        return b""
    if m.group(1) == b"<<":
        depth, i = 0, m.start(1)
        while i < len(text):
            if text.startswith(b"<<", i):
                depth += 1
                i += 2
            elif text.startswith(b">>", i):
                depth -= 1
                i += 2
                if depth == 0:
                    return text[m.start(1):i]
            else:
                i += 1
        return text[m.start(1):]
    ref = int(_REF.match(m.group(1)).group(1))
    return objs.get(ref, (b"", None))[0]


def _page_order(objs) -> list[int]:
    pages_root = [n for n, (h, s) in objs.items() if s is None and re.search(rb"/Type\s*/Pages\b", h)
                  and not re.search(rb"/Parent\b", h)]
    order: list[int] = []

    def walk(n, depth=0):
        if depth > 50 or n not in objs:
            return
        head = objs[n][0]
        if re.search(rb"/Type\s*/Pages\b", head):
            kids = re.search(rb"/Kids\s*\[(.*?)\]", head, re.S)
            for k in _REF.finditer(kids.group(1) if kids else b""):
                walk(int(k.group(1)), depth + 1)
        elif re.search(rb"/Type\s*/Page\b", head):
            order.append(n)

    for root in pages_root:
        walk(root)
    return order


def _int(head: bytes, key: bytes, default: int) -> int:
    m = re.search(rb"/" + key + rb"\s+(-?\d+)", head)
    return int(m.group(1)) if m else default


def _bool(head: bytes, key: bytes) -> bool:
    return bool(re.search(rb"/" + key + rb"\s+true", head))


def _decode(head: bytes, stream: bytes) -> Image.Image:
    arr = re.search(rb"/Filter\s*\[(.*?)\]", head, re.S)
    one = re.search(rb"/Filter\s*/(\w+)", head)
    names = re.findall(rb"/(\w+)", arr.group(1)) if arr else ([one.group(1)] if one else [])
    if names != [b"CCITTFaxDecode"]:
        raise PdfImageError("unsupported image encoding: " + (b",".join(names) or b"none").decode())
    width, height = _int(head, b"Width", 0), _int(head, b"Height", 0)
    params = head[head.find(b"/DecodeParms"):] if b"/DecodeParms" in head else b""
    k = _int(params, b"K", 0)
    columns = _int(params, b"Columns", width)
    rows = _int(params, b"Rows", height)
    black_is_1 = _bool(params, b"BlackIs1")
    byte_align = _bool(params, b"EncodedByteAlign")
    if k < 0:
        compression, options = 4, []
    else:
        t4 = (1 if k > 0 else 0) | (4 if byte_align else 0)
        compression, options = 3, [(292, 4, 1, t4)]
    entries = sorted([(256, 4, 1, columns), (257, 4, 1, rows), (258, 3, 1, 1), (259, 3, 1, compression),
                      (262, 3, 1, 1 if black_is_1 else 0), (273, 4, 1, 0), (277, 3, 1, 1), (278, 4, 1, rows),
                      (279, 4, 1, len(stream))] + options)
    offset = 8 + 2 + len(entries) * 12 + 4
    out = io.BytesIO()
    out.write(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(entries)))
    for tag, typ, count, value in entries:
        value = offset if tag == 273 else value
        out.write(struct.pack("<HHIHH", tag, typ, count, value, 0) if typ == 3 else
                  struct.pack("<HHII", tag, typ, count, value))
    out.write(struct.pack("<I", 0) + stream)
    out.seek(0)
    try:
        img = Image.open(out)
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise PdfImageError(f"cannot decode CCITT image: {exc}") from exc
    return img.convert("1")


def extract_pages(data: bytes) -> list[Image.Image]:
    """One bilevel image per PDF page (the first image XObject of the page)."""
    if not data.startswith(b"%PDF"):
        raise PdfImageError("not a PDF")
    objs = _objects(data)
    pages = []
    for page in _page_order(objs):
        resources = _resolve_dict(objs, objs[page][0], b"Resources")
        xobjects = _resolve_dict(objs, resources, b"XObject")
        image = None
        for ref in _REF.finditer(xobjects):
            head, stream = objs.get(int(ref.group(1)), (b"", None))
            if stream is not None and re.search(rb"/Subtype\s*/Image", head):
                image = _decode(head, stream)
                break
        if image is None:
            raise PdfImageError(f"page {len(pages) + 1} has no image")
        pages.append(image)
    if not pages:
        raise PdfImageError("the PDF has no pages with images")
    return pages
