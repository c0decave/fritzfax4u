"""Fax journal (sent and received faxes) via data.lua page=faxOverview.

The REST endpoint /api/v0/faxjournal exists but is not in the box's access list
(rest_api/security.lua, FRITZ!OS 8.25) and answers "permission denied"; the web interface
uses data.lua instead, and so do we.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from .errors import ProtocolError
from .session import BoxClient


@dataclass
class JournalEntry:
    id: int            # date_time, the key used by delete/markSeen
    date: str
    number: str        # the other side
    own_number: str
    sent: bool
    pages: int
    error: str
    unread: bool
    name: str
    path: str          # download link (carries the sid valid when listed)


def _entries(data: dict) -> list[JournalEntry]:
    fax_list = (data.get("data") or {}).get("faxList") or {}
    out = []
    for f in fax_list.get("faxList") or []:
        sent = int(f.get("sent", 0)) == 1
        out.append(JournalEntry(
            id=int(f.get("date_time", 0)), date=f.get("date", ""),
            number=str(f.get("number", "")), own_number=str(f.get("msn", "")), sent=sent,
            pages=int(f.get("pages", 0) or 0), error=f.get("error", "") or "", unread=bool(int(f.get("unread", 0) or 0)),
            name=f.get("name", "") or "", path=f.get("path", "")))
    return out


def parse_journal(text: str) -> list[JournalEntry]:
    try:
        return _entries(json.loads(text))
    except ValueError as exc:
        raise ProtocolError("fax journal answer is not JSON") from exc


def _call(client: BoxClient, **extra) -> dict:
    return client.post("/data.lua", {"xhr": "1", "page": "faxOverview", **extra}).json()


def list_faxes(client: BoxClient) -> list[JournalEntry]:
    return _entries(_call(client))


def download(client: BoxClient, entry: JournalEntry) -> bytes:
    parts = urlsplit(entry.path)
    params = {k: v for k, v in parse_qsl(parts.query) if k != "sid"}
    resp = client.get(parts.path, params)
    if resp.status != 200 or not resp.body.startswith(b"%PDF"):
        raise ProtocolError(f"cannot download fax {entry.id} (HTTP {resp.status})")
    return resp.body


def mark_seen(client: BoxClient, entry: JournalEntry) -> None:
    _call(client, markSeen="", faxID=str(entry.id))


def delete(client: BoxClient, entry: JournalEntry) -> None:
    answer = _call(client, delete="", faxID=str(entry.id))
    if (answer.get("data") or {}).get("delete") not in (None, "ok"):
        raise ProtocolError(f"the box could not delete fax {entry.id}")
