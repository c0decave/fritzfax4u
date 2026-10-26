from pathlib import Path

import pytest

from fritzfax.core import boxconfig, journal
from fritzfax.core.errors import TwoFactorFailed, ValidationFailed

FIXTURES = Path(__file__).parent.parent / "fixtures"


# --- form serialization (unit level, live capture) ------------------------------------

def test_serialize_live_form_like_a_browser():
    form = boxconfig.parse_form((FIXTURES / "edit_fax_intern.html").read_text())
    data = form.serialize()
    assert data["faxsendoption"] == "SendAndReceive"
    assert data["num_1"] == "12345678"
    assert data["headline"] == "TestKennung"
    assert data["from_headline"] == "TestShort"
    assert data["from_name"] == "Test Sender"
    assert data["email_send"] == "on"
    assert data["mail_type"] == "default"
    assert data["fax_save"] == "intern"
    assert "fax_switch" not in data            # unchecked checkbox is not sent
    assert "usbOther" not in data.values()     # disabled radio is not sent
    assert "oldPageXhrSid" not in data         # inputs without name are not sent


# --- read ----------------------------------------------------------------------------

def test_read_settings(fakebox, client_for):
    s = boxconfig.read_settings(client_for(fakebox))
    assert s.identifier == "TestKennung" and s.header == "TestShort" and s.sender_name == "Test Sender"
    assert s.numbers == ["12345678"]
    assert s.email_forward is True and s.store_internal is True and s.send_only is False
    assert s.country_code == "49" and s.area_code == "30"


# --- write: happy ---------------------------------------------------------------------

def test_update_changes_only_requested_fields(fakebox, client_for):
    client = client_for(fakebox)
    before = dict(fakebox.state.config)
    boxconfig.update_settings(client, identifier="Neu Kennung", header="Neu")
    after = fakebox.state.config
    assert after["FaxKennung"] == "Neu Kennung" and after["FaxSenderShort"] == "Neu"
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"FaxKennung", "FaxSenderShort"}


def test_noop_update_keeps_everything(fakebox, client_for):
    before = dict(fakebox.state.config)
    boxconfig.update_settings(client_for(fakebox))
    assert fakebox.state.config == before


def test_email_forwarding_and_storage(fakebox, client_for):
    client = client_for(fakebox)
    boxconfig.update_settings(client, email_address="fax@example.org")
    assert fakebox.state.config["FaxMailAddress0"] == "fax@example.org"
    assert fakebox.state.config["FaxMailActive"] == "5"
    boxconfig.update_settings(client, email_forward=False)
    assert fakebox.state.config["FaxMailActive"] == "4"
    s = boxconfig.read_settings(client)
    assert s.email_forward is False and s.store_internal is True


def test_update_with_two_factor(fakebox, client_for):
    fakebox.state.config_save_needs_2fa = True
    seen = []
    boxconfig.update_settings(client_for(fakebox), header="X", on_two_factor=seen.append, tfa_poll_interval=0.01)
    assert seen and fakebox.state.config["FaxSenderShort"] == "X"


# --- write: sad -----------------------------------------------------------------------

def test_validation_error_from_box(fakebox, client_for):
    with pytest.raises(ValidationFailed) as err:
        boxconfig.update_settings(client_for(fakebox), identifier="x" * 21)
    assert err.value.field == "headline"


def test_two_factor_needed_but_no_handler(fakebox, client_for):
    fakebox.state.config_save_needs_2fa = True
    with pytest.raises(TwoFactorFailed):
        boxconfig.update_settings(client_for(fakebox), header="X")
    assert fakebox.state.config["FaxSenderShort"] == "TestShort"


def test_unknown_field_is_rejected(fakebox, client_for):
    with pytest.raises(TypeError):
        boxconfig.update_settings(client_for(fakebox), colour="red")


# --- edge: local length checks before talking to the box --------------------------------

@pytest.mark.parametrize("kw", [{"identifier": "x" * 21}, {"header": ""}, {"header": "x" * 41},
                                {"sender_name": "x" * 201}])
def test_local_limits_match_box_limits(kw):
    with pytest.raises(ValidationFailed):
        boxconfig.check_limits(**kw)


# --- journal ----------------------------------------------------------------------------

def test_journal_parse_live_capture():
    entries = journal.parse_journal((FIXTURES / "faxoverview.json").read_text())
    assert len(entries) == 3
    assert entries[0].sent is True and entries[0].pages == 1
    assert entries[0].error.startswith("Beim Faxversand")
    assert entries[1].error == ""
    assert entries[0].number == "03011111111" and entries[0].own_number == "12345678"


def test_journal_list_download_delete(fakebox, client_for, tmp_path):
    from fakebox import FaxRun

    fakebox.state.journal.append({"number": "030111", "msn": "12345678", "sent": 1, "date": "25.10.26 12:00",
                                  "date_time": 1792934890, "pages": 2, "unread": 1, "name": ""})
    client = client_for(fakebox)
    (entry,) = journal.list_faxes(client)
    assert entry.id == 1792934890 and entry.pages == 2 and entry.unread is True
    pdf = journal.download(client, entry)
    assert pdf.startswith(b"%PDF")
    journal.mark_seen(client, entry)
    assert journal.list_faxes(client)[0].unread is False
    journal.delete(client, entry)
    assert journal.list_faxes(client) == []


def test_journal_download_after_session_renewal(fakebox, client_for):
    fakebox.state.journal.append({"number": "030111", "msn": "1", "sent": 0, "date": "x", "date_time": 5,
                                  "pages": 1, "unread": 0, "name": ""})
    client = client_for(fakebox)
    (entry,) = journal.list_faxes(client)
    fakebox.state.sids.clear()  # stale sid inside entry.path must not matter
    assert journal.download(client, entry).startswith(b"%PDF")


def test_journal_without_fax_device(fakebox, client_for):
    fakebox.state.fax_configured = False
    assert journal.list_faxes(client_for(fakebox)) == []
