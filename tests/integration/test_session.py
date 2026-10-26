import pytest

from fritzfax.core.errors import (
    CertificateMismatch,
    ConnectionFailed,
    LoginBlocked,
    LoginFailed,
    SessionExpired,
    UntrustedCertificate,
)
from fritzfax.core.session import BoxClient


# --- happy path -----------------------------------------------------------------

def test_login_v2_and_logout(fakebox, client_for):
    client = client_for(fakebox)
    sid = client.login()
    assert len(sid) == 16 and sid != "0" * 16
    assert sid in fakebox.state.sids
    assert client.rights.get("Dial") == 2
    client.logout()
    assert sid not in fakebox.state.sids
    assert client.sid is None


def test_login_v1_md5(fakebox_factory, client_for):
    box = fakebox_factory(login_version=1)
    assert client_for(box).login() in box.state.sids


def test_username_defaults_to_last_box_user(fakebox):
    client = BoxClient(fakebox.url, username="", password=fakebox.state.password)
    client.login()
    assert client.username == fakebox.state.username


def test_requests_carry_the_sid(fakebox, client_for):
    client = client_for(fakebox)
    resp = client.get("/query.lua", {"x": "telcfg:settings/FaxKennung"})
    assert resp.json() == {"x": "TestKennung"}


def test_expired_session_is_renewed_once_transparently(fakebox, client_for):
    client = client_for(fakebox)
    old = client.login()
    fakebox.state.sids.clear()  # box forgot the session (timeout, reboot)
    resp = client.get("/query.lua", {"x": "telcfg:settings/FaxKennung"})
    assert resp.json() == {"x": "TestKennung"}
    assert client.sid != old


def test_tls_with_pinned_fingerprint(fakebox_factory):
    box = fakebox_factory(tls=True)
    probe = BoxClient(box.url, username=box.state.username, password=box.state.password)
    with pytest.raises(UntrustedCertificate) as err:
        probe.login()
    fp = err.value.fingerprint
    assert len(fp.split(":")) == 32
    client = BoxClient(box.url, username=box.state.username, password=box.state.password, cert_sha256=fp)
    assert client.login() in box.state.sids


def test_tls_trust_on_first_use_reports_fingerprint(fakebox_factory):
    box = fakebox_factory(tls=True)
    client = BoxClient(box.url, username=box.state.username, password=box.state.password,
                       trust_unknown_cert=True)
    client.login()
    assert client.peer_fingerprint and len(client.peer_fingerprint) == 95


# --- sad path -------------------------------------------------------------------

def test_wrong_password(fakebox, client_for):
    with pytest.raises(LoginFailed):
        client_for(fakebox, password="nope").login()


def test_block_time_is_reported(fakebox, client_for):
    fakebox.state.block_time = 7
    with pytest.raises(LoginBlocked) as err:
        client_for(fakebox).login()
    assert err.value.seconds == 7


def test_connection_refused():
    client = BoxClient("http://127.0.0.1:9", username="u", password="p", timeout=2)
    with pytest.raises(ConnectionFailed):
        client.login()


def test_pinned_fingerprint_mismatch(fakebox_factory):
    box = fakebox_factory(tls=True)
    wrong = ":".join(["AA"] * 32)
    client = BoxClient(box.url, username="fritzuser", password="secret-pw", cert_sha256=wrong)
    with pytest.raises(CertificateMismatch) as err:
        client.login()
    assert err.value.expected == wrong


@pytest.mark.parametrize("exc", [BrokenPipeError(32, "Broken pipe"), ConnectionResetError(104, "reset")])
def test_fake_box_ignores_client_hangups(fakebox, capsys, exc):
    # a client that aborts (e.g. after a fingerprint mismatch) must not print tracebacks into the run
    try:
        raise exc
    except OSError:
        fakebox.server.handle_error(None, ("127.0.0.1", 1))
    assert capsys.readouterr().err == ""


def test_fake_box_still_reports_its_own_errors(fakebox, capsys):
    try:
        raise ValueError("bug in the fake box")
    except ValueError:
        fakebox.server.handle_error(None, ("127.0.0.1", 1))
    assert "ValueError: bug in the fake box" in capsys.readouterr().err


def test_session_that_cannot_be_renewed(fakebox, client_for, monkeypatch):
    client = client_for(fakebox)
    client.login()
    # the box keeps redirecting even right after a fresh login
    monkeypatch.setattr(fakebox.state, "sids", _AlwaysEmpty())
    with pytest.raises((SessionExpired, LoginFailed)):
        client.get("/query.lua", {"x": "telcfg:settings/FaxKennung"})


class _AlwaysEmpty(dict):
    def __setitem__(self, key, value):
        pass


# --- edge cases -----------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("192.0.2.1", "https://192.0.2.1"),
    ("fritz.box/", "https://fritz.box"),
    ("http://192.0.2.1:8080/", "http://192.0.2.1:8080"),
])
def test_url_normalization(raw, expected):
    assert BoxClient(raw, username="u", password="p").base_url == expected


@pytest.mark.parametrize("url", ["", "   "])
def test_missing_address_is_reported_clearly(url):
    from fritzfax.core.errors import BoxNotConfigured

    with pytest.raises(BoxNotConfigured, match="no FRITZ!Box address configured"):
        BoxClient(url, username="u", password="p")


def test_invalid_url_is_rejected():
    from fritzfax.core.errors import BoxError

    with pytest.raises(BoxError):
        BoxClient("ftp://box", username="u", password="p")


def test_password_is_not_in_repr(fakebox):
    client = BoxClient(fakebox.url, username="u", password="topsecret")
    assert "topsecret" not in repr(client)


@pytest.mark.parametrize("raw", ["ftp://fritz.box", "fritz.box:abc", "https://fritz.box:99999", "http://", "https://:80"])
def test_invalid_address_has_its_own_error(raw):
    from fritzfax.core.errors import InvalidBoxAddress

    with pytest.raises(InvalidBoxAddress) as info:
        BoxClient(raw, username="u", password="p")
    assert info.value.address == raw.strip()
