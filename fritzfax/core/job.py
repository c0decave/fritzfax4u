"""Prepare a fax (render -> SFF) and send it to one or more destinations, one after another."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from PIL import Image

import hashlib
import shutil
import tempfile
from pathlib import Path

from . import faxapi, render, sff, twofactor, verify
from .errors import BoxError, FaxFailed, FaxNotConfigured, TwoFactorRequired
from .history import Content, FileInfo, History
from .session import BoxClient


# Measured on FRITZ!OS 8.25: during transmission the status stays at CONNECTED / 100 %
# without change (about 30 s for one page), so the "no progress" watchdog gets a per-page budget.
SECONDS_PER_PAGE = 90


@dataclass
class Prepared:
    pages: list[Image.Image]
    sff: bytes
    origins: list[render.PageOrigin] = field(default_factory=list)  # per page, see render.PageOrigin

    @property
    def page_count(self) -> int:
        return len(self.pages)


def default_header(sender_name: str, sender_number: str, when: float | None = None,
                   display_number: str | None = None) -> render.Header:
    stamp = time.strftime("%d.%m.%Y %H:%M", time.localtime(when))
    number = display_number or sender_number
    return render.Header(left=f"{stamp}   {sender_name}".strip(), right=f"Fax {number}" if number else "")


def prepare(doc: render.Document, *, sender_name: str = "", sender_number: str = "",
            header_mode: str | None = None, display_number: str | None = None) -> Prepared:
    """Render the document; adds the standard header line unless header_mode is 'off'."""
    if header_mode is not None:
        doc.header_mode = header_mode
    if doc.header is None and doc.header_mode != "off":
        doc.header = default_header(sender_name, sender_number, display_number=display_number)
    pages, origins = render.render_document_with_origins(doc)
    return Prepared(pages, sff.encode_pages(pages), origins)


def describe(doc: render.Document, *, sender_name: str = "", sender_number: str = "") -> Content:
    """What the user put into the fax, for the send log."""
    files = []
    for f in doc.files:
        spec = f if isinstance(f, render.FileSpec) else render.FileSpec(f)
        files.append(FileInfo.of(spec.path, spec.pages))
    letter = bool(doc.text.strip() or doc.subject.strip())
    return Content(sender_name=sender_name, sender_number=sender_number, subject=doc.subject or
                   (doc.cover.subject if doc.cover else ""), text=doc.text, mode="letter" if letter else "document",
                   cover=doc.cover is not None, files=files)


class Events:
    """Callbacks from a running send. All optional; override what you need."""

    def on_phase(self, dest: str, phase: str, detail: str = "") -> None:
        pass

    def on_status(self, dest: str, status: faxapi.FaxStatus) -> None:
        pass

    def on_two_factor(self, dest: str, challenge: twofactor.Challenge) -> None:
        pass

    def should_cancel(self) -> bool:
        return False


@dataclass
class Result:
    number: str
    ok: bool
    error: str = ""
    reason: int = 0
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    evidence: verify.Evidence | None = None
    report_pdf: str = ""
    exception: BoxError | None = field(default=None, repr=False, compare=False)  # for a translated message


def send_sff(client: BoxClient, data: bytes, destinations: list[str], sender_name: str, sender_number: str,
             events: Events | None = None, *, history: History | None = None, content: Content | None = None,
             pages_images: list[Image.Image] | None = None, verify_send: bool = True,
             evidence_dir: Path | None = None, verify_wait: float = 15.0, report_language: str = "en",
             poll_interval: float = 1.0,
             start_timeout: float = 60.0, stall_timeout: float = 180.0, tfa_timeout: float = 120.0,
             overdue_grace: float | None = None) -> list[Result]:
    events = events or Events()
    api = faxapi.FaxApi(client)
    info = api.read_info()
    faxapi.require_configured(info)
    src = sender_number or info.src_numbers[0]
    try:
        pages = sff.page_count(data)
    except sff.SffError:
        pages = 1  # FaxApi.upload refuses the document with a clear message before anything is dialed
    stall_timeout = max(stall_timeout, pages * SECONDS_PER_PAGE)
    job_id = ""
    if verify_send or history:
        pages_images = pages_images if pages_images is not None else sff.decode(data)
    own_evidence_dir = None  # without a send log the box's archive copy is only needed while verifying
    if evidence_dir is None:
        if history:
            evidence_dir = history.sff_dir
        elif verify_send:
            evidence_dir = own_evidence_dir = Path(tempfile.mkdtemp(prefix="fritzfax-evidence-"))
    sff_sha256 = hashlib.sha256(data).hexdigest()
    if history:
        content = content or Content(sender_name=sender_name)
        content.sender_number = src
        job_id = history.start_job(content, pages_images, data)
    try:
        return _send_each(client, api, info, data, destinations, sender_name, src, events, history, job_id,
                          pages_images, verify_send, evidence_dir, verify_wait, report_language, poll_interval,
                          start_timeout, stall_timeout, tfa_timeout, overdue_grace, sff_sha256)
    finally:
        if own_evidence_dir is not None:
            shutil.rmtree(own_evidence_dir, ignore_errors=True)


def _send_each(client, api, info, data, destinations, sender_name, src, events, history, job_id, pages_images,
               verify_send, evidence_dir, verify_wait, report_language, poll_interval, start_timeout,
               stall_timeout, tfa_timeout, overdue_grace, sff_sha256) -> list[Result]:
    results: list[Result] = []
    cancelled = False
    for dest in destinations:
        result = Result(dest, False)
        results.append(result)
        # a cancel before the upload means nothing is dialed: this and every later destination is skipped
        cancelled = cancelled or events.should_cancel()
        if cancelled:
            result.error = "skipped"
            continue
        try:
            if src not in info.src_numbers:
                raise FaxNotConfigured(f"sender number {src} is not a fax number of the box "
                                       f"({', '.join(info.src_numbers)})")
            events.on_phase(dest, "precheck")
            api.ensure_idle()

            def confirm():
                events.on_phase(dest, "confirm")
                twofactor.confirm(client, lambda ch: events.on_two_factor(dest, ch), timeout=tfa_timeout,
                                  poll_interval=poll_interval, should_cancel=events.should_cancel)

            if api.precheck(dest, sender_name):
                confirm()
            before = verify.journal_ids(client) if verify_send else None
            if events.should_cancel():
                cancelled = True
                result.error = "skipped"
                continue
            events.on_phase(dest, "upload")
            try:
                api.upload(data, dest, src)
            except TwoFactorRequired:
                confirm()
                events.on_phase(dest, "upload")
                api.upload(data, dest, src)
            events.on_phase(dest, "sending")
            last: list[faxapi.FaxStatus] = []

            def on_status(st, dest=dest):
                last[:] = [st]
                events.on_status(dest, st)

            try:
                final = api.wait(on_status=on_status, poll_interval=poll_interval, start_timeout=start_timeout,
                                 stall_timeout=stall_timeout, overdue_grace=overdue_grace,
                                 on_overdue=lambda st, dest=dest: events.on_phase(dest, "overdue"),
                                 should_cancel=events.should_cancel)
            except faxapi.FaxFailed:
                if verify_send and last and last[0].status in (1, 5, -1, 0):
                    events.on_phase(dest, "verify")
                    result.evidence = verify.collect(client, dest, pages_images, last[0], before, evidence_dir,
                                                     wait=verify_wait, failed=True)
                raise
            result.ok, result.reason = True, final.reason
            if verify_send:
                events.on_phase(dest, "verify")
                result.evidence = verify.collect(client, dest, pages_images, final, before, evidence_dir,
                                                 wait=verify_wait)
            events.on_phase(dest, "done")
        except BoxError as exc:
            result.error = str(exc)
            result.exception = exc
            result.reason = getattr(exc, "reason", 0)
            events.on_phase(dest, "failed", str(exc))
            if events.should_cancel():
                cancelled = True
        except BaseException as exc:  # Ctrl+C or a bug: the log must not claim a plain failure
            if isinstance(exc, KeyboardInterrupt):
                result.error = "interrupted by the user; the box may still be sending (result unknown)"
            else:
                result.error = f"stopped by an unexpected error ({exc.__class__.__name__}: {exc}); " \
                               "the box may still be sending (result unknown)"
            raise
        finally:
            result.finished = time.time()
            if history and result.evidence is not None:
                result.report_pdf = str(verify.write_report(
                    history.sff_dir / f"{job_id}-{dest}-report.pdf", result.evidence, destination=dest,
                    sender=f"{sender_name} ({src})".strip(), sent_pages=pages_images, sff_sha256=sff_sha256,
                    language=report_language))
            if history and result.error != "skipped":
                history.record(job_id, dest, result.ok, result.error, result.reason, started=result.started,
                               finished=result.finished,
                               verification=result.evidence.to_dict() if result.evidence else None,
                               report_pdf=result.report_pdf)
    return results


__all__ = ["Content", "Events", "Prepared", "Result", "default_header", "describe", "prepare", "send_sff",
           "FaxFailed"]
