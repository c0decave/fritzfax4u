import pytest

from fritzfax.core.numbers import InvalidNumber, normalize_number, parse_destinations


# --- happy path -----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0531-49059113", "053149059113"),
        ("040 / 876 543 21", "04087654321"),
        ("(040) 87654321", "04087654321"),
        ("+49 40 87654321", "04087654321"),
        ("+44 20 1234 5678", "00442012345678"),
        ("0049 30 1", "0301"),  # own country written with 00: dialed nationally, like +49
        ("87654321", "87654321"),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


def test_normalize_number_respects_country_code():
    assert normalize_number("+43 1 234567", country_code="43") == "01234567"
    assert normalize_number("+49 30 1", country_code="43") == "0049301"


def test_parse_destinations_splits_on_comma_semicolon_newline():
    text = "0531-49059113, 040 87654321\n+49 40 123;0221 999"
    assert parse_destinations(text) == ["053149059113", "04087654321", "040123", "0221999"]


def test_parse_destinations_deduplicates_keeping_order():
    assert parse_destinations("030 1, 0301, +49 30 1") == ["0301"]


# --- sad path -------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["", "   ", "abc", "030-12a4", "++49", "+", "030 ext. 5"])
def test_normalize_number_rejects_invalid(raw):
    with pytest.raises(InvalidNumber):
        normalize_number(raw)


def test_parse_destinations_reports_offending_entry():
    with pytest.raises(InvalidNumber) as err:
        parse_destinations("030 1, hello, 040 2")
    assert "hello" in str(err.value)


def test_parse_destinations_empty_input_is_an_error():
    with pytest.raises(InvalidNumber):
        parse_destinations(" ,\n ; ")


# --- edge cases -----------------------------------------------------------------

def test_number_length_limit_matches_box_field():
    # fax_send.lua: <input name="dest_num" maxlength="40">
    assert normalize_number("0" + "1" * 39) == "0" + "1" * 39
    with pytest.raises(InvalidNumber):
        normalize_number("0" + "1" * 40)


def test_star_and_hash_are_allowed_for_box_dial_codes():
    # box dial prefixes such as "*111#" are legitimate on a FRITZ!Box
    assert normalize_number("*111#0301") == "*111#0301"


def test_missing_area_code_hint():
    from fritzfax.core.numbers import looks_local_without_area_code

    assert looks_local_without_area_code("87654321") is True
    assert looks_local_without_area_code("04087654321") is False
    assert looks_local_without_area_code("0044201") is False
    assert looks_local_without_area_code("110") is True


@pytest.mark.parametrize("number, country, area, expected", [
    ("87654321", "49", "30", "+49 30 87654321"),
    ("04087654321", "49", "40", "+49 4087654321"),
    ("0044201", "49", "30", "0044201"),
    ("87654321", "", "", "87654321"),
    ("", "49", "30", ""),
])
def test_international_display(number, country, area, expected):
    from fritzfax.core.numbers import international_display

    assert international_display(number, country, area) == expected


# --- international notation (review 2026-10-25: +49 030 was dialed as Greece) ----------

@pytest.mark.parametrize("raw, expected", [
    ("+49 (0)30 12345678", "03012345678"),    # the usual German notation with (0)
    ("+49 030 12345678", "03012345678"),      # trunk 0 written after the country code
    ("+49 30 12345678", "03012345678"),
    ("+44 (0)20 7946 0000", "00442079460000"),  # foreign (0) is not dialed from abroad
    ("+44 20 7946 0000", "00442079460000"),
])
def test_country_code_with_trunk_zero(raw, expected):
    assert normalize_number(raw, "49") == expected


@pytest.mark.parametrize("raw", ["+49", "+49 (0)", "+49 0", "+"])
def test_country_code_alone_is_not_a_number(raw):
    with pytest.raises(InvalidNumber):
        normalize_number(raw, "49")


# --- "00" written instead of "+" (review round 2) ----------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("0049 (0)30 1234567", "0301234567"),   # own country: national form, trunk zero once
    ("0049 030 1234567", "0301234567"),
    ("0049 30 1234567", "0301234567"),
    ("0043 (0)1 234567", "00431234567"),    # foreign: no trunk zero after the country code
    ("0043 1 234567", "00431234567"),
])
def test_double_zero_is_the_same_as_plus(raw, expected):
    assert normalize_number(raw, "49") == expected


@pytest.mark.parametrize("raw", ["0049", "00", "0049 (0)"])
def test_double_zero_with_nothing_after_the_country_code(raw):
    with pytest.raises(InvalidNumber):
        normalize_number(raw, "49")


def test_international_numbers_are_recognized():
    from fritzfax.core.numbers import is_international

    assert is_international("00431234567") is True
    assert is_international("0301234567") is False
    assert is_international("87654321") is False
