"""Font lookup for text pages and the header line."""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ... import syslibs
from .. import paths

_CANDIDATES = {
    False: ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf", "Arial.ttf"],
    True: ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "Arial Bold.ttf"],
}

def _dirs() -> list[str]:
    return [str(d) for d in paths.font_dirs()]


@lru_cache(maxsize=8)
def find_font_file(bold: bool = False, override: str = "") -> str | None:
    if override and Path(override).is_file():
        return override
    if shutil.which("fc-match"):
        pattern = "DejaVu Sans:bold" if bold else "DejaVu Sans"
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True,
                                 timeout=5, env=syslibs.system_env()).stdout.strip()
            if out and Path(out).is_file() and out.lower().endswith((".ttf", ".otf")):
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    for name in _CANDIDATES[bold]:
        for base in _dirs():
            hits = list(Path(base).rglob(name)) if Path(base).is_dir() else []
            if hits:
                return str(hits[0])
    return None


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False, override: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font_file(bold, override)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)
