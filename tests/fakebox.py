"""A fake FRITZ!Box speaking the HTTP dialect measured on FRITZ!OS 8.25.

Every behavior here mirrors a live measurement or a firmware source line:
  * login_sid.lua version 2 (PBKDF2) and version 1 (MD5), BlockTime after bad logins
  * an invalid SID gets 303 -> "/" on Lua pages and 302 -> "/index.lua" on firmwarecfg
  * fax_send.lua: page (sanitized live capture), precheck JSON, status JSON
  * firmwarecfg: base64 SFF upload, 2FA gate, ok/failed HTML pages
  * twofactor.lua: tfa_start / tfa_active / tfa_googleauth / tfa_cancel
  * query.lua, edit_fax_intern.lua (form + do_save semantics), data.lua faxOverview
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import subprocess
import threading
import time
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from fritzfax.core import sff  # the record parser: pages are counted like the box does

FIXTURES = Path(__file__).parent / "fixtures"
ZERO_SID = "0" * 16

OK_HTML = """<html><body><div class="blue_bar_back"><h2>Fax senden</h2></div>
<form method="POST" name="mainform" action="/index.lua">
<input type="hidden" id="uiLP" name="lp" value="fax"><input type="hidden" name="sid" value="{sid}">
<input type="hidden" name="progress" value="start"></form></body></html>"""
FAILED_HTML = """<html><body><div class="blue_bar_back"><h2>Fax senden</h2></div>
<form method="POST" name="mainform" action="/index.lua"><p>Der Faxversand ist gescheitert.<br>({status})</p>
<input type="hidden" id="uiLP" name="lp" value="fax"><input type="hidden" name="sid" value="{sid}"></form></body></html>"""


@dataclass
class FaxRun:
    sff: bytes
    dest: str
    src: str
    scenario: str
    polls: int = 0


@dataclass
class BoxState:
    username: str = "fritzuser"
    password: str = "secret-pw"
    login_version: int = 2
    iterations: tuple[int, int] = (10, 20)
    block_time: int = 0
    sids: dict[str, dict] = field(default_factory=dict)
    fax_configured: bool = True
    src_numbers: list[str] = field(default_factory=lambda: ["12345678"])
    identifier: str = "TestKennung"
    send_short: str = "TestShort"
    sender_long: str = "Test Sender"
    country: str = "49"
    area: str = "30"
    # fax run behavior: success | reason:<hex> | stall | unavailable | never_starts |
    # slow:<polls>:<hex> (measured 2026-10-26: CONNECTED at 100 % for minutes, then the box's own result)
    scenario: str = "success"
    runs: list[FaxRun] = field(default_factory=list)
    status: dict = field(default_factory=lambda: {"status": 1, "status_text": "FAXSEND_READY",
                                                  "progress": 100, "reason": 0})
    # second factor
    tfa_auto_confirm_after: int | None = 2   # polls until the user "pressed the button"
    tfa_method: str = "button,dtmf"
    tfa_code: str = "4711"
    totp_code: str = "123456"
    tfa_start_error: str = ""                 # "91" other session, "92" too many attempts
    config_save_needs_2fa: bool = False
    # box fax settings (telcfg:settings/*)
    config: dict = field(default_factory=dict)
    journal: list[dict] = field(default_factory=list)
    requests: list[tuple[str, str]] = field(default_factory=list)
    # evidence the box keeps about a send (journal archive copy, call list)
    archive_mode: str = "exact"     # exact | tampered | missing
    journal_for_runs: bool = True
    call_list: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        if not self.config:
            self.config = {
                "FaxKennung": self.identifier, "FaxSenderShort": self.send_short,
                "FaxSenderLong": self.sender_long, "FaxSendOnly": "0", "FaxSwitch": "0",
                "FaxMailActive": "5", "FaxSavePath": "", "FaxMailAddress0": "",
                **{f"FaxMSN{i}": "" for i in range(10)},
            }
            self.config["FaxMSN0"] = self.src_numbers[0]

    # --- helpers ---------------------------------------------------------------
    def new_sid(self) -> str:
        sid = secrets.token_hex(8)
        self.sids[sid] = {"tfa_state": "", "tfa_active": False, "tfa_polls": 0}
        return sid

    def query(self, key: str) -> str:
        if key.startswith("telcfg:settings/"):
            name = key.split("/", 1)[1]
            if name == "Location/LKZ":
                return self.country
            if name == "Location/OKZ":
                return self.area
            if name == "Location/OKZPrefix":
                return "0"
            if name == "Location/LKZPrefix":
                return "00"
            return self.config.get(name, "")
        return ""


def pbkdf2_response(challenge: str, password: str) -> str:
    _, it1, s1, it2, s2 = challenge.split("$")
    h1 = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(s1), int(it1))
    h2 = hashlib.pbkdf2_hmac("sha256", h1, bytes.fromhex(s2), int(it2))
    return f"{s2}${h2.hex()}"


def md5_response(challenge: str, password: str) -> str:
    return f"{challenge}-" + hashlib.md5(f"{challenge}-{password}".encode("utf-16le")).hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = "fakebox"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output clean
        pass

    @property
    def box(self) -> BoxState:
        return self.server.box  # type: ignore[attr-defined]

    # --- plumbing ----------------------------------------------------------------
    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj), "application/json")

    def _redirect(self, status=303, location="/"):
        self._send(status, b"", headers={"Location": location})

    def _params(self):
        url = urlparse(self.path)
        params = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        files = {}
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")
            if ctype.startswith("multipart/form-data"):
                msg = BytesParser(policy=HTTP).parsebytes(
                    b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    payload = part.get_payload(decode=True) or b""
                    if part.get_filename():
                        files[name] = payload
                    else:
                        params[name] = payload.decode("utf-8", "replace")
            else:
                for k, v in parse_qs(raw.decode(), keep_blank_values=True).items():
                    params[k] = v[-1]
        return url.path, params, files

    def _session(self, params):
        return self.box.sids.get(params.get("sid", ""))

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def _dispatch(self):
        path, params, files = self._params()
        with self.box.lock:
            self.box.requests.append((self.command, path))
        route = {
            "/login_sid.lua": self.login,
            "/jason_boxinfo.xml": self.boxinfo,
            "/fon_devices/fax_send.lua": self.fax_send,
            "/cgi-bin/firmwarecfg": self.firmwarecfg,
            "/twofactor.lua": self.twofactor,
            "/query.lua": self.query_lua,
            "/fon_devices/edit_fax_intern.lua": self.edit_fax,
            "/data.lua": self.data_lua,
            "/cgi-bin/luacgi_notimeout": self.download,
            "/fon_num/foncalls_list.lua": self.call_list,
        }.get(path)
        if route is None:
            self._send(404, "not found")
            return
        route(params, files)

    # --- endpoints ---------------------------------------------------------------
    def login(self, p, _files):
        box = self.box
        if "logout" in p:
            box.sids.pop(p.get("sid", ""), None)
        sid = ZERO_SID
        if "username" in p and "response" in p:
            expected = (pbkdf2_response if box.login_version == 2 else md5_response)(
                self.server.challenge, box.password)  # type: ignore[attr-defined]
            if box.block_time == 0 and p["username"] == box.username and p["response"] == expected:
                sid = box.new_sid()
            else:
                box.block_time = max(box.block_time, 1)
        elif p.get("sid") in box.sids:
            sid = p["sid"]
        if box.login_version == 2:
            salt1, salt2 = secrets.token_hex(16), secrets.token_hex(16)
            challenge = f"2${box.iterations[0]}${salt1}${box.iterations[1]}${salt2}"
        else:
            challenge = secrets.token_hex(4)
        self.server.challenge = challenge  # type: ignore[attr-defined]
        rights = ("<Rights><Name>Dial</Name><Access>2</Access><Name>BoxAdmin</Name>"
                  "<Access>2</Access></Rights>" if sid != ZERO_SID else "<Rights></Rights>")
        xml = (f'<?xml version="1.0" encoding="utf-8"?><SessionInfo><SID>{sid}</SID>'
               f"<Challenge>{challenge}</Challenge><BlockTime>{box.block_time}</BlockTime>{rights}"
               f'<Users><User last="1">{box.username}</User></Users></SessionInfo>')
        self._send(200, xml, "text/xml")

    def boxinfo(self, _p, _f):
        self._send(200, '<j:BoxInfo xmlns:j="http://jason.avm.de/updatecheck/"><j:Name>FRITZ!Box 7590'
                        "</j:Name><j:Version>154.08.25</j:Version></j:BoxInfo>", "text/xml")

    def fax_send(self, p, _f):
        sess = self._session(p)
        if sess is None:
            self._redirect()
            return
        if p.get("myXhr") and p.get("query") == "refresh":
            self._json(self._advance_status())
            return
        if "btn_send" in p:
            dest = p.get("dest_num", "")
            if not dest:
                self._send(200, (FIXTURES / "precheck_invalid.json").read_text(), "application/json")
                return
            if not re.fullmatch(r"[0-9*#+ ]+", dest):
                self._json({"data": {"valerror": {"ok": False, "tomark": ["dest_num"], "result": "outofrange",
                                                  "alert": "Die Faxnummer enthält ungültige Zeichen."},
                                     "btn_send": "valerror"}})
                return
            self._json({"data": {"needs2FA": self._needs_2fa(dest), "btn_send": "ok"}})
            return
        html = (FIXTURES / "fax_send.html").read_text()
        if not self.box.fax_configured:
            html = html.replace('var g_mode="fax_write"', 'var g_mode="fax_not_configured"')
        html = html.replace('var tfaNeeded = true', f'var tfaNeeded = {"false" if sess["tfa_active"] else "true"}')
        self._send(200, html)

    @staticmethod
    def _needs_2fa(dest: str) -> bool:
        return not dest.startswith("0") or dest.startswith("00")

    def _advance_status(self):
        box = self.box
        with box.lock:
            if not box.runs:
                return dict(box.status)
            run = box.runs[-1]
            run.polls += 1
            n = run.polls
            sc = run.scenario
            if sc == "never_starts":
                return dict(box.status)
            if sc == "unavailable":
                box.status = {"status": -1, "status_text": "FAXSEND_UNAVAILABLE", "progress": 0, "reason": 0}
            elif n == 1:
                box.status = {"status": 2, "status_text": "FAXSEND_PREPARE", "progress": 0, "reason": 0}
            elif n == 2:
                box.status = {"status": 3, "status_text": "FAXSEND_CONNECTING", "progress": 0, "reason": 0}
            elif sc.startswith("slow:") and 3 <= n < 3 + int(sc.split(":")[1]):
                box.status = {"status": 4, "status_text": "FAXSEND_CONNECTED", "progress": 100, "reason": 0}
            elif sc == "stall" and n >= 3:
                box.status = {"status": 4, "status_text": "FAXSEND_CONNECTED", "progress": 50, "reason": 0}
            elif n in (3, 4, 5) and not sc.startswith("slow:"):
                box.status = {"status": 4, "status_text": "FAXSEND_CONNECTED", "progress": (n - 2) * 33,
                              "reason": 0}
            else:
                reason = int(sc.split(":")[-1], 16) if sc.startswith(("reason:", "slow:")) else 0
                box.status = {"status": 1, "status_text": "FAXSEND_READY", "progress": 100, "reason": reason}
                if box.journal_for_runs and run not in [j.get("_run") for j in box.journal]:
                    box.journal.append({"_run": run, "number": run.dest, "msn": run.src, "sent": 1,
                                        "date": time.strftime("%d.%m.%y %H:%M"), "date_time": int(time.time()) + len(box.journal),
                                        "pages": _pages(run.sff), "unread": 0, "name": "",
                                        **({"error": "Beim Faxversand ist ein Fehler aufgetreten."} if reason else {})})
            return dict(box.status)

    def firmwarecfg(self, p, _files):
        sess = self._session(p)
        sid = p.get("sid", "")
        if sess is None:
            self._redirect(302, "/index.lua")
            return
        dest, src, payload = p.get("NumDest", ""), p.get("NumSrc", ""), p.get("FaxUploadFile", "")

        def fail(status):
            self._send(200, FAILED_HTML.format(status=status, sid=sid))

        if self._needs_2fa(dest) and not sess["tfa_active"]:
            fail("Die Ausführung muss zusätzlich bestätigt werden.")
            return
        if not dest:
            fail("no called party number")
            return
        if not src:
            fail("no calling party number")
            return
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            fail("data is not Base64 encoded")
            return
        # measured on FRITZ!OS 8.25: a non-SFF payload is accepted, the box dials, and the
        # transmission then fails with reason 0x3319 (no "data is not sff" answer)
        scenario = self.box.scenario if data.startswith(b"Sfff") else "reason:3319"
        with self.box.lock:
            self.box.runs.append(FaxRun(data, dest, src, scenario))
            sess["tfa_active"] = False  # one confirmation covers one action
        self._send(200, OK_HTML.format(sid=sid))

    def twofactor(self, p, _f):
        sess = self._session(p)
        if sess is None:
            self._redirect()
            return
        box = self.box
        answer: dict = {}
        if "tfa_start" in p:
            answer["err"] = 0
            if box.tfa_start_error:
                answer["state"] = f"starterror;{box.tfa_start_error}"
            else:
                sess["tfa_state"] = f"{box.tfa_method};{box.tfa_code}"
                sess["tfa_polls"] = 0
                answer["state"] = sess["tfa_state"]
        if "tfa_active" in p:
            if sess["tfa_state"]:
                sess["tfa_polls"] += 1
                if box.tfa_auto_confirm_after is not None and sess["tfa_polls"] >= box.tfa_auto_confirm_after:
                    sess["tfa_state"] = ""
                    sess["tfa_active"] = True
            answer["done"] = sess["tfa_state"] == ""
            if answer["done"]:
                answer["active"] = sess["tfa_active"]
        if p.get("tfa_googleauth"):
            if p["tfa_googleauth"] == box.totp_code and sess["tfa_state"]:
                sess["tfa_state"], sess["tfa_active"] = "", True
                answer.update(err=0, msg="")
            else:
                answer.update(err=1, msg="wrong code")
        if "tfa_cancel" in p:
            sess["tfa_state"] = ""
            answer.update(err=0, msg="")
        self._json(answer)

    def query_lua(self, p, _f):
        if self._session(p) is None:
            self._redirect()
            return
        self._json({k: self.box.query(v) for k, v in p.items() if k != "sid"})

    def edit_fax(self, p, _f):
        sess = self._session(p)
        if sess is None:
            self._redirect()
            return
        box = self.box
        if "apply" in p:
            if box.config_save_needs_2fa and not sess["tfa_active"]:
                sess["tfa_state"] = f"{box.tfa_method};{box.tfa_code}"
                sess["tfa_polls"] = 0
                self._json({"data": {"apply": "twofactor", "twofactor": sess["tfa_state"]}})
                return
            err = self._validate_fax_form(p)
            if err:
                self._json({"data": {"apply": "valerror", "valerror": {"ok": False, "tomark": [err[0]],
                                                                       "result": err[1], "alert": err[2]}}})
                return
            self._do_save(p)
            sess["tfa_active"] = False
            self._json({"data": {"apply": "ok", "redirect": {"back": True}}})
            return
        self._send(200, self._render_fax_form())

    @staticmethod
    def _validate_fax_form(p):
        if len(p.get("headline", "")) > 20:
            return ("headline", "toolong", "Die Faxkennung ist zu lang.")
        if not (1 <= len(p.get("from_headline", "")) <= 40):
            return ("from_headline", "toolong", "Die Kopfzeile ist ungültig.")
        if len(p.get("from_name", "")) > 200:
            return ("from_name", "toolong", "Der Absender ist zu lang.")
        if p.get("fax_save") == "no" and "email_send" not in p:
            return ("email_send", "wrong", "Speicherort oder E-Mail wählen.")
        if p.get("faxsendoption") == "OnlySend" and "fax_switch" not in p and not any(
                k.startswith("num_") for k in p):
            return ("num_1", "wrong", "Rufnummer wählen.")
        return None

    def _do_save(self, p):
        cfg = self.box.config
        nums = [p[k] for k in sorted((k for k in p if k.startswith("num_")), key=lambda k: int(k[4:])) if p[k]]
        for i in range(10):
            cfg[f"FaxMSN{i}"] = nums[i] if i < len(nums) else ""
        cfg["FaxSwitch"] = "1" if "fax_switch" in p else "0"
        cfg["FaxSendOnly"] = "1" if p.get("faxsendoption") == "OnlySend" else "0"
        cfg["FaxKennung"] = p.get("headline", "")
        cfg["FaxSenderLong"] = p.get("from_name", "")
        cfg["FaxSenderShort"] = p.get("from_headline", "")
        email = "email_send" in p
        if email:
            cfg["FaxMailAddress0"] = p.get("fax_addr", "").replace(" ", "") if p.get("mail_type") == "custom" else ""
        save = p.get("fax_save")
        mode = {"no": "0", "intern": "4"}.get(save, "2" if save and save.startswith("usb") else "0")
        if email:
            mode = {"0": "1", "4": "5", "2": "3"}[mode]
        cfg["FaxMailActive"] = mode
        if mode in ("2", "3") and "fax_save_path" in p:
            cfg["FaxSavePath"] = p["fax_save_path"]

    def _render_fax_form(self) -> str:
        c = self.box.config
        mode = c.get("FaxMailActive", "0")
        email = mode in ("1", "3", "5")
        save = {"0": "no", "1": "no", "2": "usbOther", "3": "usbOther", "4": "intern", "5": "intern"}[mode]

        def checked(flag):
            return " checked" if flag else ""
        nums = [n for n in (c.get(f"FaxMSN{i}", "") for i in range(10)) if n]
        # every box number gets a checkbox; the fax numbers are checked, one extra is not
        num_inputs = "".join(
            f'<input type="checkbox" id="uiNum_{i}" name="num_{i}" value="{n}" checked>'
            for i, n in enumerate(nums, 1))
        num_inputs += f'<input type="checkbox" id="uiNum_{len(nums) + 1}" name="num_{len(nums) + 1}" value="99999999">'
        custom = c.get("FaxMailAddress0", "")
        return f"""<html><body><form name="main_form" method="POST">
