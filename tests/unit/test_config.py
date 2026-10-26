import os
import sys

import pytest

from fritzfax.core import config


def test_defaults_without_file(tmp_path):
    s = config.load(tmp_path / "none.conf")
    assert s.box_url == ""  # no address built in: the user enters their own box
    assert s.theme == "system"
    assert s.header_mode == "overlay" and s.dither == "auto" and s.cover is False
    assert s.default_mode == "document"
    assert s.language == "de"  # German unless chosen otherwise (operator 2026-10-26)


def test_round_trip(tmp_path):
    path = tmp_path / "f.conf"
    s = config.Settings(box_url="https://fritz.box", username="u", cert_sha256="AA:BB", sender_name="Ich",
                        sender_number="123", header_mode="shrink", dither="dither", cover=True, language="de", theme="dark",
                        max_pages=12, recent_numbers=["030 1", "040 2"], shown_number="+49 40 999999")
    config.save(s, path)
    assert config.load(path) == s


def test_password_is_never_written(tmp_path):
    path = tmp_path / "f.conf"
    s = config.Settings()
    s.password = "geheim"  # attribute exists only in memory
    config.save(s, path)
    assert "geheim" not in path.read_text()
    if os.name == "posix":  # Windows has no group/other mode bits
        assert os.stat(path).st_mode & 0o077 == 0


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="XDG is the Linux layout (tests/unit/test_paths.py)")
def test_default_path_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert config.default_path() == tmp_path / "x" / "fritzfax" / "fritzfax.conf"


def test_invalid_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "f.conf"
    path.write_text("[send]\nheader_mode = sideways\nmax_pages = many\ndither = dither\n[ui]\nlanguage = fr\n")
    s = config.load(path)
    assert s.header_mode == "overlay" and s.max_pages == 50 and s.dither == "dither" and s.language == "de"


def test_broken_file_is_not_fatal(tmp_path):
    path = tmp_path / "f.conf"
    path.write_text("this is not ini\n[[[")
    assert config.load(path) == config.Settings()


def test_recent_numbers_are_capped_and_deduplicated():
    s = config.Settings()
    for n in range(15):
        s.remember_number(f"030{n}")
    s.remember_number("0305")
    assert len(s.recent_numbers) == 10 and s.recent_numbers[0] == "0305"


def test_password_from_environment(monkeypatch):
    monkeypatch.setenv("FRITZFAX_PASSWORD", "envpw")
    assert config.get_password(config.Settings()) == "envpw"


def test_password_without_keyring(monkeypatch):
    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: None)
    assert config.get_password(config.Settings()) is None


# --- the password in the settings file when there is no keyring (operator 2026-10-26) ----------------------

BOX = dict(box_url="https://fritz.box", username="u")


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: None)


def test_without_keyring_the_password_goes_into_the_settings_file(tmp_path, no_keyring):
    path = tmp_path / "f.conf"
    s = config.Settings(**BOX)
    assert config.set_password(s, "geheim-1") == "file"
    config.save(s, path)
    assert "geheim-1" not in path.read_text()  # not in plain text (base64: no encryption, see the docs)
    if os.name == "posix":
        assert os.stat(path).st_mode & 0o077 == 0
    assert config.get_password(config.load(path)) == "geheim-1"  # the next start needs no prompt


def test_a_refusing_keyring_falls_back_to_the_settings_file(tmp_path, monkeypatch):
    class Locked:
        @staticmethod
        def get_password(service, user):
            raise RuntimeError("the wallet stayed locked")

        @staticmethod
        def set_password(service, user, pw):
            raise RuntimeError("the wallet stayed locked")

    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: Locked)
    path = tmp_path / "f.conf"
    s = config.Settings(**BOX)
    assert config.set_password(s, "pw") == "file"
    config.save(s, path)
    assert config.get_password(config.load(path)) == "pw"


def test_a_working_keyring_takes_the_password_out_of_the_file(tmp_path, monkeypatch):
    store = {}

    class Kr:
        @staticmethod
        def get_password(service, user):
            return store.get(user)

        @staticmethod
        def set_password(service, user, pw):
            store[user] = pw

    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    path = tmp_path / "f.conf"
    s = config.Settings(**BOX)
    monkeypatch.setattr(config, "_keyring", lambda: None)
    config.set_password(s, "old")
    config.save(s, path)
    monkeypatch.setattr(config, "_keyring", lambda: Kr)
    s = config.load(path)
    assert config.set_password(s, "new") == "keyring"
    config.save(s, path)
    assert "password" not in path.read_text()
    assert config.get_password(config.load(path)) == "new"


def test_delete_password_removes_it_from_the_file(tmp_path, no_keyring):
    path = tmp_path / "f.conf"
    s = config.Settings(**BOX)
    config.set_password(s, "pw")
    config.delete_password(s)
    config.save(s, path)
    assert config.get_password(config.load(path)) is None


