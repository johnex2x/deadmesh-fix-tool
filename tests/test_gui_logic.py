from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        from dmfix.gui.settings import Settings, load, save

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"APPDATA": temp_dir}):
                expected = Settings(
                    deadmesh_dir=r"C:\Tools\DeadMesh",
                    language="zh-TW",
                    last_target_folder=r"C:\Mods\Example",
                    strength="aggressive",
                    categories=["crash", "degenerate"],
                    include_bsa=False,
                )
                save(expected)
                self.assertEqual(load(), expected)

    def test_missing_or_corrupt_settings_use_defaults(self) -> None:
        from dmfix.gui.settings import Settings, load

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"APPDATA": temp_dir}):
                self.assertEqual(load(), Settings())
                settings_file = Path(temp_dir) / "DeadMesh Fix Tool" / "settings.json"
                settings_file.parent.mkdir(parents=True)
                settings_file.write_text("{not json", encoding="utf-8")
                self.assertEqual(load(), Settings())

    def test_invalid_setting_types_use_safe_defaults(self) -> None:
        from dmfix.gui.settings import Settings, load

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"APPDATA": temp_dir}):
                settings_file = Path(temp_dir) / "DeadMesh Fix Tool" / "settings.json"
                settings_file.parent.mkdir(parents=True)
                settings_file.write_text(
                    '{"language": 3, "categories": null, "include_bsa": "yes"}',
                    encoding="utf-8",
                )
                self.assertEqual(load(), Settings())


class I18nTests(unittest.TestCase):
    def test_every_string_has_english_and_traditional_chinese(self) -> None:
        from dmfix.gui.i18n import STRINGS, set_language, tr

        self.assertTrue(STRINGS)
        for key, translations in STRINGS.items():
            self.assertEqual(set(translations), {"en", "zh-TW"}, key)
            self.assertTrue(translations["en"], key)
            self.assertTrue(translations["zh-TW"], key)

        set_language("zh-TW")
        self.assertEqual(tr("scan"), "掃描")
        set_language("en")
        self.assertEqual(tr("scan"), "Scan")


class OutputFolderTests(unittest.TestCase):
    def test_output_folder_is_derived_from_target(self) -> None:
        from dmfix.gui.main_window import derive_output_folder

        self.assertEqual(derive_output_folder(""), "")
        self.assertEqual(
            derive_output_folder(r"C:\Mods\Example"),
            r"C:\Mods\Example\DeadMesh-Fixed",
        )

    def test_manual_output_is_preserved_when_target_changes(self) -> None:
        from dmfix.gui.main_window import output_folder_after_target_change

        self.assertEqual(
            output_folder_after_target_change(
                r"C:\Mods\New", r"D:\My Output", manually_edited=True
            ),
            r"D:\My Output",
        )
        self.assertEqual(
            output_folder_after_target_change(
                r"C:\Mods\New", r"D:\My Output", manually_edited=False
            ),
            r"C:\Mods\New\DeadMesh-Fixed",
        )

    def test_output_folder_cannot_be_empty_or_replace_the_target(self) -> None:
        from dmfix.gui.main_window import is_safe_output_folder

        self.assertFalse(is_safe_output_folder(r"C:\Mods\Example", ""))
        self.assertFalse(
            is_safe_output_folder(r"C:\Mods\Example", r"C:\Mods\Example")
        )
        self.assertTrue(
            is_safe_output_folder(
                r"C:\Mods\Example", r"C:\Mods\Example\DeadMesh-Fixed"
            )
        )

    def test_target_folder_must_be_explicit_and_existing(self) -> None:
        from dmfix.gui.main_window import is_valid_target_folder

        self.assertFalse(is_valid_target_folder(""))
        self.assertFalse(is_valid_target_folder(r"Z:\Does\Not\Exist"))
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertTrue(is_valid_target_folder(temp_dir))


if __name__ == "__main__":
    unittest.main()
