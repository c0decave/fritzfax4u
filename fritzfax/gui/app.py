"""Entry point of the graphical application."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .. import __version__
from ..core import config
from . import i18n, theme
from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fritzfax-gui")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("files", nargs="*", help="files to put into the fax")
    args, qt_args = parser.parse_known_args(argv)
    app = QApplication.instance() or QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("fritzfax")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("fritzfax")
    settings = config.load(args.config)
    language = i18n.install(app, settings.language)
    theme.apply(app, settings.theme)
    window = MainWindow(settings, args.config, language)
    window.add_files(args.files)
    window.show()
    window.report_load_problem()
    window.auto_connect()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
