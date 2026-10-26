"""CLI end to end against the fake box (in process and as a real subprocess)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fritzfax import cli
from fritzfax.core import config, sff
from tests_helpers import one_page_pdf

ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def env(fakebox, tmp_path, monkeypatch):
    monkeypatch.setenv("FRITZFAX_PASSWORD", fakebox.state.password)
    conf = tmp_path / "f.conf"
    config.save(config.Settings(box_url=fakebox.url, username=fakebox.state.username, sender_name="Test Sender",
                                poll_interval=0.01),
                conf)
    return fakebox, conf


def run(conf, *argv):
    return cli.main(["--config", str(conf), *argv])


# --- happy path -----------------------------------------------------------------

def test_info(env, capsys):
    box, conf = env
    assert run(conf, "info") == 0
    out = capsys.readouterr().out
    assert "12345678" in out and "TestKennung" in out


def test_send_one_page_pdf_is_one_page_fax(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "brief.pdf")
    assert run(conf, "send", "--to", "0531-49059113", "--yes", str(pdf)) == 0
    (run_,) = box.state.runs
    assert run_.dest == "053149059113"
    assert len(sff.decode(run_.sff)) == 1  # the PDF page is the fax, no cover letter
    assert "sent successfully" in capsys.readouterr().out
    assert config.load(conf).recent_numbers == ["053149059113"]


def test_send_to_two_destinations_with_text_and_cover(env, tmp_path):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf", pages=2)
    assert run(conf, "send", "--to", "030 1, 030 2", "--text", "Hallo", "--cover", "--yes", str(pdf)) == 0
    assert [r.dest for r in box.state.runs] == ["0301", "0302"]
    assert len(sff.decode(box.state.runs[0].sff)) == 4  # cover + text + 2 pdf pages


def test_dry_run_writes_preview_and_sends_nothing(env, tmp_path):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    out = tmp_path / "out"
    assert run(conf, "send", "--to", "0301", "--dry-run", "--out", str(out), str(pdf)) == 0
    assert (out / "fax.sff").read_bytes().startswith(b"Sfff")
    assert (out / "page-01.png").is_file()
    assert not box.state.runs


def test_preview_without_box(tmp_path):
    pdf = one_page_pdf(tmp_path / "a.pdf", pages=3)
    out = tmp_path / "pv"
    assert cli.main(["--config", str(tmp_path / "none.conf"), "preview", "--pages", "1,3", "--out", str(out),
                     str(pdf)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["fax.sff", "page-01.png", "page-02.png"]


def test_precheck_and_status(env, capsys):
    box, conf = env
    assert run(conf, "precheck", "--to", "87654321", "--to", "030 1") == 0
    out = capsys.readouterr().out
    assert "87654321: needs confirmation" in out and "0301: ok" in out
    assert run(conf, "status") == 0
    assert "FAXSEND_READY" in capsys.readouterr().out


def test_send_with_2fa_prints_instructions(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "send", "--to", "87654321", "--yes", str(pdf)) == 0
    out = capsys.readouterr().out
    assert "*14711" in out and "press any button" in out


def test_journal_and_boxconfig(env, capsys, tmp_path):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    run(conf, "send", "--to", "0301", "--yes", str(pdf))
    capsys.readouterr()
    assert run(conf, "journal") == 0
    line = capsys.readouterr().out.strip().splitlines()[0]
    fax_id = int(line.split()[0])
    assert run(conf, "journal", "--download", str(fax_id), "--out", str(tmp_path / "f.pdf")) == 0
    assert (tmp_path / "f.pdf").read_bytes().startswith(b"%PDF")
    assert run(conf, "boxconfig", "--header", "Neu") == 0
    assert box.state.config["FaxSenderShort"] == "Neu"


# --- sad path -------------------------------------------------------------------

def test_invalid_destination(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "send", "--to", "hallo", "--yes", str(pdf)) == cli.EXIT_USAGE
    assert "invalid number" in capsys.readouterr().err


def test_wrong_password(env, tmp_path, monkeypatch, capsys):
    box, conf = env
    monkeypatch.setenv("FRITZFAX_PASSWORD", "wrong")
    assert run(conf, "info") == cli.EXIT_CONNECT
    assert "login failed" in capsys.readouterr().err


def test_failed_fax_exit_code(env, tmp_path, capsys):
    box, conf = env
    box.state.scenario = "reason:3490"
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "send", "--to", "0301", "--yes", str(pdf)) == cli.EXIT_FAILED
    assert "0x3490" in capsys.readouterr().out


def test_missing_file(env, capsys):
    box, conf = env
    assert run(conf, "send", "--to", "0301", "--yes", "/nonexistent.pdf") == cli.EXIT_USAGE
    assert not box.state.runs


def test_answer_no_aborts(env, tmp_path, monkeypatch):
    box, conf = env
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert run(conf, "send", "--to", "0301", str(one_page_pdf(tmp_path / "a.pdf"))) == cli.EXIT_FAILED
    assert not box.state.runs


# --- edge: real subprocess, as a user would run it ----------------------------------------

def test_subprocess_end_to_end(env, tmp_path):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    proc = subprocess.run([sys.executable, "-m", "fritzfax.cli", "--config", str(conf), "send", "--to",
                           "0531 49059113", "--yes", str(pdf)], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, proc.stderr
    assert "053149059113: OK" in proc.stdout
    assert len(box.state.runs) == 1


def test_send_header_shows_box_number_with_area_code(env, tmp_path, monkeypatch):
    box, conf = env
    from fritzfax.core import job

    seen = {}
    real = job.prepare

    def spy(doc, **kw):
        seen.update(kw)
        return real(doc, **kw)

    monkeypatch.setattr(job, "prepare", spy)
    assert run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == 0
    assert seen["sender_number"] == "12345678"
    assert seen["display_number"] == "+49 30 12345678"


def test_journal_lists_newest_first(env, tmp_path, capsys):
    box, conf = env
    for i in (1, 3, 2):
        box.state.journal.append({"number": f"030{i}", "msn": "1", "sent": 1, "date": "x", "date_time": i,
                                  "pages": 1, "unread": 0, "name": ""})
    assert run(conf, "journal") == 0
    ids = [int(line.split()[0]) for line in capsys.readouterr().out.strip().splitlines()]
    assert ids == [3, 2, 1]


def test_send_is_logged_with_content_and_listed(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "brief.pdf")
    assert run(conf, "send", "--to", "0301", "--subject", "Antrag", "--text", "Hallo", "--yes", str(pdf)) == 0
    capsys.readouterr()
    assert run(conf, "log") == 0
    out = capsys.readouterr().out
    assert "0301" in out and "OK" in out and "brief.pdf" in out and "Antrag" in out
    entry_id = out.strip().splitlines()[0].split()[0]
    assert run(conf, "log", "--show", entry_id) == 0
    detail = capsys.readouterr().out
    assert "Hallo" in detail and "content:" in detail and ".pdf" in detail
    assert run(conf, "log", "--json") == 0
    import json
    assert json.loads(capsys.readouterr().out)[0]["subject"] == "Antrag"


def test_log_show_unknown_id(env, capsys):
    box, conf = env
    assert run(conf, "log", "--show", "424242") == cli.EXIT_USAGE


def test_send_reports_verification(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "brief.pdf")
    assert run(conf, "send", "--to", "0301", "--yes", str(pdf)) == 0
    out = capsys.readouterr().out
    assert "0301: OK, VERIFIED" in out and "report:" in out
    assert run(conf, "log") == 0
    listing = capsys.readouterr().out
    assert "verified" in listing
    entry_id = listing.strip().splitlines()[0].split()[0]
    assert run(conf, "log", "--show", entry_id) == 0
    detail = capsys.readouterr().out
    assert "verdict:     verified" in detail and "archive:" in detail and "bit-identical" in detail
    assert "report:" in detail


def test_send_unverified_is_flagged(env, tmp_path, capsys):
    box, conf = env
    box.state.archive_mode = "tampered"
    assert run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == 0
    out = capsys.readouterr().out
    assert "0301: OK, NOT VERIFIED" in out and "archive copy" in out


def test_no_box_address_configured(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "empty.conf"
    config.save(config.Settings(), conf)
    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda *a: pytest.fail("asked for a password without an address"))
    assert run(conf, "info") == cli.EXIT_CONNECT
    err = capsys.readouterr().err
    assert "no FRITZ!Box address configured" in err and "--url" in err


def test_url_option_still_works_without_configured_address(fakebox, tmp_path, monkeypatch, capsys):
    conf = tmp_path / "empty.conf"
    monkeypatch.setenv("FRITZFAX_PASSWORD", fakebox.state.password)
    assert run(conf, "--url", fakebox.url, "--user", fakebox.state.username, "info") == 0


# --- bad input gives a message and an exit code, never a traceback (review 2026-10-25) ----

def test_page_range_outside_the_pdf(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "preview", str(pdf), "--pages", "5", "--out", str(tmp_path / "p")) == 2
    assert "error:" in capsys.readouterr().err


def test_page_range_that_is_not_a_range(env, tmp_path, capsys):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "preview", str(pdf), "--pages", "x", "--out", str(tmp_path / "p")) == 2
    assert "error:" in capsys.readouterr().err


def test_missing_text_file(env, tmp_path, capsys):
    box, conf = env
    assert run(conf, "send", "--to", "0301", "--yes", "--text-file", str(tmp_path / "nope.txt")) == 2
    err = capsys.readouterr().err
    assert "error:" in err and "nope.txt" in err
    assert box.state.runs == []


def test_precheck_with_an_invalid_number(env, capsys):
    box, conf = env
    assert run(conf, "precheck", "--to", "abc") == 2
    assert "invalid number" in capsys.readouterr().err


def test_confirmation_prompt_at_end_of_input_aborts(env, tmp_path, capsys, monkeypatch):
    box, conf = env
    pdf = one_page_pdf(tmp_path / "a.pdf")

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert run(conf, "send", "--to", "0301", str(pdf)) == 1
    assert "aborted" in capsys.readouterr().out
    assert box.state.runs == []


def test_journal_download_to_an_unwritable_place(env, tmp_path, capsys):
    box, conf = env
    run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf")))
    capsys.readouterr()
    assert run(conf, "journal") == 0
    first = capsys.readouterr().out.split()[0]
    assert run(conf, "journal", "--download", first, "--out", str(tmp_path / "no" / "such" / "dir" / "x.pdf")) == 2
    assert "error:" in capsys.readouterr().err


def test_ctrl_c_gives_a_short_message(env, capsys, monkeypatch):
    box, conf = env

    def interrupted(args, s):
        raise KeyboardInterrupt

    monkeypatch.setitem(cli.COMMANDS, "status", interrupted)
    assert run(conf, "status") == 130
    assert "interrupted" in capsys.readouterr().err


def test_url_given_for_one_run_is_not_stored(env, tmp_path):
    box, conf = env
    config.save(config.Settings(box_url="https://my-box.example", username="alice", poll_interval=0.01), conf)
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "--url", box.url, "--user", box.state.username, "send", "--to", "0301", "--yes", str(pdf)) == 0
    stored = config.load(conf)
    assert (stored.box_url, stored.username) == ("https://my-box.example", "alice")
    assert stored.recent_numbers == ["0301"]  # what send is meant to remember


def test_trust_over_plain_http_stores_nothing(env, capsys):
    box, conf = env  # the fake box speaks http
    assert run(conf, "trust", "--yes") == 2
    assert "no certificate" in capsys.readouterr().err
    assert config.load(conf).cert_sha256 == ""


def test_verification_does_not_depend_on_the_minute_in_the_header(env, tmp_path, capsys, monkeypatch):
    # reproduced 2026-10-25: with the header line of 25.10.2026 22:12 the coded page contains the
    # bytes FE 10; the fake box counted them as a second page and the send was "NOT VERIFIED"
    import time as _time

    from fritzfax.core import job

    box, conf = env
    at = _time.mktime(_time.strptime("25.10.2026 22:12:30", "%d.%m.%Y %H:%M:%S"))
    original = job.default_header
    monkeypatch.setattr(job, "default_header",
                        lambda name, number, when=None, display_number=None: original(name, number, at, display_number))
    pdf = one_page_pdf(tmp_path / "brief.pdf")
    assert run(conf, "send", "--to", "0301", "--yes", str(pdf)) == 0
    assert "0301: OK, VERIFIED" in capsys.readouterr().out
    (run_,) = box.state.runs
    if run_.sff.count(b"\xfe\x10") != 2:  # the coded header depends on the font of this system
        pytest.skip("this system's font puts no FE 10 into the header of 22:12 (tests/unit/test_sff.py covers it)")


# --- review round 2 ---------------------------------------------------------------------------------

def test_verification_failed_is_not_hidden_behind_ok(env, tmp_path, capsys, monkeypatch):
    # the box status says success, but its journal reports an error: the user must see the conflict
    from fritzfax.core import verify

    box, conf = env
    original = verify.collect

    def journal_error(*a, **k):
        ev = original(*a, **k)
        ev.journal_error = "Beim Faxversand ist ein Fehler aufgetreten."
        ev.problems.append("journal reports an error: " + ev.journal_error)
        ev.verdict = "failed"
        return ev

    monkeypatch.setattr(verify, "collect", journal_error)
    assert run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == 1
    assert "0301: OK, VERIFICATION FAILED" in capsys.readouterr().out


def test_pages_option_works_for_multi_page_images(env, tmp_path):
    from PIL import Image

    box, conf = env
    tif = tmp_path / "scan.tif"
    frames = [Image.new("L", (300, 400), 255) for _ in range(3)]
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    out = tmp_path / "p"
    assert run(conf, "preview", str(tif), "--pages", "2", "--out", str(out)) == 0
    assert len(list(out.glob("page-*.png"))) == 1


def test_pages_option_out_of_range_for_an_image(env, tmp_path, capsys):
    from PIL import Image

    box, conf = env
    tif = tmp_path / "scan.tif"
    Image.new("L", (300, 400), 255).save(tif)
    assert run(conf, "preview", str(tif), "--pages", "7", "--out", str(tmp_path / "p")) == 2
    assert "error:" in capsys.readouterr().err


def test_pages_option_for_an_office_file_is_refused(env, tmp_path, capsys):
    box, conf = env
    doc = tmp_path / "brief.docx"
    doc.write_bytes(b"x")
    assert run(conf, "preview", str(doc), "--pages", "1", "--out", str(tmp_path / "p")) == 2
    assert "--pages" in capsys.readouterr().err


def test_international_destination_gets_the_confirmation_note(env, tmp_path, capsys):
    box, conf = env
    assert run(conf, "send", "--to", "+43 1 234567", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) in (0, 1)
    assert "note: 00431234567 is an international number; the box will ask for a confirmation" in \
        capsys.readouterr().out


# --- the box's own result after the time budget (live 2026-10-26) -----------------------------------------------

def _tight_budget(box, conf, monkeypatch, scenario):
    from fritzfax.core import job

    monkeypatch.setattr(job, "SECONDS_PER_PAGE", 0)
    s = config.load(conf)
    s.stall_timeout = 0.05
    config.save(s, conf)
    box.state.scenario = scenario


def test_send_reports_a_long_call_and_the_box_result(env, tmp_path, monkeypatch, capsys):
    box, conf = env
    _tight_budget(box, conf, monkeypatch, "slow:30:3301")
    assert run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "[0301] no result yet, the box is still in the call" in out
    assert "0301: FAILED - the box reports transmission error 0x3301" in out
    assert "no progress" not in out


def test_send_without_a_box_result_says_unknown_not_failed(env, tmp_path, monkeypatch, capsys):
    from fritzfax.core import faxapi

    box, conf = env
    monkeypatch.setattr(faxapi, "OVERDUE_GRACE", 0.2)
    _tight_budget(box, conf, monkeypatch, "stall")
    assert run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "0301: RESULT UNKNOWN - no result from the box after" in out
    assert "0301: FAILED" not in out


# --- box fax numbers with country and area code (operator 2026-10-26) ------------------------------------------

def test_info_shows_the_box_numbers_with_country_and_area_code(env, capsys):
    box, conf = env
    assert run(conf, "info") == 0
    out = capsys.readouterr().out
    assert "fax numbers:    +49 30 12345678 (as --from: 12345678)" in out


def test_info_without_box_location_shows_the_plain_number(env, capsys, monkeypatch):
    from fritzfax.core import boxconfig
    from fritzfax.core.errors import ProtocolError

    def fail(client):
        raise ProtocolError("query.lua not allowed")

    monkeypatch.setattr(boxconfig, "read_settings", fail)
    box, conf = env
    assert run(conf, "info") == 0
    assert "fax numbers:    12345678\n" in capsys.readouterr().out


# --- set-password without a keyring (operator 2026-10-26) ------------------------------------------------

def test_set_password_without_keyring_saves_it_in_the_settings_file(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: None)
    conf = tmp_path / "f.conf"
    monkeypatch.setattr(sys, "stdin", io.StringIO("secret-pw\n"))
    assert run(conf, "--url", "https://fritz.box", "--user", "u", "set-password") == cli.EXIT_OK
    assert str(conf) in capsys.readouterr().out
    s = config.load(conf)
    assert "secret-pw" not in conf.read_text() and config.get_password(s) == "secret-pw"


def test_set_password_refuses_an_empty_password(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(config, "_keyring", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    with pytest.raises(SystemExit):
        run(tmp_path / "f.conf", "--url", "https://fritz.box", "set-password")
    assert not (tmp_path / "f.conf").exists()


# --- the fax number printed on the fax ---------------------------------------------------------------

def _capture_prepare(monkeypatch):
    seen = {}
    real = cli.jobmod.prepare

    def prepare(doc, **kw):
        seen["display"] = kw.get("display_number")
        seen["cover"] = doc.cover.from_number if doc.cover else None
        return real(doc, **kw)

    monkeypatch.setattr(cli.jobmod, "prepare", prepare)
    return seen


def test_send_prints_the_chosen_fax_number(env, tmp_path, monkeypatch):
    box, conf = env
    seen = _capture_prepare(monkeypatch)
    pdf = one_page_pdf(tmp_path / "a.pdf")
    assert run(conf, "send", "--to", "0301", "--yes", "--cover", "--shown-number", "+49 40 999999", str(pdf)) == 0
    assert seen == {"display": "+49 40 999999", "cover": "+49 40 999999"}
    assert box.state.runs  # sent from the box's own number all the same


def test_send_takes_the_fax_number_from_the_settings(env, tmp_path, monkeypatch):
    box, conf = env
    s = config.load(conf)
    s.shown_number = "+49 40 777777"
    config.save(s, conf)
    seen = _capture_prepare(monkeypatch)
    assert run(conf, "send", "--to", "0301", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == 0
    assert seen["display"] == "+49 40 777777"


def test_send_refuses_a_fax_number_that_is_no_number(env, tmp_path):
    box, conf = env
    assert run(conf, "send", "--to", "0301", "--yes", "--shown-number", "call me",
               str(one_page_pdf(tmp_path / "a.pdf"))) == cli.EXIT_USAGE
    assert not box.state.runs


# --- address book and the send log per recipient (operator 2026-10-26) ----------------------------------

def test_contacts_add_list_search_delete(tmp_path, capsys):
    conf = tmp_path / "f.conf"
    assert run(conf, "contacts", "--add", "Muster GmbH", "0531 49059113", "--note", "Buchhaltung") == 0
    assert run(conf, "contacts", "--add", "Amt", "+49 30 1234567") == 0
    capsys.readouterr()
    assert run(conf, "contacts") == 0
    out = capsys.readouterr().out
    assert out.index("Amt") < out.index("Muster GmbH") and "Buchhaltung" in out
    assert run(conf, "contacts", "muster") == 0
    out = capsys.readouterr().out
    assert "Muster GmbH" in out and "Amt" not in out
    from fritzfax.core.addressbook import AddressBook

    (amt,) = AddressBook().contacts("Amt")
    assert run(conf, "contacts", "--delete", str(amt.id)) == 0
    assert [c.name for c in AddressBook().contacts()] == ["Muster GmbH"]


def test_contacts_refuse_bad_entries(tmp_path, capsys):
    conf = tmp_path / "f.conf"
    assert run(conf, "contacts", "--add", "Amt", "kein Fax") == cli.EXIT_USAGE
    assert run(conf, "contacts", "--add", " ", "030 1") == cli.EXIT_USAGE
    assert run(conf, "contacts", "--add", "Amt", "030 1") == 0
    assert run(conf, "contacts", "--add", "amt", "030 1") == cli.EXIT_USAGE  # already there
    assert run(conf, "contacts", "--delete", "999") == cli.EXIT_USAGE
    assert "error" in capsys.readouterr().err


def test_send_to_a_name_from_the_address_book_and_log_per_recipient(env, tmp_path, capsys):
    box, conf = env
    pdf = str(one_page_pdf(tmp_path / "a.pdf"))
    assert run(conf, "contacts", "--add", "Muster GmbH", "0301") == 0
    assert run(conf, "send", "--to", "muster gmbh", "--yes", pdf) == 0
    assert run(conf, "send", "--to", "0402", "--yes", pdf) == 0
    capsys.readouterr()
    assert run(conf, "log", "--to", "Muster") == 0
    out = capsys.readouterr().out
    assert "Muster GmbH" in out and "0402" not in out and out.count("\n") == 1
    assert run(conf, "log", "--to", "040 2") == 0
    out = capsys.readouterr().out
    assert "0402" in out and "Muster" not in out
    assert run(conf, "log", "--to", "Unbekannt") == 0
    assert "no faxes sent to Unbekannt" in capsys.readouterr().out


def test_send_to_an_unknown_name_is_refused(env, tmp_path, capsys):
    box, conf = env
    assert run(conf, "send", "--to", "Unbekannt", "--yes", str(one_page_pdf(tmp_path / "a.pdf"))) == cli.EXIT_USAGE
    assert not box.state.runs
