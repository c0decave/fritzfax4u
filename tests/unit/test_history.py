"""Send log: when, what, to whom, with which content."""
import json
from pathlib import Path
import sqlite3

import pytest
from PIL import Image, ImageDraw

from fritzfax.core import history, sff


def pages(n=2):
    out = []
    for i in range(n):
        img = Image.new("1", (sff.LINE_WIDTH, sff.A4_LINES), 1)
        ImageDraw.Draw(img).rectangle([100, 100 + i * 50, 400, 300], fill=0)
        out.append(img)
    return out


def content(**kw):
    base = dict(sender_name="Test Sender", sender_number="87654321", subject="Antrag", text="Hallo",
                mode="letter", cover=True, files=[history.FileInfo("brief.pdf", "/tmp/brief.pdf", "ab" * 32, [0])])
    base.update(kw)
    return history.Content(**base)


# --- happy path -----------------------------------------------------------------

def test_job_keeps_content_pdf_sff_and_metadata(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    p = pages()
    data = sff.encode_pages(p)
    job_id = log.start_job(content(), p, data)
    log.record(job_id, "053149059113", True, "", 0, started=100.0, finished=140.0)
    (e,) = log.entries()
    assert (e.destination, e.ok, e.pages, e.sender_name, e.subject, e.text, e.mode, e.cover) == \
        ("053149059113", True, 2, "Test Sender", "Antrag", "Hallo", "letter", True)
    assert e.files[0].name == "brief.pdf" and e.files[0].sha256 == "ab" * 32
    assert e.started == 100.0 and e.finished == 140.0
    from pathlib import Path

    assert Path(e.sff_path).read_bytes() == data
    pdf = Path(e.content_pdf)
    assert pdf.read_bytes().startswith(b"%PDF")


def test_content_pdf_has_the_fax_pages_in_physical_size(tmp_path):
    from PySide6.QtPdf import QPdfDocument

    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(), pages(2), sff.encode_pages(pages(2)))
    log.record(job_id, "0301", True, "", 0)
    doc = QPdfDocument()
    doc.load(log.entries()[0].content_pdf)
    assert doc.pageCount() == 2
    size = doc.pagePointSize(0)
    assert abs(size.width() - 1728 / 204 * 72) < 2 and abs(size.height() - 2287 / 196 * 72) < 2


def test_several_destinations_share_one_content_copy(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0301", True, "", 0)
    log.record(job_id, "0302", False, "busy", 0x3490)
    a, b = log.entries()
    assert a.content_pdf == b.content_pdf and a.job_id == b.job_id
    assert len(list((tmp_path / "sent").glob("*.pdf"))) == 1


def test_text_log_line_per_destination(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0301", True, "", 0)
    log.record(job_id, "0302", False, "the box reports transmission error 0x3490", 0x3490)
    lines = (tmp_path / "send.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "OK" in lines[0] and "0301" in lines[0] and "brief.pdf" in lines[0] and "Antrag" in lines[0]
    assert "FAILED" in lines[1] and "0x3490" in lines[1]
    assert lines[0].split("\t")[0][:4].isdigit()  # ISO timestamp first


def test_file_info_hashes_the_file(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    info = history.FileInfo.of(f, pages=None)
    assert info.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert info.name == "a.pdf"


# --- sad / edge -----------------------------------------------------------------------

def test_old_database_is_migrated(tmp_path):
    db = tmp_path / "h.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sends (id INTEGER PRIMARY KEY, ts REAL, destination TEXT, sender_number TEXT, "
                "pages INTEGER, ok INTEGER, error TEXT, reason INTEGER, sff_path TEXT)")
    con.execute("INSERT INTO sends (ts, destination, sender_number, pages, ok, error, reason, sff_path) "
                "VALUES (1.0, '0301', '1', 1, 1, '', 0, '')")
    con.commit()
    con.close()
    log = history.History(db, tmp_path / "sent")
    (old,) = log.entries()
    assert old.destination == "0301" and old.subject == "" and old.files == []
    job_id = log.start_job(content(), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0302", True, "", 0)
    assert len(log.entries()) == 2


def test_get_by_id_and_missing_id(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0301", True, "", 0)
    e = log.entries()[0]
    assert log.get(e.id).destination == "0301"
    with pytest.raises(KeyError):
        log.get(9999)


def test_tabs_and_newlines_cannot_break_the_text_log(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(subject="a\tb\nc"), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0301", False, "line1\nline2", 0)
    (line,) = (tmp_path / "send.log").read_text(encoding="utf-8").splitlines()
    assert "a b c" in line and "line1 line2" in line


def test_entries_serialize_to_json(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0301", True, "", 0)
    data = json.loads(json.dumps(log.entries()[0].to_dict()))
    assert data["files"][0]["name"] == "brief.pdf" and data["destination"] == "0301"


def test_content_pdf_is_lossless(tmp_path):
    # regression: grayscale pages were stored as JPEG (DCTDecode) and came back with artefacts
    from fritzfax.core import verify

    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    p = pages(2)
    job_id = log.start_job(content(), p, sff.encode_pages(p))
    log.record(job_id, "0301", True, "", 0)
    pdf = log.entries()[0].content_pdf
    assert b"DCTDecode" not in open(pdf, "rb").read()
    same, diffs = verify.compare_pages(p, verify.render_archive(Path(pdf)))
    assert same, diffs


def test_file_names_with_tabs_and_newlines_cannot_break_the_text_log(tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    evil = [history.FileInfo("a\tevil\nname.pdf", "/tmp/x", "ab" * 32, None)]
    job_id = log.start_job(content(files=evil), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "03\t01", True, "", 0)
    (line,) = (tmp_path / "send.log").read_text(encoding="utf-8").splitlines()
    assert "a evil name.pdf" in line and len(line.split("\t")) == 12


def test_database_connections_are_closed(tmp_path, monkeypatch):
    opened = []
    real = sqlite3.connect

    def tracking(*a, **k):
        con = real(*a, **k)
        opened.append(con)
        return con

    monkeypatch.setattr(history.sqlite3, "connect", tracking)
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job_id = log.start_job(content(), pages(1), sff.encode_pages(pages(1)))
    log.record(job_id, "0301", True, "", 0)
    log.entries()
    log.get(1)
    assert opened
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError):  # closed
            con.execute("SELECT 1")
