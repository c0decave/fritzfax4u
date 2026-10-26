"""install.sh / uninstall.sh of the release package, run against a stand-in program (fast, offline)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="install.sh/uninstall.sh are POSIX shell scripts (Linux packages)")

ROOT = Path(__file__).parent.parent.parent
PKG_FILES = ROOT / "tools" / "package"


@pytest.fixture
def pkg(tmp_path):
    """A package directory like dist.sh builds it, with a stand-in for the frozen program."""
    p = tmp_path / "pkg dir"
    (p / "fritzfax").mkdir(parents=True)
    for name in ("install.sh", "uninstall.sh", "fritzfax.desktop"):
        shutil.copy(PKG_FILES / name, p / name)
    (p / "VERSION").write_text("9.9.9\n")
    prog = p / "fritzfax" / "fritzfax"
    prog.write_text("#!/bin/sh\necho \"stand-in $*\"\nexit 0\n")
    for f in (prog, p / "install.sh", p / "uninstall.sh"):
        f.chmod(0o755)
    return p


def sh(script, *args, home, cwd=None, check=True):
    env = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(["sh", str(script), *args], capture_output=True, text=True, env=env, cwd=cwd)
    if check:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def exec_line(desktop: Path) -> str:
    return next(line for line in desktop.read_text().splitlines() if line.startswith("Exec="))


# --- happy path -----------------------------------------------------------------------------

def test_install_and_uninstall_in_the_home_directory(pkg, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = sh(pkg / "install.sh", home=home).stdout
    assert "fritzfax 9.9.9 installed" in out and "selftest passed" in out
    bin_ = home / ".local" / "bin"
    assert subprocess.run([bin_ / "fritzfax", "x"], capture_output=True, text=True).stdout.strip() == "stand-in x"
    assert (bin_ / "fritzfax-gui").resolve() == (home / ".local/opt/fritzfax/fritzfax/fritzfax").resolve()
    assert exec_line(home / ".local/share/applications/fritzfax.desktop") == \
        f'Exec="{bin_}/fritzfax-gui" %F'
    sh(pkg / "uninstall.sh", home=home)
    assert not (bin_ / "fritzfax").exists() and not (bin_ / "fritzfax").is_symlink()
    assert not (home / ".local/opt/fritzfax").exists()
    assert not (home / ".local/share/applications/fritzfax.desktop").exists()


def test_reinstall_replaces_an_older_install(pkg, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sh(pkg / "install.sh", home=home)
    (pkg / "VERSION").write_text("9.9.10\n")
    assert "fritzfax 9.9.10 installed" in sh(pkg / "install.sh", home=home).stdout


# --- prefixes ---------------------------------------------------------------------------------

def test_relative_prefix_gives_working_commands(pkg, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sh(pkg / "install.sh", "--prefix", "rel", home=home, cwd=tmp_path)
    cmd = tmp_path / "rel" / "bin" / "fritzfax"
    assert cmd.exists(), "dangling symlink"
    assert subprocess.run([cmd, "y"], capture_output=True, text=True, cwd="/").stdout.strip() == "stand-in y"
    sh(pkg / "uninstall.sh", "--prefix", "rel", home=home, cwd=tmp_path)
    assert not cmd.is_symlink()


@pytest.mark.parametrize("name", ["my prefix", "a&b", 'q"uote$x', "back\\slash", "100%d sale"])
def test_prefix_with_special_characters(pkg, tmp_path, name):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / name
    sh(pkg / "install.sh", "--prefix", str(prefix), home=home)
    assert (prefix / "bin" / "fritzfax").exists()
    line = exec_line(prefix / "share/applications/fritzfax.desktop")
    # Desktop Entry spec: quoted argument; inside it \ " ` $ are escaped with a backslash, and the
    # backslash itself is escaped once more because Exec is a string value
    quoted = str(prefix / "bin" / "fritzfax-gui")
    for ch in ("\\", '"', "`", "$"):
        quoted = quoted.replace(ch, "\\" + ch)
    quoted = quoted.replace("\\", "\\\\").replace("%", "%%")  # a literal % is written %%
    assert line == 'Exec="' + quoted + '" %F'


# --- sad path ------------------------------------------------------------------------------------

def test_prefix_option_without_a_value(pkg, tmp_path):
    proc = sh(pkg / "install.sh", "--prefix", home=tmp_path, check=False)
    assert proc.returncode == 2 and "usage" in proc.stderr


def test_unknown_option(pkg, tmp_path):
    proc = sh(pkg / "install.sh", "--frobnicate", home=tmp_path, check=False)
    assert proc.returncode == 2 and "usage" in proc.stderr


def test_a_foreign_fritzfax_command_is_not_overwritten(pkg, tmp_path):
    # e.g. ~/.local/bin/fritzfax-gui from "pip install --user"
    home = tmp_path / "home"
    bin_ = home / ".local" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "fritzfax-gui").write_text("#!/bin/sh\necho pip version\n")
    proc = sh(pkg / "install.sh", home=home, check=False)
    assert proc.returncode == 1 and "fritzfax-gui" in proc.stderr
    assert (bin_ / "fritzfax-gui").read_text() == "#!/bin/sh\necho pip version\n"


def test_uninstall_leaves_foreign_commands_alone(pkg, tmp_path):
    home = tmp_path / "home"
    bin_ = home / ".local" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "fritzfax").write_text("pip version\n")
    sh(pkg / "uninstall.sh", home=home)
    assert (bin_ / "fritzfax").read_text() == "pip version\n"


# --- edge cases -------------------------------------------------------------------------------------

def test_selftest_failure_is_reported_but_install_succeeds(pkg, tmp_path):
    (pkg / "fritzfax" / "fritzfax").write_text('#!/bin/sh\n[ "$1" = selftest ] && exit 1\nexit 0\n')
    home = tmp_path / "home"
    home.mkdir()
    out = sh(pkg / "install.sh", home=home).stdout
    assert "selftest reported a problem" in out


def test_install_from_the_installed_copy_keeps_it(pkg, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sh(pkg / "install.sh", home=home)
    installed = home / ".local" / "opt" / "fritzfax"
    sh(installed / "install.sh", home=home)
    assert (installed / "fritzfax" / "fritzfax").exists()


def test_release_scripts_do_not_hide_exit_codes_in_pipes():
    # /bin/sh has no pipefail: "selftest | tee" or "tar | xz" would report only the last command
    import re

    for script in (ROOT / "dist.sh", ROOT / "tools" / "dist_test.sh"):
        text = script.read_text()
        assert not re.search(r"selftest[^\n|]*\|(?!\|)", text), script.name  # "||" is fine, "|" is not
        assert "| xz" not in text, script.name


# --- dist.sh keeps newer packages -----------------------------------------------------------------------

@pytest.mark.parametrize("built, present, kept", [
    ("0.1.11", ["0.1.10", "0.1.11"], ["0.1.11"]),
    ("0.1.9", ["0.1.9", "0.1.10"], ["0.1.10", "0.1.9"]),     # building an older commit keeps the newer one
    ("0.2.0", ["0.1.99", "0.2.0", "0.10.0"], ["0.10.0", "0.2.0"]),  # version order, not text order
])
def test_prune_dist_removes_only_older_versions(tmp_path, built, present, kept):
    dist = tmp_path / "dist"
    (dist / "tmp").mkdir(parents=True)  # somebody else's folder: never touched
    for v in present:
        for suffix in ("linux-x86_64.tar.xz", "linux-x86_64.tar.xz.sha256", "source.tar.gz"):
            (dist / f"fritzfax-{v}-{suffix}").write_text(v)
    subprocess.run(["sh", str(ROOT / "tools" / "prune_dist.sh"), built, str(dist)], check=True)
    versions = sorted({p.name.split("-")[1] for p in dist.glob("fritzfax-*")})
    assert versions == sorted(kept) and (dist / "tmp").is_dir()


def test_dist_uses_prune_dist_and_no_archive_pipe():
    text = (ROOT / "dist.sh").read_text()
    assert "tools/prune_dist.sh" in text and "git archive HEAD | tar" not in text
