from pathlib import Path

import pytest
from PIL import Image

from fritzfax.core import history, job, render, sff
from fritzfax.core.errors import FaxNotConfigured

sys_path = Path(__file__).parent.parent
SFF = sff.encode_pages([Image.new("1", (sff.LINE_WIDTH, 5), 255)])


class Recorder(job.Events):
    def __init__(self, cancel_after=None):
        self.phases = []
        self.challenges = []
        self.statuses = []
        self.cancel_after = cancel_after

    def on_phase(self, dest, phase, detail=""):
        self.phases.append((dest, phase))

    def on_status(self, dest, status):
        self.statuses.append((dest, status.status))

    def on_two_factor(self, dest, challenge):
        self.challenges.append((dest, challenge))

    def should_cancel(self):
        return self.cancel_after is not None and len(self.statuses) >= self.cancel_after


FAST = dict(poll_interval=0.01, start_timeout=5, stall_timeout=5, tfa_timeout=5)


# --- happy path -----------------------------------------------------------------

def test_send_to_several_destinations_one_after_another(fakebox, client_for):
    ev = Recorder()
    results = job.send_sff(client_for(fakebox), SFF, ["053149059113", "04087654321"], "Absender",
                           "12345678", ev, **FAST)
    assert [r.ok for r in results] == [True, True]
    assert [r.number for r in results] == ["053149059113", "04087654321"]
    assert [run.dest for run in fakebox.state.runs] == ["053149059113", "04087654321"]
    assert ("053149059113", "done") in ev.phases and ("04087654321", "done") in ev.phases
    assert not ev.challenges


def test_two_factor_only_for_destinations_that_need_it(fakebox, client_for):
    ev = Recorder()
    results = job.send_sff(client_for(fakebox), SFF, ["030111", "87654321"], "A", "12345678", ev, **FAST)
    assert all(r.ok for r in results)
    assert [d for d, _ in ev.challenges] == ["87654321"]


def test_sender_number_defaults_to_first_box_number(fakebox, client_for):
    results = job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "", Recorder(), **FAST)
    assert results[0].ok and fakebox.state.runs[0].src == "12345678"


def test_history_records_every_destination(fakebox, client_for, tmp_path):
    log = history.History(tmp_path / "h.db", tmp_path / "sent")
    fakebox.state.scenario = "reason:3490"
    content = history.Content(sender_name="A", subject="Antrag", mode="document")
    job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "12345678", Recorder(), history=log,
                 content=content, **FAST)
    (entry,) = log.entries()
    assert entry.destination == "030111" and entry.ok is False and entry.reason == 0x3490
    assert entry.subject == "Antrag" and entry.sender_number == "12345678"
    assert Path(entry.sff_path).read_bytes() == SFF
    assert Path(entry.content_pdf).read_bytes().startswith(b"%PDF")  # decoded from the SFF


def test_prepare_records_file_content(tmp_path):
    from tests_helpers import one_page_pdf

    pdf = one_page_pdf(tmp_path / "a.pdf", pages=3)
    doc = render.Document(files=[render.FileSpec(pdf, [0, 2])], text="Hallo", subject="S")
    content = job.describe(doc, sender_name="Ich", sender_number="1")
    assert content.mode == "letter" and content.text == "Hallo" and content.subject == "S"
    assert content.files[0].name == "a.pdf" and content.files[0].pages == [0, 2]
    assert len(content.files[0].sha256) == 64


def test_prepare_builds_header_from_sender(tmp_path):
    from tests_helpers import one_page_pdf

    pdf = one_page_pdf(tmp_path / "a.pdf")
    doc = render.Document(files=[pdf])
    prepared = job.prepare(doc, sender_name="Test Sender", sender_number="030 123", header_mode="overlay")
    assert prepared.page_count == 1
    assert prepared.sff.startswith(b"Sfff")
    assert len(prepared.pages) == 1


# --- sad path -------------------------------------------------------------------

def test_failure_on_one_destination_does_not_stop_the_others(fakebox, client_for):
    results = job.send_sff(client_for(fakebox), SFF, ["", "030111"], "A", "12345678", Recorder(), **FAST)
    assert [r.ok for r in results] == [False, True]
    assert "dest_num" in results[0].error


