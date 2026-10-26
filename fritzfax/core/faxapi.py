"""The fax-send protocol of the FRITZ!Box web interface (verified on FRITZ!OS 8.25).

    GET  /fon_devices/fax_send.lua                     -> page with configuration (parsed)
    POST /fon_devices/fax_send.lua  btn_send, dest_num  -> {"data":{"needs2FA":bool,...}}
    POST /cgi-bin/firmwarecfg  NumDest, NumSrc, FaxUploadFile=base64(SFF)  -> ok/failed HTML
    GET  /fon_devices/fax_send.lua?myXhr=1&query=refresh&no_sidrenew=1     -> status JSON
"""
from __future__ import annotations

import base64
import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .errors import (
    FaxBusy,
    FaxFailed,
    FaxNotConfigured,
    FaxResultUnknown,
    ProtocolError,
    TwoFactorRequired,
    UploadRejected,
    ValidationFailed,
)
from .session import BoxClient
from . import sff as sffmod

FAX_PAGE = "/fon_devices/fax_send.lua"
UPLOAD = "/cgi-bin/firmwarecfg"

# libfaxsendlua.so state names; numbers as used by fax_send.lua cbRefresh()
STATUS_TEXT = {-1: "FAXSEND_UNAVAILABLE", 0: "FAXSEND_UNAVAILABLE", 1: "FAXSEND_READY", 2: "FAXSEND_PREPARE",
               3: "FAXSEND_CONNECTING", 4: "FAXSEND_CONNECTED", 5: "FAXSEND_REPORT"}
ACTIVE = {2, 3, 4}
FINISHED = {1, 5}
ERROR = {-1, 0}
# After the time budget fritzfax keeps waiting this long for the box's own result: measured 2026-10-26,
# the box ended a failing one-page call by itself after about 8 minutes (reason 0x3301).
OVERDUE_GRACE = 900.0
TWO_FACTOR_HINT = "bestätigt"  # "Die Ausführung muss zusätzlich bestätigt werden."


@dataclass
class FaxInfo:
    configured: bool
    src_numbers: list[str]
    identifier: str
    send_short: str
    sender_long: str
    tfa_needed: bool
    phonebook: dict[str, list[dict]] = field(default_factory=dict)  # book id -> [{name, number}]
    phonebook_names: dict[str, str] = field(default_factory=dict)  # book id -> name


@dataclass
class FaxStatus:
    status: int
    status_text: str
    progress: int
    reason: int

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


def _js_string(page: str, name: str) -> str:
    m = re.search(rf'{re.escape(name)}\s*=\s*"((?:[^"\\]|\\.)*)"', page)
    return json.loads(f'"{m.group(1)}"') if m else ""


def parse_fax_page(page: str) -> FaxInfo:
    mode = re.search(r'var g_mode="([^"]*)"', page)
    if not mode:
        raise ProtocolError("unexpected fax page (no g_mode) - not logged in or unsupported FRITZ!OS")
    select = re.search(r'<select[^>]*id="uiViewFromNum"[^>]*>(.*?)</select>', page, re.S)
    numbers = re.findall(r'<option value="([^"]*)"', select.group(1)) if select else []
    sender = re.search(r'name="from1">([^<]*)</textarea>', page)
    tfa = re.search(r"var tfaNeeded = (true|false)", page)
    pbe_match = re.search(r"var g_pbe=(.*?);\s*\n", page)
    phonebook: dict = {}
    if pbe_match:
        try:
            parsed = json.loads(pbe_match.group(1))
            phonebook = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            phonebook = {}
    books = re.search(r'<select[^>]*id="uiFonbooks"[^>]*>(.*?)</select>', page, re.S)
    names = {}
    if books:
        for value, label in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', books.group(1)):
            if value != "manu":
                names[value] = html.unescape(label)
    return FaxInfo(
        configured=mode.group(1) in ("fax_write", "fax_progress"),
        src_numbers=[html.unescape(n) for n in numbers],
        identifier=_js_string(page, "data.identifier"),
        send_short=_js_string(page, "data.send_short"),
        sender_long=html.unescape(sender.group(1)) if sender else "",
        tfa_needed=bool(tfa and tfa.group(1) == "true"),
        phonebook=phonebook,
        phonebook_names=names,
    )


def parse_upload_answer(page: str) -> str | None:
    """None if the box accepted the upload, else the status text it reported."""
    if re.search(r'name="progress"', page):
        return None
    text = re.sub(r"<[^>]+>", " ", page)
    m = re.findall(r"\(([^()]*)\)", text)
    status = [s.strip() for s in m if s.strip() and "postUpload" not in s]
    return html.unescape(status[-1]) if status else "unknown error"


