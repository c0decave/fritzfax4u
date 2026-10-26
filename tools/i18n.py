#!/usr/bin/env python3
"""Maintain the German translation of the GUI.

    python tools/i18n.py extract     list every translatable string (context, source)
    python tools/i18n.py missing     strings without a German translation in tools/i18n_de.json
    python tools/i18n.py build       write fritzfax/gui/i18n/fritzfax_de.ts and compile the .qm

Strings are found with the AST: self.tr("..") (context = enclosing class), tr("..") in a
module whose tr() wraps QCoreApplication.translate("<ctx>", ..), and direct
QCoreApplication.translate("<ctx>", "..") calls. Only literal strings count.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "fritzfax" / "gui"
TABLE = ROOT / "tools" / "i18n_de.json"
TS = GUI / "i18n" / "fritzfax_de.ts"
QM = GUI / "i18n" / "fritzfax_de.qm"


def _module_tr_context(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "tr":
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "translate" and call.args
                        and isinstance(call.args[0], ast.Constant)):
                    return call.args[0].value
    return None


def extract() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(GUI.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_ctx = _module_tr_context(tree)

        def visit(node, cls):
            if isinstance(node, ast.ClassDef):
                cls = node.name
            if isinstance(node, ast.Call):
                f = node.func
                text_arg = None
                ctx = None
                if isinstance(f, ast.Attribute) and f.attr == "tr" and isinstance(f.value, ast.Name) \
                        and f.value.id == "self" and node.args:
                    text_arg, ctx = node.args[0], cls
                elif isinstance(f, ast.Name) and f.id == "tr" and module_ctx and node.args:
                    text_arg, ctx = node.args[0], module_ctx
                elif isinstance(f, ast.Attribute) and f.attr == "translate" and len(node.args) >= 2 \
                        and isinstance(node.args[0], ast.Constant):
                    text_arg, ctx = node.args[1], node.args[0].value
                if text_arg is not None and isinstance(text_arg, ast.Constant) and isinstance(text_arg.value, str):
                    if ctx is None:
                        raise SystemExit(f"{path.name}:{node.lineno}: self.tr outside a class")
                    item = (ctx, text_arg.value)
                    if item not in found:
                        found.append(item)
            for child in ast.iter_child_nodes(node):
                visit(child, cls)

        visit(tree, None)
    return found


def table() -> dict[str, str]:
    return json.loads(TABLE.read_text(encoding="utf-8")) if TABLE.exists() else {}


def missing() -> list[str]:
    t = table()
    return sorted({src for _, src in extract() if not t.get(src)})


def render_ts() -> str:
    t = table()
    contexts: dict[str, list[str]] = {}
    for ctx, src in extract():
        contexts.setdefault(ctx, []).append(src)
    out = ['<?xml version="1.0" encoding="utf-8"?>', "<!DOCTYPE TS>",
           '<TS version="2.1" language="de_DE" sourcelanguage="en_US">']
    for ctx in sorted(contexts):
        out.append(f"<context>\n    <name>{escape(ctx)}</name>")
        for src in contexts[ctx]:
            tr = t.get(src, "")
            attr = "" if tr else ' type="unfinished"'
            out.append(f"    <message>\n        <source>{escape(src)}</source>\n"
                       f"        <translation{attr}>{escape(tr)}</translation>\n    </message>")
        out.append("</context>")
    out.append("</TS>\n")
    return "\n".join(out)


def lrelease(ts: Path, qm: Path) -> None:
    tool = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if not tool:
        raise SystemExit("pyside6-lrelease not found")
    subprocess.run([tool, "-silent", str(ts), "-qm", str(qm)], check=True)


def build() -> None:
    gaps = missing()
    if gaps:
        print("missing German translations:", *gaps, sep="\n  ", file=sys.stderr)
        raise SystemExit(1)
    TS.parent.mkdir(parents=True, exist_ok=True)
    TS.write_text(render_ts(), encoding="utf-8")
    lrelease(TS, QM)
    print(f"wrote {TS.relative_to(ROOT)} and {QM.relative_to(ROOT)} ({len(extract())} strings)")


def compiled_matches() -> bool:
    """True if the committed .ts/.qm equal a fresh build (used by the tests)."""
    if not TS.exists() or TS.read_text(encoding="utf-8") != render_ts():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "x.qm"
        lrelease(TS, fresh)
        return QM.exists() and fresh.read_bytes() == QM.read_bytes()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "extract":
        for c, s in extract():
            print(f"{c}\t{s!r}")
    elif cmd == "missing":
        print("\n".join(missing()))
    elif cmd == "build":
        build()
    else:
        raise SystemExit(__doc__)
