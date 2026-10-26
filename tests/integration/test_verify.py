"""Proof that a fax was really sent and confirmed by the receiver."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fritzfax.core import history, job, render, sff, verify
from tests_helpers import one_page_pdf

FIXTURES = Path(__file__).parent.parent / "fixtures"
FAST = dict(poll_interval=0.01, start_timeout=5, stall_timeout=5, tfa_timeout=5)


@pytest.fixture
def prepared(tmp_path):
    return job.prepare(render.Document(files=[one_page_pdf(tmp_path / "a.pdf", pages=2)]), sender_name="Test",
                       sender_number="12345678")


def send(client, prepared, dest="053149059113", **kw):
    (result,) = job.send_sff(client, prepared.sff, [dest], "Test", "12345678", None,
                             pages_images=prepared.pages, verify_send=True, **FAST, **kw)
    return result


# --- unit: call list and page comparison ----------------------------------------------

def test_parse_call_list_live_format():
    calls = verify.parse_call_list((FIXTURES / "foncalls.csv").read_text(encoding="utf-8"))
    assert len(calls) == 4
    out = [c for c in calls if c.outgoing]
    assert [c.number for c in out] == ["04087654321", "053149059113"]
    assert out[0].date == "25.10.26 17:21" and out[0].duration == "0:01"


def test_parse_call_list_rejects_other_content():
    assert verify.parse_call_list("<html>login</html>") == []


def test_compare_pages():
    a = Image.new("1", (1728, 200), 1)
    b = a.copy()
    ImageDraw.Draw(b).rectangle([10, 10, 20, 20], fill=0)
    assert verify.compare_pages([a], [a.copy()]) == (True, [0])
    same, diffs = verify.compare_pages([a], [b])
    assert same is False and diffs == [121]
    assert verify.compare_pages([a], [a, a])[0] is False  # page count differs


# --- integration: verdicts ------------------------------------------------------------

def test_verified_send(fakebox, client_for, prepared, tmp_path):
    r = send(client_for(fakebox), prepared, evidence_dir=tmp_path)
    ev = r.evidence
    assert r.ok and ev.verdict == "verified", ev.problems
    assert ev.reason == 0 and ev.journal_pages == 2 and ev.journal_error == ""
    assert ev.archive_matches is True and ev.differing_pixels == [0, 0]
    assert ev.call_found and ev.call_date == ev.journal_date
    assert Path(ev.archive_pdf).read_bytes().startswith(b"%PDF")


def test_tampered_archive_is_not_verified(fakebox, client_for, prepared, tmp_path):
    fakebox.state.archive_mode = "tampered"
    ev = send(client_for(fakebox), prepared, evidence_dir=tmp_path).evidence
    assert ev.verdict == "unverified"
    assert ev.archive_matches is False and ev.differing_pixels[0] > 0
    assert any("archive" in p for p in ev.problems)


def test_missing_journal_entry_is_not_verified(fakebox, client_for, prepared, tmp_path):
    fakebox.state.journal_for_runs = False
    ev = send(client_for(fakebox), prepared, evidence_dir=tmp_path, verify_wait=0.05).evidence
    assert ev.verdict == "unverified" and any("journal" in p for p in ev.problems)


def test_call_list_not_accessible(fakebox, client_for, prepared, tmp_path):
    fakebox.state.call_list = False
    ev = send(client_for(fakebox), prepared, evidence_dir=tmp_path).evidence
    assert ev.verdict == "unverified" and any("call list" in p for p in ev.problems)
    assert ev.archive_matches is True  # the other evidence is still collected


def test_failed_send_has_failed_verdict(fakebox_factory, client_for, prepared, tmp_path):
    box = fakebox_factory(scenario="reason:3490")
    r = send(client_for(box), prepared, evidence_dir=tmp_path)
    assert not r.ok and r.evidence.verdict == "failed"
    assert r.evidence.reason == 0x3490 and r.evidence.journal_error


def test_evidence_and_report_go_into_the_send_log(fakebox, client_for, prepared, tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    job.send_sff(client_for(fakebox), prepared.sff, ["0301"], "Test", "12345678", None, history=log,
                 pages_images=prepared.pages, verify_send=True, **FAST)
    (e,) = log.entries()
    assert e.verification["verdict"] == "verified"
    assert Path(e.verification["archive_pdf"]).is_file()
    assert Path(e.report_pdf).read_bytes().startswith(b"%PDF")
    assert "VERIFIED" in (tmp_path / "send.log").read_text(encoding="utf-8")


def test_report_pdf_content(fakebox, client_for, prepared, tmp_path):
    from PySide6.QtPdf import QPdfDocument

    r = send(client_for(fakebox), prepared, evidence_dir=tmp_path)
    path = tmp_path / "report.pdf"
    verify.write_report(path, r.evidence, destination="053149059113", sender="Test (12345678)",
                        sent_pages=prepared.pages, sff_sha256="00" * 32)
    doc = QPdfDocument()
    doc.load(str(path))
    assert doc.pageCount() == 1


@pytest.mark.parametrize("language", ["de", "en"])
def test_report_labels_never_overlap_values(language):
    from fritzfax.core.render.fonts import font

    bold = font(28, bold=True)
    offset = verify.report_value_offset(language)
    widths = [bold.getlength(v) for k, v in verify.REPORT_LABELS[language].items()
              if k in ("to", "from", "pages", "result", "status", "journal", "archive", "call", "sha")]
    assert max(widths) + 20 <= offset


@pytest.mark.parametrize("language", ["de", "en"])
def test_report_text_stays_inside_the_page(language):
    ev = verify.Evidence(status=5, status_text="FAXSEND_REPORT", reason=0, journal_id=1, journal_date="x",
                         journal_pages=1, archive_matches=True, differing_pixels=[0], call_found=True,
                         call_date="x", call_duration="0:01", verdict="verified",
                         problems=["a very long problem description " * 6])
    img = verify.report_image(ev, destination="053149059113", sender="Test Sender (87654321) " * 3,
                              sent_pages=[Image.new("1", (1728, 2287), 1)], sff_sha256="ab" * 32,
                              language=language)
    margin = img.crop((img.width - 60, 0, img.width, img.height))
    assert margin.getextrema() == (255, 255)  # nothing drawn into the right margin
