"""HTTP session with a FRITZ!Box: login (PBKDF2 / MD5), SID handling, TLS pinning.

The box serves a self-signed certificate. Instead of switching verification off we pin
the certificate's SHA-256 fingerprint (trust on first use, confirmed by the user).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from .errors import (
    BoxError,
    BoxNotConfigured,
    CertificateMismatch,
    ConnectionFailed,
    InvalidBoxAddress,
    LoginBlocked,
    LoginFailed,
    ProtocolError,
    SessionExpired,
    UntrustedCertificate,
)

ZERO_SID = "0" * 16


def challenge_response(challenge: str, password: str) -> str:
    """Answer a login_sid.lua challenge (version 2 PBKDF2 or legacy MD5)."""
    if challenge.startswith("2$"):
        try:
            _, it1, salt1, it2, salt2 = challenge.split("$")
            h1 = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt1), int(it1))
            h2 = hashlib.pbkdf2_hmac("sha256", h1, bytes.fromhex(salt2), int(it2))
        except ValueError as exc:
            raise ProtocolError(f"malformed login challenge: {challenge!r}") from exc
        return f"{salt2}${h2.hex()}"
    text = "".join(c if ord(c) <= 255 else "." for c in f"{challenge}-{password}")
    return f"{challenge}-" + hashlib.md5(text.encode("utf-16le")).hexdigest()


def format_fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise ProtocolError(f"expected JSON, got: {self.text[:200]!r}") from exc

    @property
    def is_login_redirect(self) -> bool:
        return self.status in (301, 302, 303) and self.headers.get("location", "") in ("/", "/index.lua")


class BoxClient:
    """One logged-in user session with one box."""

    def __init__(self, url: str, username: str = "", password: str = "", *, cert_sha256: str | None = None,
                 trust_unknown_cert: bool = False, timeout: float = 20.0):
        self.base_url = self._normalize_url(url)
        parts = urlsplit(self.base_url)
        self._scheme = parts.scheme
        self._host = parts.hostname or ""
        self._port = parts.port
        self.username = username
        self._password = password
        self.cert_sha256 = cert_sha256.upper() if cert_sha256 else None
        self.trust_unknown_cert = trust_unknown_cert
        self.timeout = timeout
        self.sid: str | None = None
        self.rights: dict[str, int] = {}
        self.peer_fingerprint: str | None = None

    def __repr__(self) -> str:
        return f"BoxClient({self.base_url!r}, username={self.username!r})"

    @staticmethod
    def _normalize_url(url: str) -> str:
        given = (url or "").strip()
        if not given:
            raise BoxNotConfigured()
        url = given if "://" in given else "https://" + given
        parts = urlsplit(url)
        try:
            parts.port  # noqa: B018 - raises ValueError for "fritz.box:abc" or a port > 65535
        except ValueError:
            raise InvalidBoxAddress(given) from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise InvalidBoxAddress(given)
        return f"{parts.scheme}://{parts.netloc}"

    # --- transport ---------------------------------------------------------------
    def _connection(self) -> http.client.HTTPConnection:
        if self._scheme == "http":
            return http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # replaced by the fingerprint pin below
        return http.client.HTTPSConnection(self._host, self._port, timeout=self.timeout, context=ctx)

    def _check_certificate(self, conn: http.client.HTTPConnection) -> None:
        if self._scheme != "https":
            return
        der = conn.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
        actual = format_fingerprint(der)
        self.peer_fingerprint = actual
        if self.cert_sha256:
            if actual != self.cert_sha256:
                raise CertificateMismatch(self.cert_sha256, actual)
        elif not self.trust_unknown_cert:
            raise UntrustedCertificate(actual)

    def raw_request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None,
                    multipart: dict | None = None) -> Response:
        """Send one request without any session handling."""
        if params:
            path += ("&" if "?" in path else "?") + urlencode(params)
        headers = {"Accept": "*/*", "Connection": "close"}
        body = None
        if multipart is not None:
            body, ctype = encode_multipart(multipart)
            headers["Content-Type"] = ctype
        elif data is not None:
            body = urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn = self._connection()
        try:
            conn.connect()
            self._check_certificate(conn)
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload)
        except (BoxError, ProtocolError):
            raise
        except (OSError, http.client.HTTPException, socket.timeout) as exc:
            raise ConnectionFailed(f"cannot reach {self.base_url}: {exc}") from exc
        finally:
            conn.close()

    # --- login -------------------------------------------------------------------
    def _session_info(self, resp: Response) -> ET.Element:
        if resp.status != 200:
            raise ProtocolError(f"login_sid.lua answered HTTP {resp.status}")
        try:
            return ET.fromstring(resp.body)
        except ET.ParseError as exc:
            raise ProtocolError("login_sid.lua did not return XML") from exc

    def login(self) -> str:
        info = self._session_info(self.raw_request("GET", "/login_sid.lua", params={"version": "2"}))
        block = int(info.findtext("BlockTime") or 0)
        if block > 0:
            raise LoginBlocked(block)
        if not self.username:
            last = [u.text for u in info.iter("User") if u.get("last") == "1"]
            self.username = (last or [u.text for u in info.iter("User")] or [""])[0] or ""
        challenge = info.findtext("Challenge") or ""
        response = challenge_response(challenge, self._password)
        info = self._session_info(self.raw_request(
            "POST", "/login_sid.lua", params={"version": "2"},
            data={"username": self.username, "response": response}))
        sid = info.findtext("SID") or ZERO_SID
        if sid == ZERO_SID:
            self.sid = None
            raise LoginFailed(block_time=int(info.findtext("BlockTime") or 0))
        rights = info.find("Rights")
        names = [e.text or "" for e in rights.iter("Name")] if rights is not None else []
        access = [int(e.text or 0) for e in rights.iter("Access")] if rights is not None else []
        self.rights = dict(zip(names, access))
        self.sid = sid
        return sid

    def logout(self) -> None:
        if self.sid:
            try:
                self.raw_request("GET", "/login_sid.lua", params={"version": "2", "logout": "1", "sid": self.sid})
            except BoxError:
                pass
        self.sid = None

    def ensure_login(self) -> str:
        return self.sid or self.login()

    # --- requests with session -----------------------------------------------------
    def _with_session(self, send) -> Response:
        self.ensure_login()
        resp = send(self.sid)
        if resp.is_login_redirect:
            self.sid = None
            self.login()
            resp = send(self.sid)
            if resp.is_login_redirect:
                raise SessionExpired("the box keeps rejecting the session")
        return resp

    def get(self, path: str, params: dict | None = None) -> Response:
        return self._with_session(lambda sid: self.raw_request("GET", path, params={"sid": sid, **(params or {})}))

    def post(self, path: str, data: dict | None = None) -> Response:
        return self._with_session(lambda sid: self.raw_request("POST", path, data={"sid": sid, **(data or {})}))

    def post_multipart(self, path: str, fields: dict) -> Response:
        return self._with_session(lambda sid: self.raw_request("POST", path, multipart={"sid": sid, **fields}))


def encode_multipart(fields: dict) -> tuple[bytes, str]:
    """Encode text fields as multipart/form-data (the box reads FaxUploadFile as text)."""
    boundary = "----fritzfax" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        if isinstance(value, str):
            value = value.encode()
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n").encode()
        out += value + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
