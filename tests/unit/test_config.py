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
                        max_pages=12, recent_numbers=["030 1", "040 2"])
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
    with pytest.raises(config.NoKeyring):
        config.set_password(config.Settings(), "pw")


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
    config.set_password(s, "pw1")
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
