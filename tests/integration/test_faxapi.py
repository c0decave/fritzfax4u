import base64
import itertools
from pathlib import Path

import pytest
from PIL import Image

from fritzfax.core import faxapi, sff, twofactor
from fritzfax.core.errors import (
    FaxBusy,
    FaxFailed,
    FaxResultUnknown,
    TwoFactorFailed,
    TwoFactorRequired,
    UploadRejected,
    ValidationFailed,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
SFF = sff.encode_pages([Image.new("1", (sff.LINE_WIDTH, 10), 255)])


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


# --- parsing the real page (unit level, live capture) -----------------------------

def test_parse_fax_page_from_live_capture():
    info = faxapi.parse_fax_page((FIXTURES / "fax_send.html").read_text())
    assert info.configured is True
    assert info.src_numbers == ["12345678"]
    assert info.identifier == "TestKennung"
    assert info.send_short == "TestShort"
    assert info.sender_long == "Test Sender"
    assert info.tfa_needed is True
    assert info.phonebook == {}


def test_parse_fax_page_not_configured():
    html = (FIXTURES / "fax_send.html").read_text().replace('var g_mode="fax_write"', 'var g_mode="fax_not_configured"')
    assert faxapi.parse_fax_page(html).configured is False


def test_parse_fax_page_rejects_unknown_html():
    from fritzfax.core.errors import ProtocolError

    with pytest.raises(ProtocolError):
        faxapi.parse_fax_page("<html>login</html>")


def test_parse_upload_answers():
    assert faxapi.parse_upload_answer('<input type="hidden" name="progress" value="start">') is None
    assert faxapi.parse_upload_answer(
        "<p>Der Faxversand ist gescheitert.<br>(data is not sff)</p>") == "data is not sff"


# --- happy path -----------------------------------------------------------------

def test_read_info(fakebox, client_for):
    info = faxapi.FaxApi(client_for(fakebox)).read_info()
    assert info.src_numbers == ["12345678"]


def test_precheck(fakebox, client_for):
    api = faxapi.FaxApi(client_for(fakebox))
    assert api.precheck("04087654321", "Sender") is False
    assert api.precheck("87654321", "Sender") is True
    assert api.precheck("0044201234", "Sender") is True


def test_upload_and_wait_success(fakebox, client_for):
    api = faxapi.FaxApi(client_for(fakebox))
    api.upload(SFF, "053149059113", "12345678")
    run = fakebox.state.runs[-1]
    assert run.sff == SFF and run.dest == "053149059113" and run.src == "12345678"
    clock = FakeClock()
    seen = []
    result = api.wait(on_status=seen.append, clock=clock, sleep=clock.sleep)
    assert result.reason == 0
    assert [s.status for s in seen][:3] == [2, 3, 4]
    assert seen[-1].status == 1


def test_upload_sends_base64_text_field(fakebox, client_for):
    api = faxapi.FaxApi(client_for(fakebox))
    api.upload(SFF, "053149059113", "12345678")
    assert fakebox.state.runs[-1].sff == SFF  # the fake box decodes the text field itself
    assert base64.b64encode(SFF)  # sanity


def test_two_factor_then_upload(fakebox, client_for):
    client = client_for(fakebox)
    api = faxapi.FaxApi(client)
    clock = FakeClock()
    shown = []
    twofactor.confirm(client, on_challenge=shown.append, clock=clock, sleep=clock.sleep)
    assert shown[0].methods == {"button", "dtmf"} and shown[0].code == "4711"
    api.upload(SFF, "87654321", "12345678")
    assert fakebox.state.runs


def test_two_factor_totp(fakebox, client_for):
    fakebox.state.tfa_auto_confirm_after = None
    client = client_for(fakebox)
    clock = FakeClock()

    def on_challenge(ch):
        assert twofactor.submit_totp(client, "123456") is True

    twofactor.confirm(client, on_challenge=on_challenge, clock=clock, sleep=clock.sleep)


# --- sad path -------------------------------------------------------------------

def test_precheck_empty_number_is_validation_error(fakebox, client_for):
    with pytest.raises(ValidationFailed) as err:
        faxapi.FaxApi(client_for(fakebox)).precheck("", "x")
    assert err.value.field == "dest_num"


def test_upload_without_two_factor_is_reported_as_such(fakebox, client_for):
    with pytest.raises(TwoFactorRequired):
        faxapi.FaxApi(client_for(fakebox)).upload(SFF, "87654321", "12345678")
    assert not fakebox.state.runs


def test_upload_not_sff_is_refused_locally(fakebox, client_for):
    # Measured on FRITZ!OS 8.25: the box accepts any base64 payload, dials, and only then
    # fails (reason 0x3319). So invalid data must never leave the client.
    with pytest.raises(UploadRejected, match="nothing was sent"):
        faxapi.FaxApi(client_for(fakebox)).upload(b"hello", "03012", "12345678")
    assert not fakebox.state.runs
    assert ("POST", "/cgi-bin/firmwarecfg") not in fakebox.state.requests


def test_truncated_sff_is_refused_locally(fakebox, client_for):
    with pytest.raises(UploadRejected, match="nothing was sent"):
        faxapi.FaxApi(client_for(fakebox)).upload(SFF[:-20], "03012", "12345678")
    assert not fakebox.state.runs


def test_fakebox_mirrors_box_dialing_on_garbage(fakebox, client_for):
    # documents the measured box behavior that makes the local check necessary
    import base64

    client = client_for(fakebox)
    resp = client.post_multipart("/cgi-bin/firmwarecfg", {"NumDest": "03012", "NumSrc": "12345678",
                                                          "FaxUploadFile": base64.b64encode(b"hello").decode()})
    assert 'name="progress"' in resp.text
    clock = FakeClock()
    with pytest.raises(FaxFailed) as err:
        faxapi.FaxApi(client).wait(clock=clock, sleep=clock.sleep)
    assert err.value.reason == 0x3319


def test_reason_is_reported(fakebox_factory, client_for):
    box = fakebox_factory(scenario="reason:3490")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    with pytest.raises(FaxFailed) as err:
        api.wait(clock=clock, sleep=clock.sleep)
    assert err.value.reason == 0x3490


def test_box_that_never_ends_the_call_gives_an_unknown_result(fakebox_factory, client_for):
    # the box still reports a call after the budget and the grace: fritzfax must not claim a failure
    box = fakebox_factory(scenario="stall")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    with pytest.raises(FaxResultUnknown, match="result unknown") as err:
        api.wait(clock=clock, sleep=clock.sleep, stall_timeout=30, overdue_grace=60)
    assert isinstance(err.value, FaxFailed) and err.value.reason == 0
    assert clock.t > 30 + 60


def test_late_failure_of_the_box_is_reported_with_its_reason(fakebox_factory, client_for):
    # measured 2026-10-26: CONNECTED at 100 % for ~8 min, then READY with reason 0x3301; fritzfax
    # had given up after 180 s with "no progress" and never saw the box's own result
    box = fakebox_factory(scenario="slow:40:3301")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    overdue = []
    with pytest.raises(FaxFailed) as err:
        api.wait(clock=clock, sleep=clock.sleep, stall_timeout=10, overdue_grace=600, on_overdue=overdue.append)
    assert not isinstance(err.value, FaxResultUnknown)
    assert err.value.reason == 0x3301
    assert len(overdue) == 1 and overdue[0].status == 4


def test_late_success_of_the_box_counts(fakebox_factory, client_for):
    box = fakebox_factory(scenario="slow:40:0")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    final = api.wait(clock=clock, sleep=clock.sleep, stall_timeout=10, overdue_grace=600)
    assert final.status == 1 and final.reason == 0


def test_no_overdue_notice_within_the_budget(fakebox_factory, client_for):
    box = fakebox_factory(scenario="slow:5:0")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    overdue = []
    assert api.wait(clock=clock, sleep=clock.sleep, stall_timeout=10, on_overdue=overdue.append).reason == 0
    assert overdue == []


def test_grace_defaults_to_the_module_constant(fakebox_factory, client_for, monkeypatch):
    monkeypatch.setattr(faxapi, "OVERDUE_GRACE", 7.0)
    box = fakebox_factory(scenario="stall")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    with pytest.raises(FaxResultUnknown):
        api.wait(clock=clock, sleep=clock.sleep, stall_timeout=3)
    assert clock.t < 3 + 7 + 5


def test_cancel_while_overdue_stops_waiting(fakebox_factory, client_for):
    box = fakebox_factory(scenario="stall")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    with pytest.raises(FaxFailed, match="cancel"):
        api.wait(clock=clock, sleep=clock.sleep, stall_timeout=3, overdue_grace=600,
                 should_cancel=lambda: clock.t > 20)


def test_never_starts(fakebox_factory, client_for):
    box = fakebox_factory(scenario="never_starts")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    with pytest.raises(FaxFailed, match="did not start"):
        api.wait(clock=clock, sleep=clock.sleep, start_timeout=20)


def test_unavailable(fakebox_factory, client_for):
    box = fakebox_factory(scenario="unavailable")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    with pytest.raises(FaxFailed, match="unavailable"):
        api.wait(clock=clock, sleep=clock.sleep)


def test_busy_box_refuses_second_fax(fakebox, client_for):
    fakebox.state.status = {"status": 4, "status_text": "FAXSEND_CONNECTED", "progress": 10, "reason": 0}
    with pytest.raises(FaxBusy):
        faxapi.FaxApi(client_for(fakebox)).ensure_idle()


def test_two_factor_denied(fakebox, client_for):
    client = client_for(fakebox)
    fakebox.state.tfa_auto_confirm_after = None
    clock = FakeClock()
    with pytest.raises(TwoFactorFailed, match="timed out"):
        twofactor.confirm(client, on_challenge=lambda c: None, clock=clock, sleep=clock.sleep, timeout=60)


@pytest.mark.parametrize("code, text", [("91", "another session"), ("92", "too many")])
def test_two_factor_start_errors(fakebox, client_for, code, text):
    fakebox.state.tfa_start_error = code
    with pytest.raises(TwoFactorFailed, match=text):
        twofactor.start(client_for(fakebox))


def test_wrong_totp(fakebox, client_for):
    client = client_for(fakebox)
    twofactor.start(client)
    assert twofactor.submit_totp(client, "000000") is False


# --- edge cases -----------------------------------------------------------------

def test_status_after_session_expiry_is_renewed(fakebox, client_for):
    client = client_for(fakebox)
    api = faxapi.FaxApi(client)
    api.upload(SFF, "03012", "12345678")
    fakebox.state.sids.clear()
    clock = FakeClock()
    assert api.wait(clock=clock, sleep=clock.sleep).reason == 0


def test_progress_resets_the_stall_timer(fakebox_factory, client_for):
    # progress 33 -> 66 -> 99 arrives every poll; a stall timeout shorter than the whole
    # transfer but longer than one poll must not fire
    box = fakebox_factory()
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    assert api.wait(clock=clock, sleep=clock.sleep, stall_timeout=1.5, poll_interval=1).reason == 0


def test_cancel_check_aborts_waiting(fakebox_factory, client_for):
    box = fakebox_factory(scenario="stall")
    api = faxapi.FaxApi(client_for(box))
    api.upload(SFF, "03012", "12345678")
    clock = FakeClock()
    counter = itertools.count()
    with pytest.raises(FaxFailed, match="cancel"):
        api.wait(clock=clock, sleep=clock.sleep, should_cancel=lambda: next(counter) > 3)
