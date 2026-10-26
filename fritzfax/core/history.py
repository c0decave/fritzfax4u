"""Local send log: when, what, to whom, with which content.

Per send job the exact fax pages are kept as a readable PDF (plus the SFF for resending);
per destination one row in SQLite (history.db) and one line in a plain text log (send.log).
Everything stays in ~/.local/share/fritzfax and can be switched off (settings: keep_history).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from . import paths
from . import sff as sffmod

# pixels per inch of a fine-resolution fax page: 1728 px across 215 mm, 7.7 lines/mm
FAX_DPI = (sffmod.LINE_WIDTH / (215 / 25.4), 7.7 * 25.4)

_COLUMNS = {  # name -> SQL type; every column after the original nine is added by migration
    "ts": "REAL", "destination": "TEXT", "sender_number": "TEXT", "pages": "INTEGER", "ok": "INTEGER",
    "error": "TEXT", "reason": "INTEGER", "sff_path": "TEXT",
    "job_id": "TEXT", "started": "REAL", "finished": "REAL", "sender_name": "TEXT", "subject": "TEXT",
    "text": "TEXT", "mode": "TEXT", "cover": "INTEGER", "files": "TEXT", "content_pdf": "TEXT",
    "verification": "TEXT", "report_pdf": "TEXT",
}


def default_paths() -> tuple[Path, Path]:
    base = paths.data_dir()
    return base / "history.db", base / "sent"


@dataclass
class FileInfo:
    name: str
    path: str
    sha256: str
    pages: list[int] | None = None  # 0-based selection, None = all

    @classmethod
    def of(cls, path: Path, pages: list[int] | None) -> "FileInfo":
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            sha = digest.hexdigest()
        except OSError:
            sha = ""
        return cls(Path(path).name, str(Path(path).resolve()), sha, pages)


@dataclass
class Content:
    """What a send job contains, as entered by the user."""
    sender_name: str = ""
    sender_number: str = ""
    subject: str = ""
    text: str = ""
    mode: str = "document"
    cover: bool = False
    files: list[FileInfo] = field(default_factory=list)


@dataclass
class Entry:
    id: int
    timestamp: float
    destination: str
    sender_number: str
    pages: int
    ok: bool
    error: str
    reason: int
    sff_path: str
    job_id: str = ""
    started: float = 0.0
    finished: float = 0.0
    sender_name: str = ""
    subject: str = ""
    text: str = ""
    mode: str = ""
    cover: bool = False
    files: list[FileInfo] = field(default_factory=list)
    content_pdf: str = ""
    verification: dict = field(default_factory=dict)
    report_pdf: str = ""

    @property
    def verdict(self) -> str:
        return self.verification.get("verdict", "")

    def to_dict(self) -> dict:
        return asdict(self)


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


class History:
    def __init__(self, db_path: Path | None = None, sff_dir: Path | None = None):
        default_db, default_dir = default_paths()
        self.db_path = Path(db_path or default_db)
        self.sff_dir = Path(sff_dir or default_dir)
        self.text_log = self.db_path.parent / "send.log"
        paths.private_dir(self.db_path.parent)  # sent faxes and to whom: only for the own user
        paths.private_dir(self.sff_dir)
        paths.private_file(self.db_path)
        paths.private_file(self.text_log)
        self._jobs: dict[str, tuple[Content, int, str, str]] = {}
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sends (id INTEGER PRIMARY KEY, ts REAL, destination TEXT, "
                       "sender_number TEXT, pages INTEGER, ok INTEGER, error TEXT, reason INTEGER, sff_path TEXT)")
            present = {row[1] for row in db.execute("PRAGMA table_info(sends)")}
            for name, typ in _COLUMNS.items():
                if name not in present:
                    db.execute(f"ALTER TABLE sends ADD COLUMN {name} {typ}")

    @contextlib.contextmanager
    def _db(self):
        """One connection per use: commit on success, roll back on error, always close."""
        con = sqlite3.connect(self.db_path)
        try:
            with con:
                yield con
        finally:
            con.close()

    # --- recording ---------------------------------------------------------------
    def start_job(self, content: Content, pages: list[Image.Image] | None, data: bytes) -> str:
        """Store the content of one send job (PDF of the exact pages + SFF); returns the job id."""
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        sff_path = self.sff_dir / f"{job_id}.sff"
        sff_path.write_bytes(data)
        if pages is None:
            pages = sffmod.decode(data)
        pdf_path = self.sff_dir / f"{job_id}.pdf"
        bilevel = [p if p.mode == "1" else p.convert("1") for p in pages]  # "1" -> CCITT, lossless
        bilevel[0].save(pdf_path, "PDF", save_all=True, append_images=bilevel[1:], dpi=FAX_DPI,
                     title=f"fritzfax {job_id}", author=content.sender_name or "fritzfax")
        self._jobs[job_id] = (content, len(pages), str(sff_path), str(pdf_path))
        return job_id

    def record(self, job_id: str, destination: str, ok: bool, error: str, reason: int,
               started: float | None = None, finished: float | None = None, verification: dict | None = None,
               report_pdf: str = "") -> None:
        content, pages, sff_path, pdf_path = self._jobs[job_id]
        now = time.time()
        started = started or now
        finished = finished or now
        files_json = json.dumps([asdict(f) for f in content.files], ensure_ascii=False)
        with self._db() as db:
            db.execute(
                "INSERT INTO sends (ts, destination, sender_number, pages, ok, error, reason, sff_path, job_id, "
                "started, finished, sender_name, subject, text, mode, cover, files, content_pdf, verification, "
                "report_pdf) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (finished, destination, content.sender_number, pages, int(ok), error, reason, sff_path, job_id,
                 started, finished, content.sender_name, content.subject, content.text, content.mode,
                 int(content.cover), files_json, pdf_path, json.dumps(verification) if verification else "",
                 report_pdf))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(finished))
        result = "OK" if ok else "FAILED"
        if verification:
            result += "/" + verification.get("verdict", "").upper()
        detail = _one_line(error) + (f" (0x{reason:04X})" if reason and f"0x{reason:04X}" not in error else "")
        fields = [stamp, result, _one_line(destination), f"{pages} p", _one_line(content.sender_name),
                  _one_line(content.sender_number), _one_line(content.subject),
                  ", ".join(_one_line(f.name) for f in content.files), _one_line(content.text)[:200], detail,
                  _one_line(pdf_path), _one_line(report_pdf)]
        with open(self.text_log, "a", encoding="utf-8") as fh:
            fh.write("\t".join(fields) + "\n")

    # --- reading ------------------------------------------------------------------
    _SELECT = ("SELECT id, ts, destination, sender_number, pages, ok, error, reason, sff_path, job_id, started, "
               "finished, sender_name, subject, text, mode, cover, files, content_pdf, verification, report_pdf FROM sends")

    @staticmethod
    def _entry(r) -> Entry:
        files = [FileInfo(**f) for f in json.loads(r[17])] if r[17] else []
        return Entry(r[0], r[1] or 0.0, r[2] or "", r[3] or "", r[4] or 0, bool(r[5]), r[6] or "", r[7] or 0,
                     r[8] or "", r[9] or "", r[10] or 0.0, r[11] or 0.0, r[12] or "", r[13] or "", r[14] or "",
                     r[15] or "", bool(r[16]), files, r[18] or "", json.loads(r[19]) if r[19] else {},
                     r[20] or "")

    def entries(self, limit: int = 200, destinations=None) -> list[Entry]:
        """The newest `limit` entries, oldest first; only those to `destinations` (dialed form) if given."""
        where, args = "", ()
        if destinations is not None:
            wanted = sorted(set(destinations))
            if not wanted:
                return []
            where, args = f" WHERE destination IN ({', '.join('?' * len(wanted))})", tuple(wanted)
        with self._db() as db:
            rows = db.execute(self._SELECT + where + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._entry(r) for r in reversed(rows)]

    def get(self, entry_id: int) -> Entry:
        with self._db() as db:
            row = db.execute(self._SELECT + " WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return self._entry(row)
