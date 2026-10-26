"""Parsing and normalizing fax destination numbers."""
from __future__ import annotations

import re

MAX_LEN = 40  # fax_send.lua: <input name="dest_num" maxlength="40">
_SEPARATORS = re.compile(r"[,;\n\r]+")
_DECORATION = re.compile(r"[\s/\-().]")
_VALID = re.compile(r"^[0-9*#]+$")
_TRUNK_ZERO = re.compile(r"\(\s*0\s*\)")  # "(0)" in "+49 (0)30 ..."
_SHOWN = re.compile(r"^\+?[0-9 ()/\-.]+$")


class InvalidNumber(ValueError):
    """A destination number cannot be dialed by the box.

    kind: "invalid", "too_long" or "empty"; value: the entry as typed (for a translated message)."""

    def __init__(self, message: str, kind: str = "invalid", value: str = ""):
        super().__init__(message)
        self.kind = kind
        self.value = value


def normalize_number(raw: str, country_code: str = "49") -> str:
    """Return the number as the box dials it.

    Decoration (spaces, slashes, dashes, brackets, dots) is removed. A leading
    ``+<own country code>`` (or ``00<own country code>``) becomes the national prefix ``0``; any
    other ``+`` becomes the international prefix ``00``. The trunk zero that international notation often
    keeps ("+49 (0)30 ...", "+49 030 ...") is never dialed after a country code.
    """
    text = (raw or "").strip()
    if text.startswith("+") or _DECORATION.sub("", text).startswith("00"):
        text = _TRUNK_ZERO.sub("", text, count=1)
    text = _DECORATION.sub("", text)
    if text.startswith("00"):  # "0049 30 ..." is written like "+49 30 ..."
        text = "+" + text[2:]
    if text.startswith("+"):
        rest = text[1:]
        if not rest or not rest[0].isdigit():
            raise InvalidNumber(f"invalid number: {raw!r}", "invalid", (raw or "").strip())
        if rest.startswith(country_code):
            national = rest[len(country_code):]
            national = national[1:] if national.startswith("0") else national
            if not national:
                raise InvalidNumber(f"invalid number: {raw!r}", "invalid", (raw or "").strip())
            text = "0" + national
        else:
            text = "00" + rest
    if not text or not _VALID.match(text):
        raise InvalidNumber(f"invalid number: {raw!r}", "invalid", (raw or "").strip())
    if len(text) > MAX_LEN:
        raise InvalidNumber(f"number longer than {MAX_LEN} characters: {raw!r}", "too_long", (raw or "").strip())
    return text


def parse_destinations(text: str, country_code: str = "49") -> list[str]:
    """Split a free-text list of destinations and normalize each entry."""
    result: list[str] = []
    for entry in _SEPARATORS.split(text or ""):
        if not entry.strip():
            continue
        number = normalize_number(entry, country_code)
        if number not in result:
            result.append(number)
    if not result:
        raise InvalidNumber("no destination number given", "empty")
    return result


def looks_local_without_area_code(number: str) -> bool:
    """True for numbers dialed without area code.

    Measured on FRITZ!OS 8.25: such numbers (and international ones) make the box
    demand a second-factor confirmation before it sends a fax.
    """
    return not number.startswith("0")


def is_international(number: str) -> bool:
    """True for a normalized number dialed abroad; the box asks for a confirmation for those."""
    return number.startswith("00")


def box_number_display(number: str, location: tuple[str, str] | None) -> str:
    """A fax number of the box as people read it: '+49 30 87654321' (the box itself stores 87654321)."""
    return international_display(number, *location) if location else number


def international_display(number: str, country: str, area: str) -> str:
    """Sender number for the header line: '+49 30 87654321' when country/area are known."""
    if not number or not country or number.startswith("00"):
        return number
    if number.startswith("0"):
        return f"+{country} {number[1:]}"
    return f"+{country} {area} {number}" if area else f"+{country} {number}"


def shown_number(raw: str) -> str:
    """The fax number to print on the fax instead of the sending one, tidied; "" = none chosen.

    It may differ from the number the box sends from, e.g. the number replies should go to. It is only
    printed (header line, cover sheet), never dialed, so it keeps the way it was written."""
    text = " ".join(raw.split())
    if not text:
        return ""
    if len(text) > MAX_LEN:
        raise InvalidNumber(f"{text!r} is longer than {MAX_LEN} characters", "too_long", text)
    if not _SHOWN.match(text) or sum(c.isdigit() for c in text) < 3:
        raise InvalidNumber(f"{text!r} is not a fax number", "invalid", text)
    return text


def printed_number(shown: str, sender_number: str, location: tuple[str, str] | None) -> str:
    """The fax number printed on the fax: the chosen one, else the sending number (international when known)."""
    return shown or box_number_display(sender_number, location)