class FaxApi:
    def __init__(self, client: BoxClient):
        self.client = client

    def read_info(self) -> FaxInfo:
        resp = self.client.get(FAX_PAGE)
        if resp.status != 200:
            raise ProtocolError(f"fax page answered HTTP {resp.status}")
        return parse_fax_page(resp.text)

    def precheck(self, dest: str, sender_name: str) -> bool:
        """Validate the destination on the box; return True if it needs 2FA."""
        answer = self.client.post(FAX_PAGE, {"btn_send": "", "dest_num": dest, "from1": sender_name}).json()
        data = answer.get("data", {})
        if data.get("btn_send") == "valerror":
            err = data.get("valerror", {})
            raise ValidationFailed((err.get("tomark") or ["dest_num"])[0], err.get("alert", ""))
        if data.get("btn_send") != "ok":
            raise ProtocolError(f"unexpected precheck answer: {answer!r}")
        return bool(data.get("needs2FA"))

    def status(self) -> FaxStatus:
        resp = self.client.get(FAX_PAGE, {"no_sidrenew": "1", "myXhr": "1", "query": "refresh"})
        try:
            d = resp.json()
        except ProtocolError:
            raise
        if not isinstance(d, dict):
            d = {}
        st = int(d.get("status", -1))
        return FaxStatus(st, d.get("status_text") or STATUS_TEXT.get(st, "?"), int(d.get("progress", 0)),
                         int(d.get("reason", 0)))

    def ensure_idle(self) -> FaxStatus:
        st = self.status()
        if st.active:
            raise FaxBusy("the box is already sending a fax")
        return st

    def upload(self, sff: bytes, dest: str, src: str) -> None:
        # The box does not validate the payload before dialing: measured on FRITZ!OS 8.25 it
        # accepted non-SFF data, called the destination and failed afterwards (reason 0x3319).
        # So the document is checked here and broken data never leaves the client.
        try:
            pages = sffmod.page_rows(sff)
        except sffmod.SffError as exc:
            raise UploadRejected(f"not a valid fax document ({exc}); nothing was sent") from exc
        if any(not rows for rows in pages):
            raise UploadRejected("fax document contains an empty page; nothing was sent")
        resp = self.client.post_multipart(UPLOAD, {
            "NumDest": dest, "NumSrc": src, "FaxUploadFile": base64.b64encode(sff).decode()})
        if resp.status != 200:
            raise ProtocolError(f"upload answered HTTP {resp.status}")
        status = parse_upload_answer(resp.text)
        if status is None:
            return
        if TWO_FACTOR_HINT in status:
            raise TwoFactorRequired(status)
        raise UploadRejected(status)

    def wait(self, *, on_status: Callable[[FaxStatus], None] | None = None, poll_interval: float = 1.0,
             start_timeout: float = 60.0, stall_timeout: float = 180.0, overdue_grace: float | None = None,
             on_overdue: Callable[[FaxStatus], None] | None = None,
             should_cancel: Callable[[], bool] | None = None,
             clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> FaxStatus:
        """Poll until the box reports the end of the transmission.

        `progress` stays at 100 during the whole call (docs/protocol.md), so the time without a status
        change is the only budget. When it runs out the box is still in the call and decides the result
        itself: fritzfax reports that once (on_overdue) and keeps waiting `overdue_grace` seconds more.
        Only then it stops, with an unknown result instead of a failure the box never reported."""
        grace = OVERDUE_GRACE if overdue_grace is None else overdue_grace
        started = clock()
        last_change = started
        seen_active = False
        overdue = False
        last_key = None
        while True:
            if should_cancel and should_cancel():
                raise FaxFailed("waiting cancelled by the user (the box may still be sending)")
            st = self.status()
            if on_status:
                on_status(st)
            now = clock()
            key = (st.status, st.progress)
            if key != last_key:
                last_key, last_change = key, now
            # before an active state the status may still be the one of the previous fax (docs/protocol.md),
            # an error as well as a result: only what follows the start counts
            if st.active:
                seen_active = True
            elif seen_active and st.status in ERROR:
                raise FaxFailed("fax sending unavailable on the box", st.reason)
            elif seen_active and st.status in FINISHED:
                if st.reason:
                    raise FaxFailed(f"the box reports transmission error 0x{st.reason:04X}", st.reason)
                return st
            if not seen_active and now - started > start_timeout:
                if st.status in ERROR:
                    raise FaxFailed("fax sending unavailable on the box", st.reason)
                raise FaxFailed("the box did not start sending")
            if seen_active and now - last_change > stall_timeout:
                if not overdue:
                    overdue = True
                    if on_overdue:
                        on_overdue(st)
                if now - last_change > stall_timeout + grace:
                    raise FaxResultUnknown(now - started)
            sleep(poll_interval)


def require_configured(info: FaxInfo) -> None:
    if not info.configured or not info.src_numbers:
        raise FaxNotConfigured("the fax function of the box is not set up")
