"""Command line interface: fritzfax info|send|preview|status|precheck|journal|boxconfig|trust."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from . import __version__
from .core import boxconfig, config, faxapi, journal, render, twofactor
from .core import job as jobmod
from .core.errors import BoxError, BoxNotConfigured, FaxResultUnknown, UntrustedCertificate
from .core.history import History
from .core.numbers import (
    InvalidNumber,
    box_number_display,
    international_display,
    is_international,
    looks_local_without_area_code,
    parse_destinations,
)
from .core.render.office import OfficeUnavailable
from .core.session import BoxClient

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_CONNECT = 0, 1, 2, 3


def _settings(args) -> config.Settings:
    s = config.load(args.config)
    if args.url:
        s.box_url = args.url
    if args.user is not None:
        s.username = args.user
    return s


def _client(args, s: config.Settings) -> BoxClient:
    if not s.box_url.strip():
        raise BoxNotConfigured()
    password = config.get_password(s)
    if password is None:
        if not sys.stdin.isatty():
            raise SystemExit("no password: set FRITZFAX_PASSWORD or store it in the keyring (fritzfax set-password)")
        password = getpass.getpass(f"Password for {s.username or 'the box'} at {s.box_url}: ")
    return BoxClient(s.box_url, s.username, password, cert_sha256=s.cert_sha256 or None,
                     trust_unknown_cert=getattr(args, "trust", False), timeout=s.timeout)


def _document(args, s: config.Settings) -> render.Document:
    files = []
    for f in args.files:
        path = Path(f)
        pages = None
        ext = path.suffix.lower()
        if args.pages and path.is_file():
            # the number of pages must be known before choosing: PDF and images, not office files
            if ext == ".pdf":
                from .core.render.pdf import page_count as count
            elif ext in render.IMAGE_EXTENSIONS:
                from .core.render.image import frame_count as count
            else:
                raise render.RenderError(f"{path.name}: --pages works for PDF and image files only; "
                                         "save the document as PDF to choose pages")
            try:
                pages = render.parse_page_ranges(args.pages, count(path))
            except ValueError as exc:
                raise render.RenderError(f"{path.name}: {exc}") from exc
        files.append(render.FileSpec(path, pages))
    text = args.text or ""
    if args.text_file:
        try:
            text = Path(args.text_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise render.RenderError(f"cannot read the text file {args.text_file}: {exc}") from exc
    cover = None
    if args.cover if args.cover is not None else s.cover:
        cover = render.Cover(to_name=args.to_name or "", to_number=", ".join(args.to or []),
                             from_name=args.from_name or s.sender_name, from_number=args.from_number or s.sender_number,
                             subject=args.subject or "", message=args.cover_text or "",
                             date=time.strftime("%d.%m.%Y %H:%M"), labels=render.cover_labels(s.language))
    return render.Document(files=files, text=text, subject=args.subject if text else "", cover=cover,
                           header_mode=args.header_mode or s.header_mode, dither=args.dither or s.dither,
                           max_pages=s.max_pages, font_file=s.font_file, font_size_pt=s.font_size, soffice=s.soffice)


def _write_preview(prepared: jobmod.Prepared, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "fax.sff").write_bytes(prepared.sff)
    for i, page in enumerate(prepared.pages, 1):
        page.save(out / f"page-{i:02d}.png")
    print(f"{prepared.page_count} page(s) written to {out} (fax.sff, page-NN.png)")


class CliEvents(jobmod.Events):
    def __init__(self, totp: str | None, client: BoxClient):
        self.totp = totp
        self.client = client
        self._last = None

    def on_phase(self, dest, phase, detail=""):
        labels = {"precheck": "checking", "confirm": "waiting for confirmation", "upload": "uploading",
                  "sending": "sending", "verify": "collecting proof from the box", "done": "sent successfully",
                  "overdue": "no result yet, the box is still in the call; waiting for its result",
                  "failed": "FAILED"}
        print(f"[{dest}] {labels.get(phase, phase)}{': ' + detail if detail else ''}", flush=True)

    def on_status(self, dest, st):
        key = (st.status, st.progress)
        if key != self._last:
            self._last = key
            print(f"[{dest}]   {st.status_text} {st.progress}%", flush=True)

    def on_two_factor(self, dest, ch):
        print(f"[{dest}] The box wants a confirmation for this fax:")
        if "button" in ch.methods:
            print("         press any button on the FRITZ!Box")
        if ch.dial:
            print(f"         or dial {ch.dial} on a phone connected to the box")
        if self.totp:
            ok = twofactor.submit_totp(self.client, self.totp)
            print(f"         TOTP code {'accepted' if ok else 'rejected'}")


def _confirm(prompt: str) -> bool:
    """Ask yes/no on the terminal; the end of the input counts as no."""
    try:
        answer = input(prompt)
    except EOFError:
        print()
        return False
    return answer.strip().lower() in ("y", "j", "yes", "ja")


def cmd_info(args, s):
    client = _client(args, s)
    api = faxapi.FaxApi(client)
    info = api.read_info()
    st = api.status()
    print(f"box:            {client.base_url}")
    print(f"user:           {client.username}")
    print(f"fax configured: {'yes' if info.configured else 'no'}")
    location = boxconfig.read_location(client)
    shown = [f"{box_number_display(n, location)} (as --from: {n})" if location else n for n in info.src_numbers]
    print(f"fax numbers:    {', '.join(shown) or '-'}")
    print(f"station id:     {info.identifier}")
    print(f"header:         {info.send_short}")
    print(f"sender:         {info.sender_long}")
    print(f"status:         {st.status_text} ({st.progress}%, reason {st.reason})")
    client.logout()
    return EXIT_OK


def cmd_preview(args, s):
    prepared = jobmod.prepare(_document(args, s), sender_name=args.from_name or s.sender_name,
                              sender_number=args.from_number or s.sender_number)
    _write_preview(prepared, Path(args.out))
    return EXIT_OK


def cmd_send(args, s):
    try:
        destinations = parse_destinations(",".join(args.to), s.country_code)
    except InvalidNumber as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sender_name = args.from_name or s.sender_name
    if args.dry_run:
        prepared = jobmod.prepare(_document(args, s), sender_name=sender_name,
                                  sender_number=args.from_number or s.sender_number)
        _write_preview(prepared, Path(args.out or "fritzfax-preview"))
        print(f"dry run: would send {prepared.page_count} page(s) to {', '.join(destinations)}")
        return EXIT_OK
    # connect first: the header line shows the box's fax number with country and area code
    client = _client(args, s)
    info = faxapi.FaxApi(client).read_info()
    faxapi.require_configured(info)
    sender_number = args.from_number or s.sender_number or info.src_numbers[0]
    try:
        location = boxconfig.read_settings(client)
        display = international_display(sender_number, location.country_code, location.area_code)
    except BoxError:
        display = sender_number
    doc = _document(args, s)
    prepared = jobmod.prepare(doc, sender_name=sender_name, sender_number=sender_number, display_number=display)
    for d in destinations:
        if looks_local_without_area_code(d):
            print(f"note: {d} has no area code; the box will ask for a confirmation")
        elif is_international(d):
            print(f"note: {d} is an international number; the box will ask for a confirmation")
    if not args.yes:
        if not _confirm(f"Send {prepared.page_count} page(s) to {', '.join(destinations)}? [y/N] "):
            print("aborted")
            client.logout()
            return EXIT_FAILED
    results = jobmod.send_sff(client, prepared.sff, destinations, sender_name, sender_number,
                              CliEvents(args.totp, client), history=History() if s.keep_history else None,
                              content=jobmod.describe(doc, sender_name=sender_name, sender_number=sender_number),
                              pages_images=prepared.pages, report_language=_lang(s),
                              start_timeout=s.start_timeout, stall_timeout=s.stall_timeout,
                              poll_interval=s.poll_interval)
    client.logout()
    # store only the numbers: --url/--user are for this run, not a new default
    stored = config.load(args.config)
    for d in destinations:
        stored.remember_number(d)
    config.save(stored, args.config)
    # a send whose own verification failed (e.g. the box journal reports an error) is no success
    failed = [r for r in results if not r.ok or (r.evidence is not None and r.evidence.verdict == "failed")]
    for r in results:
        verdict = ""
        if r.evidence is not None:
            verdict = {"verified": ", VERIFIED", "unverified": ", NOT VERIFIED",
                       "failed": ", VERIFICATION FAILED" if r.ok else ""}[r.evidence.verdict]
        if r.ok:
            outcome = "OK"
        elif isinstance(r.exception, FaxResultUnknown):
            outcome = "RESULT UNKNOWN - " + r.error
        else:
            outcome = "FAILED - " + r.error
        print(f"{r.number}: {outcome}{verdict}")
        if r.evidence is not None and r.evidence.problems:
            for problem in r.evidence.problems:
                print(f"    - {problem}")
        if r.report_pdf:
            print(f"    report: {r.report_pdf}")
    return EXIT_FAILED if failed else EXIT_OK


def cmd_status(args, s):
    client = _client(args, s)
    st = faxapi.FaxApi(client).status()
    print(f"{st.status_text} status={st.status} progress={st.progress}% reason={st.reason}")
    client.logout()
    return EXIT_OK


def cmd_precheck(args, s):
    try:
        destinations = parse_destinations(",".join(args.to), s.country_code)
    except InvalidNumber as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    client = _client(args, s)
    api = faxapi.FaxApi(client)
    for d in destinations:
        print(f"{d}: {'needs confirmation (2FA)' if api.precheck(d, s.sender_name) else 'ok, no confirmation needed'}")
    client.logout()
    return EXIT_OK


def cmd_journal(args, s):
    client = _client(args, s)
    entries = sorted(journal.list_faxes(client), key=lambda e: e.id, reverse=True)
    if args.download is not None or args.delete is not None:
        wanted = args.download if args.download is not None else args.delete
        match = [e for e in entries if e.id == wanted]
        if not match:
            print(f"error: no fax with id {wanted}", file=sys.stderr)
            return EXIT_USAGE
        if args.download is not None:
            out = Path(args.out or f"fax-{wanted}.pdf")
            try:
                out.write_bytes(journal.download(client, match[0]))
            except OSError as exc:
                print(f"error: cannot write {out}: {exc.strerror or exc}", file=sys.stderr)
                return EXIT_USAGE
            print(f"saved {out}")
        else:
            journal.delete(client, match[0])
            print(f"deleted {wanted}")
    else:
        for e in entries:
            direction = "sent" if e.sent else "received"
            status = f" ERROR: {e.error}" if e.error else ""
            print(f"{e.id}  {e.date}  {direction:8}  {e.number:>16}  {e.pages} p{status}")
        if not entries:
            print("no faxes")
    client.logout()
    return EXIT_OK


def _lang(s) -> str:
    if s.language in ("de", "en"):
        return s.language
    return "de" if os.environ.get("LANG", "").startswith("de") else "en"


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def cmd_log(args, s):
    log = History()
    if args.show is not None:
        try:
            e = log.get(args.show)
        except KeyError:
            print(f"error: no log entry {args.show}", file=sys.stderr)
            return EXIT_USAGE
        print(f"id:          {e.id}")
        print(f"started:     {_fmt_time(e.started or e.timestamp)}")
        print(f"finished:    {_fmt_time(e.finished or e.timestamp)}")
        print(f"to:          {e.destination}")
        print(f"from:        {e.sender_name} ({e.sender_number})")
        print(f"result:      {'OK' if e.ok else 'FAILED'}{' - ' + e.error if e.error else ''}")
        print(f"pages:       {e.pages}   mode: {e.mode or '-'}   cover sheet: {'yes' if e.cover else 'no'}")
        print(f"subject:     {e.subject}")
        for f in e.files:
            sel = "" if f.pages is None else f"  pages {','.join(str(p + 1) for p in f.pages)}"
            print(f"file:        {f.name}{sel}  sha256 {f.sha256}  ({f.path})")
        if e.text:
            print("text:")
            for line in e.text.splitlines():
                print(f"    {line}")
        print(f"content:     {e.content_pdf or '-'}")
        print(f"sff:         {e.sff_path or '-'}")
        v = e.verification
        if v:
            print(f"verdict:     {v.get('verdict')}")
            print(f"box status:  {v.get('status_text')} (status {v.get('status')}, reason 0x{v.get('reason', 0):04X})")
            if v.get("journal_id"):
                print(f"journal:     #{v['journal_id']} {v.get('journal_date')} {v.get('journal_pages')} p "
                      f"{v.get('journal_error') or ''}".rstrip())
            match = {True: "bit-identical to the sent pages", False: "DIFFERS from the sent pages",
                     None: "not available"}[v.get("archive_matches")]
            print(f"archive:     {match} {v.get('archive_pdf', '')}".rstrip())
            print(f"call list:   {v.get('call_date') + ', ' + v.get('call_duration') if v.get('call_found') else '-'}")
            for problem in v.get("problems", []):
                print(f"  open:      {problem}")
        print(f"report:      {e.report_pdf or '-'}")
        return EXIT_OK
    entries = list(reversed(log.entries(args.limit)))
    if args.json:
        import json

        print(json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=1))
        return EXIT_OK
    for e in entries:
        what = e.subject or ", ".join(f.name for f in e.files) or "-"
        files = ", ".join(f.name for f in e.files)
        if e.subject and files:
            what = f"{e.subject} [{files}]"
        print(f"{e.id:>5}  {_fmt_time(e.timestamp)}  {'OK    ' if e.ok else 'FAILED'}  {e.verdict or '-':10}  "
              f"{e.destination:>16}  {e.pages} p  {what}")
    if not entries:
        print("no faxes sent yet")
    return EXIT_OK


def cmd_boxconfig(args, s):
    client = _client(args, s)
    changes = {k: v for k, v in dict(identifier=args.identifier, header=args.header, sender_name=args.sender_name,
                                     email_address=args.email, email_forward=args.email_forward,
                                     store_internal=args.store_internal).items() if v is not None}
    if changes:
        boxconfig.update_settings(client, **changes, on_two_factor=lambda ch: print(
            "confirm on the box: press a button" + (f" or dial {ch.dial}" if ch.dial else "")))
        print("saved")
    cur = boxconfig.read_settings(client)
    for key, value in vars(cur).items():
        print(f"{key:15} {value}")
    client.logout()
    return EXIT_OK


def cmd_trust(args, s):
    probe = BoxClient(s.box_url, s.username, "", trust_unknown_cert=True, timeout=s.timeout)
    probe.raw_request("GET", "/login_sid.lua", params={"version": "2"})
    if not probe.peer_fingerprint:
        print(f"error: {s.box_url} is reached without TLS (http): there is no certificate to trust",
              file=sys.stderr)
        return EXIT_USAGE
    print(f"certificate SHA-256: {probe.peer_fingerprint}")
    if args.yes or _confirm("Trust this certificate? [y/N] "):
        s.cert_sha256 = probe.peer_fingerprint or ""
        config.save(s, args.config)
        print("stored")
    return EXIT_OK


def cmd_selftest(args, s):
    from .selftest import run

    return EXIT_OK if run(gui=args.gui, soffice=s.soffice) else EXIT_FAILED


def cmd_set_password(args, s):
    pw = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass("Password: ")
    config.set_password(s, pw)
    print("password stored in the keyring")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fritzfax", description="Send faxes through an AVM FRITZ!Box.")
    p.add_argument("--version", action="version", version=f"fritzfax {__version__}")
    p.add_argument("--config", type=Path, default=None, help="settings file")
    p.add_argument("--url", help="address of your FRITZ!Box, as used in the browser")
    p.add_argument("--user", help="box user name")
    p.add_argument("--trust", action="store_true", help="accept an unknown TLS certificate for this run")
    sub = p.add_subparsers(dest="command", required=True)

    def doc_options(sp):
        sp.add_argument("files", nargs="*", help="PDF, image or office files; their pages are the fax")
        sp.add_argument("--pages", help="page selection for PDFs, e.g. 1-2,4")
        sp.add_argument("--text", help="cover letter text (letter mode)")
        sp.add_argument("--text-file", help="read the letter text from a file")
        sp.add_argument("--subject")
        sp.add_argument("--cover", action=argparse.BooleanOptionalAction, default=None, help="add a cover sheet")
        sp.add_argument("--cover-text", help="message on the cover sheet")
        sp.add_argument("--to-name", help="recipient name on the cover sheet")
        sp.add_argument("--from-name", help="sender name (header line, cover sheet)")
        sp.add_argument("--from-number", help="box fax number to send from")
        sp.add_argument("--header-mode", choices=render.HEADER_MODES)
        sp.add_argument("--dither", choices=render.DITHER_MODES)

    sub.add_parser("info", help="show fax setup of the box")
    sp = sub.add_parser("send", help="send a fax")
    sp.add_argument("--to", action="append", required=True, help="destination(s); repeat or separate by comma")
    doc_options(sp)
    sp.add_argument("--dry-run", action="store_true", help="render only, do not send")
    sp.add_argument("--out", help="directory for --dry-run output")
    sp.add_argument("--yes", "-y", action="store_true", help="do not ask before sending")
    sp.add_argument("--totp", help="authenticator code if the box asks for a confirmation")
    sp = sub.add_parser("preview", help="render pages and SFF without a box")
    doc_options(sp)
    sp.set_defaults(to=None)
    sp.add_argument("--out", default="fritzfax-preview")
    sub.add_parser("status", help="current fax status of the box")
    sp = sub.add_parser("precheck", help="ask the box whether a destination needs confirmation")
    sp.add_argument("--to", action="append", required=True)
    sp = sub.add_parser("journal", help="list, download or delete faxes in the box journal")
    sp.add_argument("--download", type=int, metavar="ID")
    sp.add_argument("--delete", type=int, metavar="ID")
    sp.add_argument("--out")
    sp = sub.add_parser("log", help="local send log: when, to whom, which content")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--show", type=int, metavar="ID", help="details of one entry")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("boxconfig", help="show or change the fax settings of the box")
    sp.add_argument("--identifier", help="station id (max 20 characters)")
    sp.add_argument("--header", help="header line text (1-40 characters)")
    sp.add_argument("--sender-name")
    sp.add_argument("--email", help="forward received faxes to this address")
    sp.add_argument("--email-forward", action=argparse.BooleanOptionalAction, default=None)
    sp.add_argument("--store-internal", action=argparse.BooleanOptionalAction, default=None)
    sp = sub.add_parser("trust", help="show and store the box certificate fingerprint")
    sp.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("set-password", help="store the box password in the system keyring")
    sp = sub.add_parser("selftest", help="check that fritzfax works on this system (no box needed)")
    sp.add_argument("--gui", action="store_true", help="also open the main window")
    return p


COMMANDS = {"info": cmd_info, "send": cmd_send, "preview": cmd_preview, "status": cmd_status,
            "precheck": cmd_precheck, "journal": cmd_journal, "log": cmd_log, "boxconfig": cmd_boxconfig, "trust": cmd_trust,
            "set-password": cmd_set_password, "selftest": cmd_selftest}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.command == "selftest" and args.gui):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    s = _settings(args)
    if s.load_problem:
        print(f"warning: {s.load_problem}", file=sys.stderr)
    try:
        return COMMANDS[args.command](args, s)
    except UntrustedCertificate as exc:
        print(f"error: {exc}\n       check it, then run: fritzfax trust  (or pass --trust once)", file=sys.stderr)
        return EXIT_CONNECT
    except OfficeUnavailable as exc:
        print(f"error: {exc}\n       office documents need LibreOffice: save the document as PDF, or install "
              "LibreOffice (the full fritzfax package brings it along)", file=sys.stderr)
        return EXIT_USAGE
    except render.RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (BoxError, config.NoKeyring) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONNECT
    except KeyboardInterrupt:
        print("\ninterrupted (a fax that was already handed to the box may still be sent: "
              "check with 'fritzfax status')", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
