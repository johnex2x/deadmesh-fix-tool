"""Entry point: no arguments -> GUI; any argument -> CLI."""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from dmfix.cli import main as cli_main
        return cli_main()
    from dmfix.gui.main_window import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
