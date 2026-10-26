"""Tests against a real FRITZ!Box. Opt-in only:

    FRITZFAX_LIVE=1 FRITZFAX_URL=https://<box address> FRITZFAX_USER=... FRITZFAX_PASSWORD=... \
        tools/dev.sh live
    FRITZFAX_LIVE_SEND=<number>   additionally sends ONE real one-page test fax to <number>

Everything except the live_send test is read-only or restores what it changed.
"""
import os
import time
from pathlib import Path

import pytest

from fritzfax.core import boxconfig, faxapi, job, journal, render, sff
from fritzfax.core.session import BoxClient
from tests_helpers import one_page_pdf

pytestmark = pytest.mark.skipif(os.environ.get("FRITZFAX_LIVE") != "1", reason="live tests are opt-in")


@pytest.fixture(scope="module")
def client():
    c = BoxClient(os.environ["FRITZFAX_URL"], os.environ.get("FRITZFAX_USER", ""),
                  os.environ["FRITZFAX_PASSWORD"], cert_sha256=os.environ.get("FRITZFAX_CERT") or None,
                  trust_unknown_cert=not os.environ.get("FRITZFAX_CERT"))
    c.login()
    print(f"\nbox certificate: {c.peer_fingerprint}")
    yield c
    c.logout()


@pytest.fixture(scope="module")
def info(client):
    return faxapi.FaxApi(client).read_info()


def own_number_with_area_code(client, info):
    s = boxconfig.read_settings(client)
    return "0" + s.area_code + info.src_numbers[0]


def test_fax_is_configured(info):
    assert info.configured and info.src_numbers


def test_status_is_readable(client):
    st = faxapi.FaxApi(client).status()
    assert st.status in faxapi.STATUS_TEXT


def test_precheck_matches_measurement(client, info):
    api = faxapi.FaxApi(client)
    assert api.precheck(own_number_with_area_code(client, info), "fritzfax live test") is False
    assert api.precheck(info.src_numbers[0], "fritzfax live test") is True  # no area code
    assert api.precheck("0044201234567", "fritzfax live test") is True     # international


def test_boxconfig_round_trip_restores_everything(client):
    original = boxconfig.read_settings(client)
    boxconfig.update_settings(client)  # no-op write
    assert boxconfig.read_settings(client) == original
    try:
        boxconfig.update_settings(client, header="fritzfax-test")
        assert boxconfig.read_settings(client).header == "fritzfax-test"
    finally:
        boxconfig.update_settings(client, header=original.header)
    assert boxconfig.read_settings(client) == original


def test_journal_lists(client):
    for e in journal.list_faxes(client):
        assert e.id > 0 and e.path


@pytest.mark.live_send
@pytest.mark.skipif(not os.environ.get("FRITZFAX_LIVE_SEND"), reason="sending needs FRITZFAX_LIVE_SEND=<number>")
def test_send_one_page_pdf_for_real(client, info, tmp_path):
    dest = os.environ["FRITZFAX_LIVE_SEND"]
    pdf = one_page_pdf(tmp_path / "fritzfax-live.pdf", text="fritzfax live test")
    prepared = job.prepare(render.Document(files=[pdf]), sender_name="fritzfax live test",
                           sender_number=info.src_numbers[0])
    assert prepared.page_count == 1
    out = Path(os.environ.get("FRITZFAX_LIVE_OUT", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    (out / "sent.sff").write_bytes(prepared.sff)

    class Log(job.Events):
        def on_phase(self, d, phase, detail=""):
            print(f"{time.strftime('%H:%M:%S')} [{d}] {phase} {detail}", flush=True)

        def on_status(self, d, st):
            print(f"{time.strftime('%H:%M:%S')} [{d}]   {st}", flush=True)

    before = {e.id for e in journal.list_faxes(client)}
    (result,) = job.send_sff(client, prepared.sff, [dest], "fritzfax live test", info.src_numbers[0], Log())
    print(f"result: {result}")
    assert result.ok, result.error
    ev = result.evidence
    print(f"evidence: {ev}")
    assert ev is not None and ev.verdict == "verified", ev.problems
    assert ev.archive_matches and ev.differing_pixels == [0] and ev.call_found
    from fritzfax.core import verify
    import hashlib

    verify.write_report(out / "report.pdf", ev, destination=dest, sender="fritzfax live test",
                        sent_pages=prepared.pages, sff_sha256=hashlib.sha256(prepared.sff).hexdigest(),
                        language="de")
    # the sent entry to this number: a fax to the box's own number also adds the received copy
    new = [e for e in journal.list_faxes(client) if e.id not in before and e.sent and e.number == dest]
    assert new, "the box journal has no entry for the fax"
    entry = max(new, key=lambda e: e.id)
    print(f"journal: {entry}")
    assert entry.sent and entry.pages == 1 and not entry.error
    pdf_back = journal.download(client, entry)
    (out / "journal.pdf").write_bytes(pdf_back)
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    doc.load(str(out / "journal.pdf"))
    size = doc.pagePointSize(0)
    print(f"box recorded the page as {size.width():.0f} x {size.height():.0f} pt")
    assert doc.pageCount() == 1
    assert abs(size.height() - 842) < 15  # A4 height, unlike the 897 pt of the web interface pages
    assert sff.decode(prepared.sff)[0].size == (1728, 2287)
