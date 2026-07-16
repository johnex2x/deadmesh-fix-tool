from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dmfix.core.scanner import DmScan, DmScanError


def _record(path: Path) -> str:
    return json.dumps(
        {
            "file": str(path),
            "status": "CLEAN",
            "verdict": "OK",
            "orphan_mopp": False,
        }
    )


class DmScanUnicodePathTests(unittest.TestCase):
    def test_directory_scan_recovers_unicode_file_with_single_file_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deadmesh = root / "DeadMesh"
            deadmesh.mkdir()
            exe = deadmesh / "dmscan.exe"
            exe.touch()
            ascii_nif = root / "ascii.nif"
            unicode_nif = root / "SmallHouseInterior04 – kopija.nif"
            ascii_nif.touch()
            unicode_nif.touch()

            directory_result = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout=_record(ascii_nif) + "\n",
                stderr=f"cannot stat {unicode_nif}\n",
            )
            single_result = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=_record(unicode_nif),
                stderr="",
            )

            with patch(
                "dmfix.core.scanner.subprocess.run",
                side_effect=[directory_result, single_result],
            ) as run:
                records = DmScan(deadmesh).scan_dir(root)

            self.assertEqual([record.file for record in records], [str(ascii_nif), str(unicode_nif)])
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0][1], "--json")
            staged_path = Path(run.call_args_list[1].args[0][2])
            self.assertEqual(staged_path.name, "input.nif")
            self.assertTrue(str(staged_path).isascii())
            self.assertFalse(staged_path.exists())

    def test_unrelated_directory_scan_error_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deadmesh = root / "DeadMesh"
            deadmesh.mkdir()
            (deadmesh / "dmscan.exe").touch()
            failure = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="failed to initialize scanner\n",
            )

            with patch("dmfix.core.scanner.subprocess.run", return_value=failure):
                with self.assertRaises(DmScanError):
                    DmScan(deadmesh).scan_dir(root)


if __name__ == "__main__":
    unittest.main()
