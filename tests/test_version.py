from __future__ import annotations

import json
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class VersionTests(unittest.TestCase):
    def test_public_version_is_in_run_reports(self) -> None:
        from dmfix import __version__
        from dmfix.core.report import RunReport

        self.assertEqual(__version__, "1.1.0")
        report = RunReport(scanned_folder="input", output_folder="output")
        self.assertEqual(json.loads(report.to_json())["tool_version"], __version__)
        self.assertIn(f"version  : {__version__}", report.to_text())

    def test_cli_version_does_not_require_a_target_folder(self) -> None:
        from contextlib import redirect_stdout

        from dmfix.cli import main

        output = io.StringIO()
        with self.assertRaises(SystemExit) as exit_context, redirect_stdout(output):
            main(["--version"])
        self.assertEqual(exit_context.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "dmfix 1.1.0")


if __name__ == "__main__":
    unittest.main()
