import os
import pytest

from fritzfax.core import render
from fritzfax.core.render.office import find_soffice

needs_office = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")

RTF = r"{\rtf1\ansi{\fonttbl{\f0 Helvetica;}}\f0\fs24 Sehr geehrte Damen und Herren,\par Testfax.\par}"


@pytest.mark.office
@needs_office
def test_rtf_document_becomes_one_page(tmp_path):
    p = tmp_path / "brief.rtf"
    p.write_text(RTF)
    pages = render.render_document(render.Document(files=[p]))
    assert len(pages) == 1
    assert pages[0].histogram()[0] > 200  # some black text


@pytest.mark.office
@needs_office
def test_corrupt_docx_reports_file(tmp_path):
    p = tmp_path / "kaputt.docx"
    p.write_bytes(b"PK\x03\x04 not really a docx")
    with pytest.raises(render.RenderError, match="kaputt.docx"):
        render.render_document(render.Document(files=[p]))


def test_missing_libreoffice_is_a_clear_error(tmp_path):
    p = tmp_path / "brief.odt"
    p.write_bytes(b"x")
    with pytest.raises(render.RenderError, match="LibreOffice"):
        render.render_document(render.Document(files=[p], soffice=str(tmp_path / "no-soffice")))


def test_missing_office_is_its_own_error_type(tmp_path, monkeypatch):
    from fritzfax.core.render.office import OfficeUnavailable

    monkeypatch.setenv("PATH", str(tmp_path))  # no soffice anywhere
    monkeypatch.setattr("fritzfax.core.render.office.bundled_candidates", lambda: [])
    p = tmp_path / "brief.docx"
    p.write_bytes(b"x")
    with pytest.raises(OfficeUnavailable):
        render.render_document(render.Document(files=[p]))
    # PDF and images still work without LibreOffice
    from tests_helpers import one_page_pdf

    assert len(render.render_document(render.Document(files=[one_page_pdf(tmp_path / "a.pdf")]))) == 1


def test_bundled_libreoffice_is_found_next_to_the_program(tmp_path, monkeypatch):
    from fritzfax.core.render import office

    fake = tmp_path / "libreoffice" / "program" / "soffice"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(office, "bundled_candidates", lambda: [fake])
    assert office.find_soffice() == str(fake)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes / shell scripts")
def test_libreoffice_that_cannot_start_is_reported(tmp_path, monkeypatch):
    # e.g. the bundled LibreOffice on a system with a too old glibc
    from fritzfax.core.render import office

    broken = tmp_path / "soffice"
    broken.write_text("#!/bin/sh\necho 'soffice.bin: /lib/libc.so.6: version GLIBC_2.34 not found' >&2\nexit 1\n")
    broken.chmod(0o755)
    p = tmp_path / "brief.rtf"
    p.write_text("{\\rtf1 x}")
    with pytest.raises(office.OfficeUnavailable, match="GLIBC_2.34"):
        office.convert_to_pdf(p, tmp_path, soffice=str(broken))


def test_office_status_text():
    from fritzfax.core.render import office

    ok, detail = office.status(override="/nonexistent/soffice")
    assert ok is False and detail


def test_environment_for_system_libreoffice_restores_original_library_path(monkeypatch):
    from fritzfax.core.render import office

    monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    env = office.soffice_env("/usr/bin/soffice")
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    monkeypatch.setattr(office.sys, "frozen", True, raising=False)
    monkeypatch.setattr(office.sys, "_MEIPASS", "/frozen/_internal", raising=False)
    assert "LD_LIBRARY_PATH" not in office.soffice_env("/usr/bin/soffice")


def test_environment_for_bundled_libreoffice_adds_runtime_libs_and_fonts(tmp_path, monkeypatch):
    from fritzfax.core.render import office

    root = tmp_path / "libreoffice"
    (root / "program").mkdir(parents=True)
    (root / "fritzfax-runtime").mkdir()
    (root / "fritzfax-runtime" / "fonts.conf").write_text("<fontconfig/>")
    soffice = root / "program" / "soffice"
    soffice.write_text("#!/bin/sh\n")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr(office, "SYSTEM_FONTS_CONF", tmp_path / "missing-fonts.conf")
    env = office.soffice_env(str(soffice))
    assert env["LD_LIBRARY_PATH"].split(os.pathsep)[0] == str(root / "fritzfax-runtime")
    assert env["FONTCONFIG_FILE"] == str(root / "fritzfax-runtime" / "fonts.conf")
    # with a system fontconfig the system configuration is used
    sys_conf = tmp_path / "fonts.conf"
    sys_conf.write_text("<fontconfig/>")
    monkeypatch.setattr(office, "SYSTEM_FONTS_CONF", sys_conf)
    assert "FONTCONFIG_FILE" not in office.soffice_env(str(soffice))


def test_bundled_fontconfig_is_used_only_when_the_system_has_none(tmp_path, monkeypatch):
    # live 2026-10-26 (Arch, fontconfig 2.18): with the bundled fontconfig 2.14 always first on the
    # path, LibreOffice printed 270 "Fontconfig warning" lines and skipped the system's font rules
    from fritzfax import syslibs
    from fritzfax.core.render import office

    root = tmp_path / "libreoffice"
    (root / "program").mkdir(parents=True)
    (root / "fritzfax-runtime" / "fallback").mkdir(parents=True)
    (root / "fritzfax-runtime" / "fallback" / "libfontconfig.so.1").write_bytes(b"")
    soffice = root / "program" / "soffice"
    soffice.write_text("#!/bin/sh\n")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr(syslibs, "LOADED", [])
    path = office.soffice_env(str(soffice))["LD_LIBRARY_PATH"].split(os.pathsep)
    assert path == [str(root / "fritzfax-runtime")]
    monkeypatch.setattr(syslibs, "LOADED", ["libfontconfig.so.1"])  # the system has no fontconfig
    path = office.soffice_env(str(soffice))["LD_LIBRARY_PATH"].split(os.pathsep)
    assert path == [str(root / "fritzfax-runtime"), str(root / "fritzfax-runtime" / "fallback")]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes / shell scripts")
def test_a_conversion_without_output_never_returns_another_file_with_the_same_name(tmp_path):
    # brief.docx and brief.odt share the stem; the PDF of the first must not stand in for the second
    from fritzfax.core.render import office

    writes = tmp_path / "writes-pdf"
    writes.write_text('#!/bin/sh\nfor a; do out=$prev; prev=$a; done\n'
                      'while [ $# -gt 1 ]; do [ "$1" = --outdir ] && dir=$2; shift; done\n'
                      'echo "%PDF-1.4 first" > "$dir/brief.pdf"\n')
    silent = tmp_path / "silent"
    silent.write_text("#!/bin/sh\nexit 0\n")
    for f in (writes, silent):
        f.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "brief.docx").write_text("x")
    (tmp_path / "brief.odt").write_text("y")
    first = office.convert_to_pdf(tmp_path / "brief.docx", work, soffice=str(writes))
    assert first.read_text().startswith("%PDF-1.4 first")
    with pytest.raises(render.RenderError, match="brief.odt"):
        office.convert_to_pdf(tmp_path / "brief.odt", work, soffice=str(silent))