def test_unknown_sender_number_is_rejected(fakebox, client_for):
    results = job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "99999", Recorder(), **FAST)
    assert results[0].ok is False and "99999" in results[0].error
    assert not fakebox.state.runs


def test_fax_not_configured(fakebox, client_for):
    fakebox.state.fax_configured = False
    with pytest.raises(FaxNotConfigured):
        job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "12345678", Recorder(), **FAST)


def test_cancel_skips_remaining_destinations(fakebox_factory, client_for):
    box = fakebox_factory(scenario="stall")
    ev = Recorder(cancel_after=4)
    results = job.send_sff(client_for(box), SFF, ["030111", "030222"], "A", "12345678", ev, **FAST)
    assert results[0].ok is False and "cancel" in results[0].error
    assert results[1].ok is False and results[1].error == "skipped"
    assert len(box.state.runs) == 1


# --- edge cases -----------------------------------------------------------------

def test_box_asks_for_2fa_although_precheck_said_no(fakebox, client_for, monkeypatch):
    # the precheck is advisory; if the upload still demands confirmation we confirm and retry once
    from fakebox import Handler

    monkeypatch.setattr(Handler, "_needs_2fa", staticmethod(lambda d: d == "030999"))
    original = job.faxapi.FaxApi.precheck
    monkeypatch.setattr(job.faxapi.FaxApi, "precheck", lambda self, d, s: False if d == "030999" else original(self, d, s))
    ev = Recorder()
    (result,) = job.send_sff(client_for(fakebox), SFF, ["030999"], "A", "12345678", ev, **FAST)
    assert result.ok and [d for d, _ in ev.challenges] == ["030999"]


def test_stall_timeout_scales_with_page_count(fakebox, client_for, monkeypatch):
    # measured live: progress jumps to 100 % at once and then stays unchanged while the box
    # transmits (~30 s for one page), so a fixed timeout would abort long faxes
    seen = {}
    original = job.faxapi.FaxApi.wait

    def spy(self, **kw):
        seen.update(kw)
        return original(self, **kw)

    monkeypatch.setattr(job.faxapi.FaxApi, "wait", spy)
    ten_pages = sff.encode_pages([Image.new("1", (sff.LINE_WIDTH, 5), 255)] * 10)
    job.send_sff(client_for(fakebox), ten_pages, ["030111"], "A", "12345678", Recorder(),
                 poll_interval=0.01, stall_timeout=180)
    assert seen["stall_timeout"] >= 10 * job.SECONDS_PER_PAGE
    job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "12345678", Recorder(),
                 poll_interval=0.01, stall_timeout=180)
    assert seen["stall_timeout"] == 180


# --- the box's own result after the time budget (live 2026-10-26) -------------------------

def _tight_budget(monkeypatch):
    monkeypatch.setattr(job, "SECONDS_PER_PAGE", 0)
    return dict(poll_interval=0.01, start_timeout=5, stall_timeout=0.05, tfa_timeout=5)


def test_late_failure_keeps_the_box_reason_and_the_evidence(fakebox_factory, client_for, monkeypatch):
    box = fakebox_factory(scenario="slow:30:3301")
    ev = Recorder()
    (result,) = job.send_sff(client_for(box), SFF, ["030111"], "A", "12345678", ev, overdue_grace=30,
                             verify_wait=1, **_tight_budget(monkeypatch))
    assert result.ok is False and result.reason == 0x3301
    assert "no progress" not in result.error and "unknown" not in result.error
    assert ("030111", "overdue") in ev.phases
    assert result.evidence is not None and result.evidence.verdict == "failed"


def test_late_success_is_a_success(fakebox_factory, client_for, monkeypatch):
    box = fakebox_factory(scenario="slow:30:0")
    ev = Recorder()
    (result,) = job.send_sff(client_for(box), SFF, ["030111"], "A", "12345678", ev, overdue_grace=30,
                             verify_wait=1, **_tight_budget(monkeypatch))
    assert result.ok and result.evidence.verdict == "verified"
    phases = [p for _, p in ev.phases]
    assert phases.index("overdue") < phases.index("done")


