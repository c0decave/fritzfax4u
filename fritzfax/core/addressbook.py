"""Address book: names and fax numbers, kept in SQLite (addressbook.db next to the send log).

A number is kept as it was written ("0531 / 490 591-13") and in the form the box dials it
("053149059113"); lookups (who is 053149059113? which entries match "4905"?) use the dialed form.
"""
from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .numbers import _SEPARATORS, InvalidNumber, normalize_number, parse_destinations

MAX_NAME = 200
MAX_NOTE = 1000


class InvalidContact(ValueError):
    """kind: "name" (empty or too long), "duplicate" (same name and number already there)."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    number: str  # as written
    dial: str  # as the box dials it
    note: str = ""


def default_path() -> Path:
    return paths.data_dir() / "addressbook.db"


class AddressBook:
    def __init__(self, db_path: Path | None = None, country_code: str = "49"):
        self.db_path = Path(db_path or default_path())
        self.country_code = country_code
        paths.private_dir(self.db_path.parent)  # who you send faxes to: only for the own user
        paths.private_file(self.db_path)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                       "number TEXT NOT NULL, dial TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
                       "created REAL, updated REAL)")
            db.execute("CREATE INDEX IF NOT EXISTS contacts_dial ON contacts (dial)")

    @contextlib.contextmanager
    def _db(self):
        """One connection per use: commit on success, roll back on error, always close."""
        con = sqlite3.connect(self.db_path)
        try:
            with con:
                yield con
        finally:
            con.close()

    # --- changing ---------------------------------------------------------------
    def _checked(self, name: str, number: str, note: str) -> tuple[str, str, str, str]:
        name = " ".join((name or "").split())
        if not name or len(name) > MAX_NAME:
            raise InvalidContact(f"a contact needs a name of 1 to {MAX_NAME} characters", "name")
        number = " ".join((number or "").split())
        if len(_SEPARATORS.split(number)) > 1:
            raise InvalidNumber(f"one number per entry: {number!r}", "invalid", number)
        dial = normalize_number(number, self.country_code)
        return name, number, dial, (note or "").strip()[:MAX_NOTE]

    def _check_duplicate(self, db, name: str, dial: str, own_id: int | None = None) -> None:
        for other_id, other_name in db.execute("SELECT id, name FROM contacts WHERE dial = ?", (dial,)):
            if other_id != own_id and other_name.casefold() == name.casefold():
                raise InvalidContact(f"{name} with this number is already in the address book", "duplicate")

    def add(self, name: str, number: str, note: str = "") -> Contact:
        name, number, dial, note = self._checked(name, number, note)
        now = time.time()
        with self._db() as db:
            self._check_duplicate(db, name, dial)
            cur = db.execute("INSERT INTO contacts (name, number, dial, note, created, updated) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (name, number, dial, note, now, now))
            return Contact(cur.lastrowid, name, number, dial, note)

    def update(self, contact_id: int, name: str, number: str, note: str = "") -> Contact:
        name, number, dial, note = self._checked(name, number, note)
        with self._db() as db:
            self._check_duplicate(db, name, dial, contact_id)
            cur = db.execute("UPDATE contacts SET name = ?, number = ?, dial = ?, note = ?, updated = ? WHERE id = ?",
                             (name, number, dial, note, time.time(), contact_id))
            if cur.rowcount == 0:
                raise KeyError(contact_id)
        return Contact(contact_id, name, number, dial, note)

    def delete(self, contact_id: int) -> None:
        with self._db() as db:
            if db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,)).rowcount == 0:
                raise KeyError(contact_id)

    # --- reading ------------------------------------------------------------------
    def _all(self) -> list[Contact]:
        with self._db() as db:
            rows = db.execute("SELECT id, name, number, dial, note FROM contacts").fetchall()
        return sorted((Contact(*r) for r in rows), key=lambda c: (c.name.casefold(), c.dial, c.id))

    def get(self, contact_id: int) -> Contact:
        for c in self._all():
            if c.id == contact_id:
                return c
        raise KeyError(contact_id)

    def contacts(self, query: str = "") -> list[Contact]:
        """All entries by name, or those whose name, note or number contains `query`."""
        query = query.strip()
        if not query:
            return self._all()
        text = query.casefold()
        digits = "".join(ch for ch in query if ch.isdigit())
        try:  # "+49 30 12" and "030 12" find the same entries
            dialed = normalize_number(query, self.country_code) if digits else ""
        except InvalidNumber:
            dialed = ""
        return [c for c in self._all()
                if text in c.name.casefold() or text in c.note.casefold() or text in c.number.casefold()
                or (dialed and dialed in c.dial)]

    def name_for(self, dial: str) -> str:
        """The name of the entry with this (dialed) number; "" if there is none."""
        try:
            dial = normalize_number(dial, self.country_code)
        except InvalidNumber:
            return ""
        for c in self._all():
            if c.dial == dial:
                return c.name
        return ""

    def resolve(self, text: str, country_code: str | None = None) -> list[str]:
        return resolve(text, country_code or self.country_code, self)


def resolve(text: str, country_code: str, book: AddressBook | None) -> list[str]:
    """Like numbers.parse_destinations, but an entry that is no number may be a name in the address book.

    A name has to match one entry exactly (case ignored); a name with several numbers is not guessed
    (InvalidNumber kind "ambiguous"), an unknown one stays the number error."""
    if book is None:
        return parse_destinations(text, country_code)
    by_name: dict[str, set[str]] = {}
    for c in book.contacts():
        by_name.setdefault(c.name.casefold(), set()).add(c.dial)
    result: list[str] = []
    for entry in _SEPARATORS.split(text or ""):
        if not entry.strip():
            continue
        try:
            number = normalize_number(entry, country_code)
        except InvalidNumber:
            found = by_name.get(" ".join(entry.split()).casefold())
            if not found:
                raise
            if len(found) > 1:
                raise InvalidNumber(f"{entry.strip()!r} has several numbers in the address book", "ambiguous",
                                    entry.strip()) from None
            number = next(iter(found))
        if number not in result:
            result.append(number)
    if not result:
        raise InvalidNumber("no destination number given", "empty")
    return result
