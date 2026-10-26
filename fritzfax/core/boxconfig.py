"""Read and change the fax settings of the box.

Reading uses query.lua (telcfg:settings/*). Writing goes through the form of
/fon_devices/edit_fax_intern.lua exactly like a browser: the page is fetched, the whole form
is serialized, only the requested fields are changed, and everything is posted back. This
matters because the box's do_save() writes *every* field - a POST without num_1 would clear
the fax numbers (edit_fax_intern.lua do_save, FRITZ!OS 8.25).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable

from . import twofactor
from .errors import BoxError, ProtocolError, TwoFactorFailed, ValidationFailed
from .session import BoxClient

PAGE = "/fon_devices/edit_fax_intern.lua"
# limits from edit_fax_intern.lua valprog()
LIMITS = {"identifier": (0, 20, "headline"), "header": (1, 40, "from_headline"), "sender_name": (0, 200, "from_name")}
FIELDS = {"identifier": "headline", "header": "from_headline", "sender_name": "from_name"}


@dataclass
class FaxSettings:
    identifier: str          # FaxKennung: station id the remote side sees (TSI)
    header: str              # FaxSenderShort: header line of the box's own fax pages
    sender_name: str         # FaxSenderLong
    numbers: list[str]       # FaxMSN0..9
    send_only: bool
    fax_switch: bool
    email_forward: bool
    email_address: str       # "" = the push-service default address
    store_internal: bool
    store_usb: bool
    country_code: str
    area_code: str


def read_settings(client: BoxClient) -> FaxSettings:
    keys = {"kennung": "FaxKennung", "short": "FaxSenderShort", "long": "FaxSenderLong",
            "sendonly": "FaxSendOnly", "switch": "FaxSwitch", "mail": "FaxMailActive", "addr": "FaxMailAddress0",
            "lkz": "Location/LKZ", "okz": "Location/OKZ", **{f"msn{i}": f"FaxMSN{i}" for i in range(10)}}
    v = client.get("/query.lua", {k: f"telcfg:settings/{name}" for k, name in keys.items()}).json()
    mode = v.get("mail", "0")
    return FaxSettings(
        identifier=v.get("kennung", ""), header=v.get("short", ""), sender_name=v.get("long", ""),
        numbers=[v[f"msn{i}"] for i in range(10) if v.get(f"msn{i}")],
        send_only=v.get("sendonly") == "1", fax_switch=v.get("switch") == "1",
        email_forward=mode in ("1", "3", "5"), email_address=v.get("addr", ""),
        store_internal=mode in ("4", "5"), store_usb=mode in ("2", "3"),
        country_code=v.get("lkz", ""), area_code=v.get("okz", ""))


def read_location(client: BoxClient) -> tuple[str, str] | None:
    """(country code, area code) of the box, None if the box does not tell (no permission for query.lua)."""
    try:
        st = read_settings(client)
    except (BoxError, ValueError):
        return None
    return (st.country_code, st.area_code) if st.country_code else None


@dataclass
class Control:
    tag: str
    name: str
    type: str = ""
    value: str = ""
    checked: bool = False
    disabled: bool = False
    options: list[tuple[str, bool]] = field(default_factory=list)  # select: (value, selected)


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.controls: list[Control] = []
        self._select: Control | None = None
        self._textarea: Control | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name"):
            self.controls.append(Control("input", a["name"], (a.get("type") or "text").lower(), a.get("value") or "",
                                         "checked" in a, "disabled" in a))
        elif tag == "select" and a.get("name"):
            self._select = Control("select", a["name"], disabled="disabled" in a)
            self.controls.append(self._select)
        elif tag == "option" and self._select is not None:
            self._select.options.append((a.get("value") or "", "selected" in a))
        elif tag == "textarea" and a.get("name"):
            self._textarea = Control("textarea", a["name"], disabled="disabled" in a)
            self.controls.append(self._textarea)

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea.value += data


@dataclass
class Form:
    controls: list[Control]

    def serialize(self) -> dict[str, str]:
        """The name/value pairs a browser would submit."""
        data: dict[str, str] = {}
        for c in self.controls:
            if c.disabled:
                continue
            if c.tag == "input":
                if c.type in ("checkbox", "radio"):
                    if c.checked:
                        data[c.name] = c.value or "on"
                elif c.type not in ("submit", "button", "image", "reset", "file"):
                    data[c.name] = c.value
            elif c.tag == "select":
                chosen = [v for v, sel in c.options if sel] or [v for v, _ in c.options[:1]]
                if chosen:
                    data[c.name] = chosen[0]
            elif c.tag == "textarea":
                data[c.name] = c.value
        return data


def parse_form(page: str) -> Form:
    p = _FormParser()
    p.feed(page)
    if not any(c.name == "headline" for c in p.controls):
        raise ProtocolError("unexpected fax settings page - not logged in or unsupported FRITZ!OS")
    return Form(p.controls)


def check_limits(**values) -> None:
    for key, value in values.items():
        if key in LIMITS and value is not None:
            lo, hi, name = LIMITS[key]
            if not lo <= len(value) <= hi:
                raise ValidationFailed(name, f"length must be {lo}-{hi} characters")


def update_settings(client: BoxClient, *, identifier: str | None = None, header: str | None = None,
                    sender_name: str | None = None, email_forward: bool | None = None,
                    email_address: str | None = None, store_internal: bool | None = None,
                    send_only: bool | None = None,
                    on_two_factor: Callable[[twofactor.Challenge], None] | None = None,
                    tfa_timeout: float = 120.0, tfa_poll_interval: float = 1.0) -> None:
    """Change the given settings; everything else stays as the box has it."""
    check_limits(identifier=identifier, header=header, sender_name=sender_name)
    form = parse_form(client.get(PAGE).text)
    data = form.serialize()
    for key, value in (("identifier", identifier), ("header", header), ("sender_name", sender_name)):
        if value is not None:
            data[FIELDS[key]] = value
    if email_address is not None:
        email_forward = True if email_forward is None else email_forward
        data["mail_type"] = "custom" if email_address else "default"
        data["fax_addr"] = email_address
    if email_forward is not None:
        if email_forward:
            data["email_send"] = "on"
        else:
            data.pop("email_send", None)
    if store_internal is not None:
        data["fax_save"] = "intern" if store_internal else "no"
    if send_only is not None:
        data["faxsendoption"] = "OnlySend" if send_only else "SendAndReceive"
    data["apply"] = ""
    for _attempt in range(2):
        answer = client.post(PAGE, data).json().get("data", {})
        state = answer.get("apply")
        if state == "ok":
            return
        if state == "valerror":
            err = answer.get("valerror", {})
            raise ValidationFailed((err.get("tomark") or ["?"])[0], err.get("alert", ""))
        if state == "twofactor":
            if on_two_factor is None:
                twofactor.cancel(client)
                raise TwoFactorFailed("the box wants a confirmation for this change")
            challenge_state = answer.get("twofactor", "")
            if challenge_state.startswith("starterror"):
                raise TwoFactorFailed(twofactor.START_ERRORS.get(challenge_state.partition(";")[2],
                                                                 "confirmation could not be started"))
            on_two_factor(twofactor.parse_state(challenge_state))
            twofactor.wait_confirmed(client, timeout=tfa_timeout, poll_interval=tfa_poll_interval)
            continue
        if state == "saveerror":
            raise ProtocolError(f"the box could not save the settings: {answer.get('saveerror')}")
        raise ProtocolError(f"unexpected answer when saving: {answer!r}")
    raise TwoFactorFailed("the box still wants a confirmation after it was given")


__all__ = ["FaxSettings", "Form", "check_limits", "parse_form", "read_settings", "update_settings", "time"]
