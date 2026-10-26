"""Address book: names and fax numbers in SQLite (operator 2026-10-26)."""
import os
import sqlite3

import pytest

from fritzfax.core import addressbook
from fritzfax.core.addressbook import AddressBook, InvalidContact
from fritzfax.core.numbers import InvalidNumber


@pytest.fixture
def book(tmp_path):
    return AddressBook(tmp_path / "data" / "addressbook.db")


# --- happy path -----------------------------------------------------------------

def test_a_contact_is_kept_with_name_number_and_note(book, tmp_path):
    c = book.add("  Muster GmbH ", "0531 / 490 591-13", "Buchhaltung")
    assert (c.name, c.number, c.dial, c.note) == ("Muster GmbH", "0531 / 490 591-13", "053149059113", "Buchhaltung")
    again = AddressBook(tmp_path / "data" / "addressbook.db")  # the next start
    assert again.contacts() == [c]


def test_contacts_are_sorted_by_name_ignoring_case(book):
    book.add("zeta", "030 3")
    book.add("Alpha", "030 1")
    book.add("beta", "030 2")
    assert [c.name for c in book.contacts()] == ["Alpha", "beta", "zeta"]


def test_search_finds_name_note_and_number_in_any_spelling(book):
    a = book.add("Muster GmbH", "0531 49059113", "Buchhaltung")
    b = book.add("Amt", "+49 30 1234567")
    assert book.contacts("muster") == [a]
    assert book.contacts("BUCHHALT") == [a]
    assert book.contacts("4905") == [a]
    assert book.contacts("030 123") == [b]  # typed with a space, stored as +49 30 ...
    assert book.contacts("") == [b, a]


def test_update_and_delete(book):
    c = book.add("Amt", "030 1234567")
    u = book.update(c.id, "Amt Mitte", "030 7654321", "neu")
    assert book.get(c.id) == u and u.dial == "0307654321"
    book.delete(c.id)
    assert book.contacts() == []


def test_name_for_a_dialed_number(book):
    book.add("Muster GmbH", "+49 531 49059113")
    assert book.name_for("053149059113") == "Muster GmbH"
    assert book.name_for("0301") == ""
    assert book.name_for("not a number") == ""


def test_names_in_the_recipient_field_become_numbers(book):
    book.add("Muster GmbH", "0531 49059113")
    book.add("Amt", "030 1234567")
    assert book.resolve("muster gmbh, 040 999; Amt", "49") == ["053149059113", "040999", "0301234567"]


def test_resolve_without_address_book_is_parse_destinations():
    assert addressbook.resolve("030 1, 030 1, 040 2", "49", None) == ["0301", "0402"]


# --- sad path -----------------------------------------------------------------

@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_a_contact_needs_a_name(book, name):
    with pytest.raises(InvalidContact) as exc:
        book.add(name, "030 1")
    assert exc.value.kind == "name"


@pytest.mark.parametrize("number", ["", "abc", "030 1, 040 2", "1" * 41])
def test_a_contact_needs_one_valid_number(book, number):
    with pytest.raises(InvalidNumber):
        book.add("Amt", number)


def test_the_same_contact_is_not_added_twice(book):
    book.add("Amt", "030 1234567")
    with pytest.raises(InvalidContact) as exc:
        book.add("amt", "+49 30 1234567")
    assert exc.value.kind == "duplicate"
    book.add("Amt", "030 7654321")  # a second number of the same name is fine


def test_update_and_delete_of_a_missing_contact(book):
    with pytest.raises(KeyError):
        book.update(99, "x", "030 1")
    with pytest.raises(KeyError):
        book.delete(99)
    with pytest.raises(KeyError):
        book.get(99)


def test_an_unknown_name_in_the_recipient_field_stays_an_error(book):
    book.add("Amt", "030 1")
    with pytest.raises(InvalidNumber) as exc:
        book.resolve("Amt, Unbekannt", "49")
    assert exc.value.value == "Unbekannt"


def test_an_ambiguous_name_is_not_guessed(book):
    book.add("Amt", "030 1")
    book.add("Amt", "030 2")
    with pytest.raises(InvalidNumber) as exc:
        book.resolve("Amt", "49")
    assert exc.value.kind == "ambiguous" and exc.value.value == "Amt"


# --- edge cases -----------------------------------------------------------------

def test_names_with_quotes_and_unicode_are_stored_as_typed(book):
    c = book.add("O'Brien \"Fax\" Ünal 漢字; DROP TABLE contacts", "030 1")
    assert book.contacts()[0] == c and book.contacts("ünal") == [c]


@pytest.mark.skipif(os.name != "posix", reason="mode bits")
def test_the_address_book_is_private(book, tmp_path):
    book.add("Amt", "030 1")
    assert os.stat(tmp_path / "data").st_mode & 0o077 == 0
    assert os.stat(tmp_path / "data" / "addressbook.db").st_mode & 0o077 == 0


def test_the_database_is_plain_sqlite(book, tmp_path):
    book.add("Amt", "030 1")
    with sqlite3.connect(tmp_path / "data" / "addressbook.db") as db:
        assert db.execute("SELECT name, dial FROM contacts").fetchall() == [("Amt", "0301")]


def test_a_number_written_with_plus_and_own_country_is_found_as_dialed(book):
    c = book.add("Amt", "+49 (0)30 123")
    assert c.dial == "030123" and book.name_for("030123") == "Amt"
