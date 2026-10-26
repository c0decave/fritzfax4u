"""The README screenshots, made from made-up data (no box, no real numbers except the public test fax).

    tools/dev.sh run python tools/screenshots.py docs/images
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from fritzfax.core import config  # noqa: E402
from fritzfax.core.faxapi import FaxInfo  # noqa: E402
from fritzfax.gui import i18n, theme  # noqa: E402
from fritzfax.gui.main_window import MainWindow  # noqa: E402
from tests_helpers import one_page_pdf  # noqa: E402


def settle(app, ready, seconds=30):
    end = time.time() + seconds
    while not ready() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(25):
        app.processEvents()
        time.sleep(0.02)


def window(app, tmp: Path) -> MainWindow:
    s = config.Settings(box_url="https://fritz.box", username="fax", sender_name="Erika Muster")
    w = MainWindow(s, tmp / "x.conf", "de")
    w.resize(1280, 820)
    w.show()
    w.box_info = FaxInfo(True, ["87654321"], "Erika Muster", "E. Muster", "Erika Muster", False)
    w.box_location = ("49", "30")
    w._fill_sender_numbers()
    w._set_chip("connected")
    w.status_label.setText("Verbunden mit https://fritz.box als fax - Fax bereit")
    w.to_edit.setText("0531 49059113")
    w.to_name_edit.setText("Testempfang")
    return w


def main(out: str) -> int:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    i18n.install(app, "de")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        pdf = one_page_pdf(tmp / "Rechnung-2026-10.pdf", pages=3, text="Rechnung Oktober")

        theme.apply(app, "light")
        w = window(app, tmp)
        w.add_files([str(pdf)])
        settle(app, lambda: w.prepared is not None and w.thumbs.count() == 3)
        w.grab().save(str(out_dir / "fritzfax-light.png"))
        w.close()

        theme.apply(app, "dark")
        w = window(app, tmp)
        w.add_files([str(pdf)])
        w.cover_check.setChecked(True)
        w.mode_letter.setChecked(True)
        w.subject_edit.setText("Rechnung Oktober")
        w.text_edit.setPlainText("Sehr geehrte Damen und Herren,\n\nanbei die Rechnung für Oktober.\n\n"
                                 "Mit freundlichen Grüßen\nErika Muster")
        settle(app, lambda: not w.preview_pending and not w.preview_timer.isActive() and w.thumbs.count() == 5)
        w.thumbs.setCurrentRow(1)
        settle(app, lambda: True, 1)
        w.grab().save(str(out_dir / "fritzfax-dark.png"))
        w.close()
    print("written:", *sorted(p.name for p in out_dir.glob("fritzfax-*.png")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/images"))
