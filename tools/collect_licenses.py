#!/usr/bin/env python3
"""Copy the license texts of the bundled Python packages into a folder (Windows and macOS packages;
dist.sh does the same for Linux).

    python tools/collect_licenses.py OUT_DIR PACKAGE...   or   --all

A package without a license file, or one that is not installed, is an error: the package must not
ship without its license. Qt for Python publishes its wheels without license files, so for those the
texts in tools/package/licenses (LGPL-3.0, GPL-3.0 and a notice with the source location) are used.
"""
import shutil
import sys
from importlib import metadata
from pathlib import Path

LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
# Qt for Python: its wheels may carry no license files; these texts always go with it
SHIPPED = Path(__file__).resolve().parent / "package" / "licenses"
WITHOUT_FILES = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}


def _key(name: str) -> str:
    return name.lower().replace("_", "-")


def installed() -> list[str]:
    return sorted({d.metadata["Name"] for d in metadata.distributions() if d.metadata["Name"]})


def collect(out: Path, packages: list[str]) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in packages:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            raise SystemExit(f"{name}: not installed")
        tag = f"{dist.metadata['Name']}-{dist.metadata['Version']}"
        found = [f for f in (dist.files or [])
                 if any(Path(str(f)).name.upper().startswith(n) for n in LICENSE_NAMES) and ".dist-info" in str(f)]
        if _key(dist.metadata["Name"]) in WITHOUT_FILES:
            # the Linux wheels carry no license files, the Windows wheels some (CI 2026-10-26); the LGPL
            # texts and the notice with the source location go into every package
            for text in sorted(SHIPPED.iterdir()):
                if not (out / text.name).exists():
                    shutil.copyfile(text, out / text.name)
                    written.append(text.name)
            if not found:
                continue
        if not found:
            raise SystemExit(f"{tag} ({name}): no license file in its metadata")
        for f in found:
            target = out / f"{tag}-{Path(str(f)).name}"
            shutil.copyfile(f.locate(), target)
            written.append(target.name)
    return written


if __name__ == "__main__":
    wanted = installed() if sys.argv[2:] == ["--all"] else sys.argv[2:]
    names = collect(Path(sys.argv[1]), wanted)
    print(f"{len(names)} license files")
