"""Proof that a fax was really sent and confirmed by the receiving fax machine.

What the box offers (FRITZ!OS 8.25; it records no remote station id):
  1. the final status of the send job: reason 0 means the T.30 session ended normally, i.e. the
     receiver confirmed every page (MCF) - the "OK" of a classic transmission report
  2. the fax journal entry of the send (pages, error text)
  3. the archive copy of the sent pages (PDF in the journal); measured: bit-identical to the
     uploaded SFF, so comparing it proves *which content* the box transmitted
  4. the call list: an outgoing call to the destination at the same box time as the journal entry

verdict: "verified" when all four agree, "failed" when the box reports an error, otherwise
"unverified" with the list of what is missing or does not match.
"""
from __future__ import annotations

import csv
import hashlib
import io
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageDraw

from . import journal
from .errors import BoxError
from .faxapi import FaxStatus
from .render.fonts import font
from .session import BoxClient

CALL_LIST = "/fon_num/foncalls_list.lua"
OUTGOING = "4"


@dataclass
class Call:
    kind: str
    date: str
    number: str
    duration: str

    @property
    def outgoing(self) -> bool:
        return self.kind == OUTGOING


@dataclass
class Evidence:
    status: int = -1
    status_text: str = ""
    reason: int = 0
    journal_id: int | None = None
    journal_date: str = ""
    journal_pages: int | None = None
    journal_error: str = ""
    archive_pdf: str = ""
    archive_sha256: str = ""
    archive_pages: int | None = None
    archive_matches: bool | None = None
    differing_pixels: list[int] = field(default_factory=list)
    call_found: bool = False
    call_date: str = ""
    call_duration: str = ""
    verdict: str = "unverified"
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_call_list(text: str) -> list[Call]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("sep="):
        lines = lines[1:]
    if not lines or not lines[0].startswith("Typ;"):
        return []
    calls = []
    for row in csv.reader(io.StringIO("\n".join(lines[1:])), delimiter=";"):
        if len(row) >= 8:
            calls.append(Call(row[0], row[1], row[3], row[7]))
    return calls


def _count_diff(a: Image.Image, b: Image.Image) -> int:
    if a.size != b.size:
        return a.width * a.height
    hist = ImageChops.difference(a.convert("L"), b.convert("L")).histogram()
    return a.width * a.height - hist[0]


def compare_pages(sent: list[Image.Image], archived: list[Image.Image]) -> tuple[bool, list[int]]:
    diffs = [_count_diff(a, b) for a, b in zip(sent, archived)]
    return len(sent) == len(archived) and all(d == 0 for d in diffs), diffs


def render_archive(path: Path) -> list[Image.Image]:
    """The pages of the box's archive PDF, decoded from its CCITT images with libtiff.

    Not rendered with pdfium: that changed 246 pixels of a G4 page in a measurement."""
    from .pdfccitt import extract_pages

    return extract_pages(Path(path).read_bytes())


def journal_ids(client: BoxClient) -> set[int] | None:
    try:
        return {e.id for e in journal.list_faxes(client)}
    except BoxError:
        return None


