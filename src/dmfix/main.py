"""Entry point: arguments -> CLI; no arguments -> GUI (or usage on a console).

The distribution ships two launchers over this same entry point: windowed
DeadMeshFixTool.exe (GUI) and console dmfix.exe (CLI). A bare `dmfix` in a
terminal prints usage instead of surprising the user with a GUI window.
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from dmfix.cli import main as cli_main
        return cli_main()
    # The windowed launcher has no attached console (stdout is None or not a
    # tty); the console launcher run bare from a terminal does.
    if sys.stdout is not None and sys.stdout.isatty():
        from dmfix.cli import build_parser
        build_parser().print_help()
        return 2
    from dmfix.gui.main_window import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
