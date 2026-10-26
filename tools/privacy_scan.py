#!/usr/bin/env python3
"""Scan a directory for personal or private data before anything is published.

    python3 tools/privacy_scan.py DIR [--words FILE]      exit 1 and list hits, 0 if clean

Words come from FILE (default: .private-words of this repository, git-ignored; one per line,
# for comments) and match without regard to case, also beyond ASCII; generic patterns catch
e-mail addresses, private network addresses (any, including the AVM factory default; only
loopback is allowed), home paths and private keys. Binary files are scanned too; text rendered
into images cannot be found this way.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_ANYWHERE = {".git", "__pycache__", ".pytest_cache"}
SKIP_TOP = {"out", "dist", "build", ".venv", ".cache"}  # generated folders at the top of the tree only
SKIP_FILES = {".private-words"}
ALLOWED_EMAILS = {"user@example.org", "a@b.de", "fax@example.org", "noreply@anthropic.com"}
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0"}
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
IPV4 = re.compile(rb"(?<![\d.])(10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?!\d|\.\d)")  # a full stop after it ends a sentence
OTHER = [re.compile(rb"/(?:home|mnt|Users)/[A-Za-z]"), re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY")]


def read_words(path: Path) -> list[str]:
    if not path.exists():
        return []
    words = [w.strip() for w in path.read_text(encoding="utf-8").replace("\r", "").splitlines()]
    return [w for w in words if w and not w.startswith("#")]


def files(root: Path):
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if not path.is_file() or path.name in SKIP_FILES or set(parts[:-1]) & SKIP_ANYWHERE:
            continue
        if len(parts) > 1 and parts[0] in SKIP_TOP:
            continue
        yield path


def scan(root: Path, words: list[str]) -> list[tuple[str, str]]:
    hits = []
    folded = [w.casefold() for w in words]
    for path in files(root):
        data = path.read_bytes()
        text = data.decode("utf-8", "replace").casefold()
        rel = path.relative_to(root).as_posix()  # the same report on every system
        hits += [(rel, w) for w in folded if w in text]
        hits += [(rel, m.group(0).decode()) for m in EMAIL.finditer(data) if m.group(0).decode() not in ALLOWED_EMAILS]
        hits += [(rel, m.group(0).decode()) for m in IPV4.finditer(data) if m.group(0).decode() not in ALLOWED_IPS]
        hits += [(rel, m.group(0).decode(errors="replace")) for p in OTHER for m in p.finditer(data)]
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--words", type=Path, default=Path(__file__).resolve().parent.parent / ".private-words")
    a = ap.parse_args()
    hits = scan(a.dir, read_words(a.words))
    for rel, what in sorted(set(hits)):
        print(f"private data: {rel}: {what}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