def collect(client: BoxClient, dest: str, sent_pages: list[Image.Image], final: FaxStatus | None,
            before: set[int] | None, evidence_dir: Path, *, wait: float = 15.0, failed: bool = False,
            clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> Evidence:
    ev = Evidence()
    if final is not None:
        ev.status, ev.status_text, ev.reason = final.status, final.status_text, final.reason

    # 2. journal entry of this send
    entry = None
    if before is None:
        ev.problems.append("fax journal not readable before sending")
    else:
        deadline = clock() + wait
        while entry is None:
            try:
                new = [e for e in journal.list_faxes(client) if e.id not in before and e.sent and e.number == dest]
            except BoxError as exc:
                ev.problems.append(f"fax journal not readable: {exc}")
                break
            if new:
                entry = max(new, key=lambda e: e.id)
                break
            if clock() >= deadline:
                ev.problems.append("no journal entry for this fax")
                break
            sleep(min(1.0, wait / 10 or 0.01))
    if entry is not None:
        ev.journal_id, ev.journal_date = entry.id, entry.date
        ev.journal_pages, ev.journal_error = entry.pages, entry.error
        if entry.error:
            ev.problems.append(f"journal reports an error: {entry.error}")
        if entry.pages != len(sent_pages):
            ev.problems.append(f"journal shows {entry.pages} page(s), {len(sent_pages)} were sent")
        # 3. archive copy = what the box transmitted
        try:
            data = journal.download(client, entry)
            evidence_dir.mkdir(parents=True, exist_ok=True)
            path = evidence_dir / f"archive-{entry.id}.pdf"
            path.write_bytes(data)
            ev.archive_pdf, ev.archive_sha256 = str(path), hashlib.sha256(data).hexdigest()
            archived = render_archive(path)
            ev.archive_pages = len(archived)
            ev.archive_matches, ev.differing_pixels = compare_pages(sent_pages, archived)
            if not ev.archive_matches:
                ev.problems.append("archive copy of the box differs from the sent pages")
        except Exception as exc:  # noqa: BLE001 - any failure here means "not proven"
            ev.problems.append(f"archive copy not available: {exc}")

    # 4. call list
    try:
        resp = client.get(CALL_LIST, {"csv": ""})
        calls = parse_call_list(resp.text) if resp.status == 200 else []
        if resp.status != 200 or not calls and "Typ;" not in resp.text:
            ev.problems.append(f"call list not readable (HTTP {resp.status})")
        else:
            match = [c for c in calls if c.outgoing and c.number == dest and
                     (not ev.journal_date or c.date == ev.journal_date)]
            if match:
                ev.call_found, ev.call_date, ev.call_duration = True, match[0].date, match[0].duration
            else:
                ev.problems.append("no outgoing call to the destination in the call list")
    except BoxError as exc:
        ev.problems.append(f"call list not readable: {exc}")

    # 1. + verdict
    if failed or (final is not None and final.reason) or ev.journal_error:
        ev.verdict = "failed"
    elif final is not None and final.status in (1, 5) and not ev.problems:
        ev.verdict = "verified"
    else:
        if final is None or final.status not in (1, 5):
            ev.problems.append("no final status from the box")
        ev.verdict = "unverified"
    return ev


REPORT_LABELS = {
    "en": {"title": "Fax transmission report", "to": "To", "from": "From", "pages": "Pages", "result": "Result",
           "verified": "VERIFIED - sent and confirmed by the receiving fax", "failed": "FAILED",
           "unverified": "NOT VERIFIED", "status": "Box status", "journal": "Box journal", "archive": "Archive copy",
           "call": "Call list", "sha": "SHA-256 of the fax data", "problems": "Open points", "first": "Page 1",
           "identical": "bit-identical to the sent pages", "differs": "differs from the sent pages",
           "none": "-", "created": "Report created"},
    "de": {"title": "Fax-Sendebericht", "to": "An", "from": "Von", "pages": "Seiten", "result": "Ergebnis",
           "verified": "VERIFIZIERT - gesendet und vom Empfangsgerät bestätigt", "failed": "FEHLGESCHLAGEN",
           "unverified": "NICHT VERIFIZIERT", "status": "Status der Box", "journal": "Journal der Box",
           "archive": "Archivkopie", "call": "Anrufliste", "sha": "SHA-256 der Faxdaten", "problems": "Offene Punkte",
           "first": "Seite 1", "identical": "bitgleich mit den gesendeten Seiten",
           "differs": "weicht von den gesendeten Seiten ab", "none": "-", "created": "Bericht erstellt"},
}


_ROW_KEYS = ("to", "from", "pages", "result", "status", "journal", "archive", "call", "sha")


def report_value_offset(language: str) -> int:
    """x offset of the value column: longest label plus a gap (labels differ per language)."""
    lab = REPORT_LABELS.get(language, REPORT_LABELS["en"])
    bold = font(28, bold=True)
    return round(max(bold.getlength(lab[k]) for k in _ROW_KEYS)) + 40


def report_image(ev: Evidence, *, destination: str, sender: str, sent_pages: list[Image.Image],
                 sff_sha256: str, language: str = "en") -> Image.Image:
    """The transmission report as an A4 page at 150 dpi (1240 x 1754)."""
    from .render.text import wrap

    lab = REPORT_LABELS.get(language, REPORT_LABELS["en"])
    page = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(page)
    title, bold, body, small = font(46, bold=True), font(28, bold=True), font(26), font(20)
    x, y, right = 100, 100, 1240 - 100
    d.text((x, y), lab["title"], font=title, fill=0)
    y += 90
    archive = (lab["identical"] if ev.archive_matches else lab["differs"] if ev.archive_matches is False
               else lab["none"])
    rows = [
        (lab["to"], destination), (lab["from"], sender), (lab["pages"], str(len(sent_pages))),
        (lab["result"], lab.get(ev.verdict, ev.verdict)),
        (lab["status"], f"{ev.status_text} (status {ev.status}, reason 0x{ev.reason:04X})"),
        (lab["journal"], f"#{ev.journal_id} {ev.journal_date}, {ev.journal_pages} p {ev.journal_error}".strip()
         if ev.journal_id else lab["none"]),
        (lab["archive"], f"{archive} ({', '.join(map(str, ev.differing_pixels))} px)" if ev.differing_pixels
         else archive),
        (lab["call"], f"{ev.call_date}, {ev.call_duration}" if ev.call_found else lab["none"]),
        (lab["sha"], f"{sff_sha256[:32]} {sff_sha256[32:]}"),
    ]
    value_x = x + report_value_offset(language)
    for key, value in rows:
        d.text((x, y), key, font=bold, fill=0)
        for line in wrap(d, value, body, right - value_x) or [""]:
            d.text((value_x, y), line, font=body, fill=0)
            y += 38
        y += 8
    if ev.problems:
        y += 20
        d.text((x, y), lab["problems"], font=bold, fill=0)
        y += 44
        for p in ev.problems:
            for i, line in enumerate(wrap(d, p, small, right - x - 40)):
                d.text((x + 20, y), ("- " if i == 0 else "  ") + line, font=small, fill=0)
                y += 30
    y = max(y + 40, 900)
    if y + 600 < 1680:
        d.text((x, y), lab["first"], font=small, fill=0)
        page.paste(sent_pages[0].convert("L").resize((420, 556)), (x, y + 30))
        d.rectangle([x - 1, y + 29, x + 421, y + 587], outline=0, width=2)
    d.text((x, 1680), f"{lab['created']}: {time.strftime('%d.%m.%Y %H:%M:%S')}  -  fritzfax", font=small, fill=0)
    return page


def write_report(path: Path, ev: Evidence, *, destination: str, sender: str, sent_pages: list[Image.Image],
                 sff_sha256: str, language: str = "en") -> Path:
    """A one-page, printable transmission report (PDF)."""
    report_image(ev, destination=destination, sender=sender, sent_pages=sent_pages, sff_sha256=sff_sha256,
                 language=language).save(path, "PDF", resolution=150.0)
    return path
