"""Entry point of the frozen fritzfax binary (PyInstaller)."""
import sys

from fritzfax import syslibs

syslibs.preload_frozen()  # before anything imports Qt
syslibs.restore_system_env()  # fc-match, the PDF viewer, LibreOffice: the system's libraries

from fritzfax.__main__ import main  # noqa: E402

sys.exit(main())