def test_call_that_never_ends_is_an_unknown_result_not_a_failure(fakebox_factory, client_for, monkeypatch):
    from fritzfax.core.errors import FaxResultUnknown

    box = fakebox_factory(scenario="stall")
    ev = Recorder()
    (result,) = job.send_sff(client_for(box), SFF, ["030111"], "A", "12345678", ev, overdue_grace=0.2,
                             **_tight_budget(monkeypatch))
    assert result.ok is False and isinstance(result.exception, FaxResultUnknown)
    assert "result unknown" in result.error
    assert result.evidence is None


# --- cancel before anything is dialed (review 2026-10-25) ------------------------------

class CancelOnPhase(Recorder):
    def __init__(self, dest, phase):
        super().__init__()
        self.trigger = (dest, phase)

    def should_cancel(self):
        return self.trigger in self.phases


def test_cancel_while_checking_uploads_nothing(fakebox, client_for):
    ev = CancelOnPhase("030111", "precheck")
    results = job.send_sff(client_for(fakebox), SFF, ["030111", "030222"], "A", "12345678", ev, **FAST)
    assert fakebox.state.runs == []  # the box dialed nothing
    assert [(r.ok, r.error) for r in results] == [(False, "skipped"), (False, "skipped")]
    assert ("030111", "upload") not in ev.phases


def test_cancel_while_verifying_skips_the_next_destination(fakebox, client_for):
    ev = CancelOnPhase("030111", "verify")
    results = job.send_sff(client_for(fakebox), SFF, ["030111", "030222"], "A", "12345678", ev,
                           verify_wait=0.1, **FAST)
    assert results[0].ok  # already sent: stays a success
    assert results[1].error == "skipped"
    assert [r.dest for r in fakebox.state.runs] == ["030111"]


def test_without_history_no_fax_copy_is_left_in_the_temp_directory(fakebox, client_for, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    (r,) = job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "12345678", Recorder(), verify_wait=0.1, **FAST)
    assert r.ok and r.evidence is not None
    assert list(tmp_path.glob("fritzfax-evidence-*")) == []


def test_without_verification_no_temp_directory_is_created(fakebox, client_for, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    (r,) = job.send_sff(client_for(fakebox), SFF, ["030111"], "A", "12345678", Recorder(), verify_send=False,
                        **FAST)
    assert r.ok and list(tmp_path.iterdir()) == []


def test_stall_timeout_counts_pages_by_parsing(fakebox, client_for, monkeypatch):
    # bytes FE 10 inside the coded rows are no page header: one page, so 1 x 90 s
    data = SFF[:-2] + b"\x02\xfe\x10" + b"\xfe\x00"
    assert data.count(b"\xfe\x10") == 2
    seen = {}
    original = job.faxapi.FaxApi.wait

    def spy(self, **kw):
        seen.update(kw)
        return original(self, **kw)

    monkeypatch.setattr(job.faxapi.FaxApi, "wait", spy)
    job.send_sff(client_for(fakebox), data, ["030111"], "A", "12345678", Recorder(), verify_send=False,
                 poll_interval=0.01, start_timeout=5, stall_timeout=5, tfa_timeout=5)
    assert seen["stall_timeout"] == job.SECONDS_PER_PAGE


def test_interrupted_send_is_logged_as_unknown_not_as_plain_failure(fakebox_factory, client_for, tmp_path):
    box = fakebox_factory(scenario="stall")
    log = history.History(tmp_path / "h.db", tmp_path / "sent")

    class Interrupt(Recorder):
        def on_status(self, dest, status):
            super().on_status(dest, status)
            if status.status == 4:
                raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        job.send_sff(client_for(box), SFF, ["030111", "030222"], "A", "12345678", Interrupt(), history=log,
                     verify_send=False, **FAST)
    (e,) = log.entries()
    assert e.destination == "030111" and not e.ok
    assert "interrupted" in e.error and "may still" in e.error
