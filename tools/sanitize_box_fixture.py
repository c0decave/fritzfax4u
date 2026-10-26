#!/usr/bin/env python3
"""Remove private content from a FRITZ!Box fax archive PDF so it can serve as a test fixture.

    python tools/sanitize_box_fixture.py IN.pdf OUT.pdf OUT.sff [--blank-rows N] [--replace OLD=NEW ...]

The page image is decoded, the first N rows (the header line with sender name and number) are
blanked, and the image is encoded again in exactly the box's format: CCITT G3 1D (Modified
Huffman), MSB first, no EOL codes, not byte aligned, no DecodeParms (measured on FRITZ!OS 8.25).
The PDF structure is kept; /Length and the xref table are rebuilt. OUT.sff gets the same page
as fritzfax would upload it. --replace changes literal text such as numbers in /Title
(OLD and NEW must have the same length).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from fritzfax.core import pdfccitt, sff  # noqa: E402


def encode_g3_1d(page: Image.Image) -> bytes:
    raw = page.tobytes()
    stride = page.width // 8
    codes = []
    for y in range(page.height):
        bits = "".join(f"{b:08b}" for b in raw[y * stride:(y + 1) * stride])
        white = True
        if bits[0] == "0":
            codes.append(sff._WHITE_CODES[0])  # noqa: SLF001
            white = False
        for run in sff._RUNS.findall(bits):  # noqa: SLF001
            codes.append((sff._WHITE_CODES if white else sff._BLACK_CODES)[len(run)])  # noqa: SLF001
            white = not white
    stream = "".join(codes)
    stream += "0" * (-len(stream) % 8)
    return int(stream, 2).to_bytes(len(stream) // 8, "big")


def rebuild_pdf(data: bytes, new_stream: bytes) -> bytes:
    m = re.search(rb"/Length (\d+)(\s*>>\s*stream\r?\n)", data)
    old_len = int(m.group(1))
    start = m.end()
    out = data[:m.start()] + b"/Length " + str(len(new_stream)).encode() + m.group(2) + new_stream + \
        data[start + old_len:]
    body, _, _ = out.partition(b"xref\n")
    offsets = {int(o.group(1)): o.start() for o in re.finditer(rb"(?m)^(\d+) 0 obj\b", body)}
    size = max(offsets) + 1
    xref = [b"xref\n", f"0 {size}\n".encode(), b"0000000000 65535 f \n"]
    xref += [f"{offsets[i]:010d} 00000 n \n".encode() for i in range(1, size)]
    trailer = out[out.index(b"trailer"):out.index(b"startxref")]
    return body + b"".join(xref) + trailer + f"startxref\n{len(body)}\n%%EOF".encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out_pdf")
    ap.add_argument("out_sff")
    ap.add_argument("--blank-rows", type=int, default=40)
    ap.add_argument("--replace", action="append", default=[])
    a = ap.parse_args()
    data = Path(a.src).read_bytes()
    (page,) = pdfccitt.extract_pages(data)
    page = page.copy()
    page.paste(1, (0, 0, page.width, a.blank_rows))
    pdf = rebuild_pdf(data, encode_g3_1d(page))
    for pair in a.replace:
        old, new = (s.encode() for s in pair.split("=", 1))
        if len(old) != len(new):
            raise SystemExit("--replace needs equal lengths")
        pdf = pdf.replace(old, new)
    Path(a.out_pdf).write_bytes(pdf)
    Path(a.out_sff).write_bytes(sff.encode_pages([page]))
    check = pdfccitt.extract_pages(pdf)[0]
    assert check.tobytes() == page.tobytes(), "re-encoded archive does not decode to the sanitized page"
    print(f"wrote {a.out_pdf} ({len(pdf)} bytes) and {a.out_sff}")


if __name__ == "__main__":
    main()