def test_the_file_password_belongs_to_one_box_and_user(tmp_path, no_keyring):
    path = tmp_path / "f.conf"
    s = config.Settings(**BOX)
    config.set_password(s, "alices")
    config.save(s, path)
    other_user = config.load(path)
    other_user.username = "bob"
    assert config.get_password(other_user) is None
    other_box = config.load(path)
    other_box.box_url = "https://192.0.2.7"
    assert config.get_password(other_box) is None


def test_environment_and_session_password_come_before_the_file(tmp_path, no_keyring, monkeypatch):
    s = config.Settings(**BOX)
    config.set_password(s, "from-file")
    monkeypatch.setenv("FRITZFAX_PASSWORD", "from-env")
    assert config.get_password(s) == "from-env"
    s.password = "typed"
    assert config.get_password(s) == "typed"


@pytest.mark.parametrize("pw", ["äöü€ 漢字", "a=b%c;d#e", " leading and trailing ", "x" * 500])
def test_any_password_survives_the_round_trip(tmp_path, no_keyring, pw):
    path = tmp_path / "f.conf"
    s = config.Settings(**BOX)
    config.set_password(s, pw)
    config.save(s, path)
    assert config.get_password(config.load(path)) == pw


def test_an_empty_password_is_not_stored(no_keyring):
    with pytest.raises(ValueError):
        config.set_password(config.Settings(**BOX), "")


@pytest.mark.parametrize("raw", ["!!!not base64!!!", "/w==", ""])
def test_an_unreadable_file_password_is_ignored(tmp_path, no_keyring, raw):
    path = tmp_path / "f.conf"
    path.write_text(f"[box]\nurl = https://fritz.box\nusername = u\npassword_for = u@https://fritz.box\n"
                    f"password_b64 = {raw}\n")
    assert config.get_password(config.load(path)) is None  # "/w==" is the byte 0xff: not UTF-8


@pytest.mark.skipif(os.name != "posix", reason="mode bits")
def test_a_stale_temporary_file_does_not_widen_the_mode(tmp_path, no_keyring):
    path = tmp_path / "f.conf"
    stale = path.with_suffix(".tmp")
    stale.write_text("old")
    os.chmod(stale, 0o644)
    s = config.Settings(**BOX)
    config.set_password(s, "pw")
    config.save(s, path)
    assert os.stat(path).st_mode & 0o077 == 0


@pytest.mark.skipif(os.name != "posix", reason="mode bits")
def test_the_backup_of_a_broken_file_is_private_too(tmp_path, no_keyring):
    path = tmp_path / "f.conf"
    path.write_text("[box]\npassword_b64 = c2VjcmV0\n[[[broken")
    os.chmod(path, 0o600)
    s = config.load(path)
    assert s.load_backup
    assert os.stat(s.load_backup).st_mode & 0o077 == 0


def test_password_with_keyring(monkeypatch):
    store = {}

    class FakeKeyring:
        @staticmethod
        def get_password(service, user):
            return store.get((service, user))

        @staticmethod
        def set_password(service, user, pw):
            store[(service, user)] = pw

        @staticmethod
        def delete_password(service, user):
            store.pop((service, user), None)

    monkeypatch.delenv("FRITZFAX_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: FakeKeyring)
    s = config.Settings(box_url="https://fritz.box", username="u")
    assert config.set_password(s, "pw1") == "keyring"
    assert config.get_password(s) == "pw1"
    assert ("fritzfax", "u@https://fritz.box") in store


def test_unknown_theme_keeps_the_default(tmp_path):
    path = tmp_path / "f.conf"
    path.write_text("[ui]\ntheme = neon\n", encoding="utf-8")
    assert config.load(path).theme == "system"


# --- a broken settings file (review round 2) ------------------------------------------------------------

def test_broken_file_is_kept_as_a_backup_and_reported(tmp_path):
    conf = tmp_path / "fritzfax.conf"
    conf.write_text("[box]\nurl = https://fritz.box\ncert_sha256 = AA:BB\n[sender]\nname = A\nname = B\n")
    s = config.load(conf)
    assert s.load_problem and "fritzfax.conf" in s.load_problem
    backup = tmp_path / "fritzfax.conf.broken"
    assert backup.read_text() == conf.read_text()
    config.save(s, conf)  # a later save must not lose the old contents
    assert "https://fritz.box" in backup.read_text()


def test_good_file_has_no_load_problem(tmp_path):
    conf = tmp_path / "f.conf"
    config.save(config.Settings(box_url="fritz.box"), conf)
    assert config.load(conf).load_problem == ""
    assert not (tmp_path / "f.conf.broken").exists()


def test_missing_file_is_no_problem(tmp_path):
    assert config.load(tmp_path / "nope.conf").load_problem == ""


def test_a_chosen_language_is_kept(tmp_path):
    for lang in ("en", "auto", "de"):
        path = tmp_path / f"{lang}.conf"
        path.write_text(f"[ui]\nlanguage = {lang}\n")
        assert config.load(path).language == lang
