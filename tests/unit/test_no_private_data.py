"""Nothing personal or private in the repository (text and binary files). See tools/privacy_scan.py."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "tools" / "privacy_scan.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


def test_no_private_data_in_repository():
    assert ps.scan(ROOT, ps.read_words(ROOT / ".private-words")) == []


def test_scanner_finds_planted_data(tmp_path):
    # negative control; built from pieces so that this file itself stays clean
    (tmp_path / "x.bin").write_bytes(b"x geheim" + b"wort y\x00 jane.doe" + b"@corp.example 10.20" + b".30.40 /ho"
                                     + b"me/jane")
    found = {h[1] for h in ps.scan(tmp_path, ["GeheimWort"])}
    assert found == {"geheim" + "wort", "jane.doe" + "@corp.example", "10.20" + ".30.40", "/ho" + "me/j"}


def test_scanner_skips_the_word_list_itself(tmp_path):
    (tmp_path / ".private-words").write_text("secretword\n")
    assert ps.scan(tmp_path, ["secretword"]) == []


@pytest.mark.skipif(not (ROOT / ".private-words").exists(), reason="no local .private-words")
def test_private_words_list_is_used():
    assert ps.read_words(ROOT / ".private-words")


def test_box_lan_address_counts_as_private(tmp_path):
    # the program must not ship anyone's box address; built from pieces to keep this file clean
    (tmp_path / "x.py").write_text('URL = "https://192.168' + '.178.1"\n')
    assert ps.scan(tmp_path, []) != []


def test_build_folder_names_are_only_skipped_at_the_top(tmp_path):
    # out/, dist/, build/ at the top are generated; the same names deeper down are real content
    mail = "jane.doe" + "@corp.example"
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x.txt").write_text(mail)  # skipped: build output
    (tmp_path / "docs" / "build").mkdir(parents=True)
    (tmp_path / "docs" / "build" / "notes.txt").write_text(mail)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "out").write_text(mail)  # a file called "out"
    found = sorted(rel for rel, _ in ps.scan(tmp_path, []))
    assert found == ["docs/build/notes.txt", "pkg/out"]


def test_words_match_without_regard_to_case_also_beyond_ascii(tmp_path):
    (tmp_path / "x.txt").write_text("Herr M" + "ÜLLER schreibt", encoding="utf-8")
    assert [w for _, w in ps.scan(tmp_path, ["mül" + "ler"])] == ["mül" + "ler"]


@pytest.mark.parametrize("text", ["at 10.20" + ".30.40.", "(10.20" + ".30.40)", "10.20" + ".30.40, next"])
def test_private_address_at_the_end_of_a_sentence(tmp_path, text):
    (tmp_path / "x.txt").write_text(text)
    assert [w for _, w in ps.scan(tmp_path, [])] == ["10.20" + ".30.40"]


def test_version_numbers_are_no_addresses(tmp_path):
    (tmp_path / "x.txt").write_text("libfoo 10.20" + ".30.40.50 and 1.10" + ".20.30.40")  # built from pieces
    assert ps.scan(tmp_path, []) == []
