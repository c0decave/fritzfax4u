import pytest

from fritzfax.core.session import challenge_response


# Example values from AVM's technical note "Session-ID", sections for login version 1 and 2.
def test_pbkdf2_example_from_avm_note():
    assert challenge_response("2$10000$5A1711$2000$5A1722", "1example!") == (
        "5A1722$1798a1672bca7c6463d6b245f82b53703b0f50813401b03e4045a5861e689adb")


def test_md5_example_from_avm_note():
    assert challenge_response("1234567z", "äbc") == "1234567z-9e224a41eeefa284df7bb0f26c2913e2"


def test_md5_replaces_characters_above_255_with_dot():
    # AVM: "characters with a code point > 255 are replaced by '.'"
    assert challenge_response("abc", "€x") == challenge_response("abc", ".x")


@pytest.mark.parametrize("challenge", ["2$", "2$abc$11$22$33", "2$10$zz$20$11"])
def test_malformed_v2_challenge_is_rejected(challenge):
    from fritzfax.core.errors import ProtocolError

    with pytest.raises(ProtocolError):
        challenge_response(challenge, "pw")


def test_empty_password_still_produces_a_response():
    assert challenge_response("2$1$00$1$11", "").startswith("11$")