<input type="radio" name="faxsendoption" value="SendAndReceive"{checked(c['FaxSendOnly'] == '0')}>
<input type="radio" name="faxsendoption" value="OnlySend"{checked(c['FaxSendOnly'] == '1')}>
<input type="checkbox" name="fax_switch" id="uiViewFaxSwitch"{checked(c['FaxSwitch'] == '1')}>
{num_inputs}
<input type="text" name="headline" id="uiViewHeadline" value="{c['FaxKennung']}">
<input type="text" name="from_headline" id="uiViewHeadline_from" value="{c['FaxSenderShort']}">
<textarea id="uiView_from_name" name="from_name">{c['FaxSenderLong']}</textarea>
<input type="checkbox" name="email_send" id="uiActivate"{checked(email)}>
<select id="uiMailType" name="mail_type"><option value="default"{' selected' if not custom else ''}>Standard</option>
<option value="custom"{' selected' if custom else ''}>Andere</option></select>
<input type="text" id="uiViewAddr" name="fax_addr" value="{custom}">
<input type="radio" name="fax_save" value="no"{checked(save == 'no')}>
<input type="radio" name="fax_save" value="intern"{checked(save == 'intern')}>
<input type="radio" name="fax_save" value="usbOther" disabled{checked(save == 'usbOther')}>
<input type="hidden" name="fax_save_path" id="faxSavePath" value="{c.get('FaxSavePath', '')}">
<input type="hidden" name="back_to_page" value="/fon_devices/fondevices_list.lua">
<input type="hidden" id="oldPageXhrSid" value="x">
</form></body></html>"""

    def data_lua(self, p, _f):
        sess = self._session(p)
        if sess is None:
            self._redirect()
            return
        if p.get("page") != "faxOverview":
            self._json({"data": {}})
            return
        box = self.box
        data: dict = {}
        if "delete" in p:
            before = len(box.journal)
            box.journal = [j for j in box.journal if str(j["date_time"]) != p.get("faxID")]
            data["delete"] = "ok" if len(box.journal) < before else "saveerror"
        if "markSeen" in p:
            for j in box.journal:
                if str(j["date_time"]) == p.get("faxID"):
                    j["unread"] = 0
            data["markSeen"] = "ok"
        sid = p["sid"]
        entries = []
        for j in box.journal:
            e = {k: v for k, v in j.items() if k != "_run"}
            e["path"] = "/cgi-bin/luacgi_notimeout?" + urlencode(
                {"sid": sid, "script": "/lua/photo.lua",
                 "myfaxfile": f"/var/InternerSpeicher/FRITZ/faxbox/Send_{j['date_time']}.pdf"})
            e.setdefault("recipient", e["number"])
            entries.append(e)
        data["faxList"] = {"hasFaxDevices": box.fax_configured, "faxList": entries}
        self._json({"pid": "faxOverview", "sid": sid, "data": data})

    def download(self, p, _f):
        if self._session(p) is None:
            self._redirect()
            return
        wanted = p.get("myfaxfile", "")
        entry = next((j for j in self.box.journal if f"_{j['date_time']}.pdf" in wanted), None)
        run = entry.get("_run") if entry else None
        if run is None:
            self._send(200, b"%PDF-1.4\n% fake fax\n%%EOF\n", "application/pdf")
            return
        if self.box.archive_mode == "missing":
            self._send(404, "not found")
            return
        self._send(200, archive_pdf(run.sff, tampered=self.box.archive_mode == "tampered"), "application/pdf")

    def call_list(self, p, _f):
        if self._session(p) is None:
            self._redirect()
            return
        if not self.box.call_list:
            self._send(403, "forbidden")
            return
        rows = ["sep=;", "Typ;Datum;Name;Rufnummer;Landes-/Ortsnetzbereich;Nebenstelle;Eigene Rufnummer;Dauer"]
        for j in reversed(self.box.journal):
            if j.get("_run"):
                rows.append(f"4;{j['date']};;{j['number']};;Fax (intern/PC);{j['msn']};0:01")
        self._send(200, "\r\n".join(rows) + "\r\n", "text/csv; charset=utf-8")


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # clients that hang up on purpose (TLS fingerprint mismatch, timeouts) are not errors
        if isinstance(sys.exc_info()[1], (ConnectionError, ssl.SSLError)):
            return
        super().handle_error(request, client_address)


class FakeBox:
    """Context manager running the fake box on 127.0.0.1 (optionally with TLS)."""

    def __init__(self, tls: bool = False, cert_dir: Path | None = None, **state):
        self.state = BoxState(**state)
        self.tls = tls
        self.cert_dir = cert_dir
        self.server: _Server | None = None

    def __enter__(self) -> "FakeBox":
        self.server = _Server(("127.0.0.1", 0), Handler)
        self.server.box = self.state  # type: ignore[attr-defined]
        self.server.challenge = ""  # type: ignore[attr-defined]
        if self.tls:
            cert, key = make_cert(self.cert_dir)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        assert self.server is not None
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address[:2]
        return f"{'https' if self.tls else 'http'}://{host}:{port}"


def make_cert(directory: Path | None) -> tuple[str, str]:
    directory = Path(directory or os.environ.get("TMPDIR", "/tmp"))
    cert, key = directory / "fakebox.crt", directory / "fakebox.key"
    if not cert.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                        "-out", str(cert), "-days", "2", "-subj", "/CN=fritz.box"],
                       check=True, capture_output=True)
    return str(cert), str(key)


def _pages(data: bytes) -> int:
    """Pages in the journal; a non-SFF upload (dialed anyway, see firmwarecfg) has none."""
    try:
        return sff.page_count(data)
    except sff.SffError:
        return 0


def archive_pdf(data: bytes, tampered: bool = False) -> bytes:
    """What the box keeps of a sent fax: the pages as PDF (595 x 840 pt for A4).

    The pages are decoded with libtiff (TIFF Compression=2), independent of fritzfax's own
    decoder, so a bug in fritzfax.sff cannot hide in a self-consistent round trip."""
    import io
    import struct

    from PIL import Image

    from fritzfax.core import sff

    images = []
    for rows in sff.page_rows(data):
        blob = b"".join(rows)
        entries = [(256, 4, 1, 1728), (257, 4, 1, len(rows)), (258, 3, 1, 1), (259, 3, 1, 2), (262, 3, 1, 0),
                   (266, 3, 1, 2), (273, 4, 1, 0), (277, 3, 1, 1), (278, 4, 1, len(rows)), (279, 4, 1, len(blob))]
        offset = 8 + 2 + len(entries) * 12 + 4
        out = io.BytesIO()
        out.write(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(entries)))
        for tag, typ, count, value in entries:
            value = offset if tag == 273 else value
            out.write(struct.pack("<HHIHH", tag, typ, count, value, 0) if typ == 3 else
                      struct.pack("<HHII", tag, typ, count, value))
        out.write(struct.pack("<I", 0) + blob)
        out.seek(0)
        img = Image.open(out)
        img.load()
        images.append(img.convert("1"))
    if tampered:
        images[0].paste(0, (800, 1000, 900, 1100))
    buf = io.BytesIO()
    # the box stores CCITT (lossless); Pillow does the same for mode "1"
    images[0].save(buf, "PDF", save_all=True, append_images=images[1:], dpi=(1728 / (215 / 25.4), 7.7 * 25.4))
    return buf.getvalue()
