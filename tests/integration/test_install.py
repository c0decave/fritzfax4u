"""The package installs into a fresh virtualenv and both entry points work (offline)."""
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent


@pytest.mark.skipif(not shutil.which("pip") and not shutil.which("pip3"), reason="pip missing")
def test_install_and_run(tmp_path):
    env_dir = tmp_path / "venv"
    venv.create(env_dir, system_site_packages=True)  # PySide6/Pillow come from the image
    scripts = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
    exe = ".exe" if sys.platform == "win32" else ""
    py = scripts / f"python{exe}"
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "out", "dist", ".cache", "*.pyc", "__pycache__",
                                                               ".pytest_cache"))
    subprocess.run([py, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "--no-index", "-q", str(src)],
                   check=True, capture_output=True, text=True, cwd=tmp_path)
    run_env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run([scripts / f"fritzfax{exe}", "--version"], capture_output=True, text=True,
                         check=True, env=run_env, cwd=tmp_path).stdout
    assert out.strip().startswith("fritzfax ")
    code = ("import fritzfax.gui.i18n as i, fritzfax.gui.app; "
            "p = i.I18N_DIR / 'fritzfax_de.qm'; assert p.is_file(), p; "
            "import importlib.resources as r; assert (r.files('fritzfax') / 'data/fritzfax.desktop').is_file(); "
            "print(fritzfax.gui.app.__file__)")
    shown = subprocess.run([py, "-c", code], capture_output=True, text=True, check=True, env=run_env,
                           cwd=tmp_path).stdout
    assert str(env_dir) in shown  # the installed copy, not the source tree
