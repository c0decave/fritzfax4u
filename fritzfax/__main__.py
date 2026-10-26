"""One program for both modes: `fritzfax` / `fritzfax gui` / `fritzfax-gui` start the GUI,
`fritzfax <command>` runs the command line interface."""
from __future__ import annotations

import re
import sys


def choose(argv0: str, args: list[str]) -> tuple[str, list[str]]:
    name = re.split(r"[\\/]", argv0)[-1]  # also a Windows path on any system
    if re.sub(r"\.exe$", "", name, flags=re.IGNORECASE).endswith("-gui"):
        return "gui", args
    if not args:
        return "gui", []
    if args[0] == "gui":
        return "gui", args[1:]
    return "cli", args


def main() -> int:
    mode, args = choose(sys.argv[0], sys.argv[1:])
    if mode == "gui":
        from .gui.app import main as gui_main

        return gui_main(args)
    from .cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
