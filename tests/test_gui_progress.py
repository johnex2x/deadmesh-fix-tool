from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class GuiProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_window_exposes_version_and_safe_run_controls(self) -> None:
        from dmfix.gui.main_window import MainWindow
        from dmfix.gui.settings import Settings

        window = MainWindow(Settings(language="en"))
        try:
            self.assertIn("1.0.2", window.windowTitle())
            self.assertEqual(window.pause_button.text(), "Pause")
            self.assertEqual(window.stop_button.text(), "Stop")
            self.assertFalse(window.pause_button.isEnabled())
            self.assertFalse(window.stop_button.isEnabled())
            window._set_inputs_enabled(False)
            self.assertFalse(window.language_combo.isEnabled())
            window._set_inputs_enabled(True)
            self.assertTrue(window.language_combo.isEnabled())
        finally:
            window.close()

    def test_completed_file_updates_its_row_immediately(self) -> None:
        from dmfix.core.pipeline import PipelineEvent, PipelineEventKind, WorkItem
        from dmfix.core.report import FileResult, Outcome
        from dmfix.core.scanner import FixCategory, ScanRecord
        from dmfix.gui.main_window import MainWindow
        from dmfix.gui.settings import Settings

        relative_path = r"meshes\example.nif"
        record = ScanRecord(
            file=relative_path,
            verdict="CRASH RISK",
            status="PROBLEM",
            raw={},
            categories=[FixCategory.CRASH],
        )
        result = FileResult(
            source=relative_path,
            relative_path=relative_path,
            categories=[FixCategory.CRASH.value],
            outcome=Outcome.FIXED,
            verdict_before="CRASH RISK",
            verdict_after="OK",
        )
        window = MainWindow(Settings(language="en"))
        try:
            window._pending_items = [
                WorkItem(relative_path, "loose", Path(relative_path), record=record)
            ]
            window._render_rows()
            window._pipeline_event(
                PipelineEvent(PipelineEventKind.ITEM_STARTED, 0, 1, relative_path)
            )
            self.assertEqual(window.results_table.item(0, 1).text(), "Processing")
            window._pipeline_event(
                PipelineEvent(
                    PipelineEventKind.ITEM_PROGRESS,
                    0,
                    1,
                    relative_path,
                    message="simplification round 1/4: decimation",
                )
            )
            self.assertIn(
                "simplification round 1/4: decimation",
                window.status_label.text(),
            )

            window._pipeline_event(
                PipelineEvent(
                    PipelineEventKind.ITEM_COMPLETED,
                    1,
                    1,
                    relative_path,
                    result,
                )
            )
            self.assertEqual(window.results_table.item(0, 1).text(), "Fixed")
            self.assertEqual(
                window.results_table.item(0, 3).text(), "CRASH RISK -> OK"
            )
            self.assertEqual(window.progress_bar.value(), 1)
            self.assertIn("Fixed 1", window.count_label.text())
        finally:
            window.close()

    def test_pause_resume_and_stop_requests_are_visible_and_cooperative(self) -> None:
        from dmfix.core.pipeline import (
            PipelineEvent,
            PipelineEventKind,
            PipelineOptions,
        )
        from dmfix.gui.main_window import FixWorker, MainWindow
        from dmfix.gui.settings import Settings

        window = MainWindow(Settings(language="en"))
        worker = FixWorker(
            Path("target"),
            PipelineOptions(deadmesh_dir=Path("deadmesh"), output_dir=Path("output")),
        )
        try:
            window._worker = worker
            window.pause_button.setEnabled(True)
            window.stop_button.setEnabled(True)

            window._toggle_pause()
            self.assertTrue(worker.control.is_paused)
            self.assertEqual(window.pause_button.text(), "Resume")
            self.assertIn("Pause requested", window.status_label.text())

            window._toggle_pause()
            self.assertFalse(worker.control.is_paused)
            self.assertEqual(window.pause_button.text(), "Pause")

            window._stop()
            self.assertTrue(worker.control.is_stop_requested)
            self.assertIn("Stop requested", window.status_label.text())
            self.assertFalse(window.pause_button.isEnabled())
            self.assertFalse(window.stop_button.isEnabled())

            window._pipeline_event(
                PipelineEvent(
                    PipelineEventKind.ITEM_PROGRESS,
                    0,
                    1,
                    r"meshes\heavy.nif",
                    message="simplification round 2/4: DeadMesh scan",
                )
            )
            self.assertIn("Stop requested", window.status_label.text())
        finally:
            window._worker = None
            window.close()

    def test_stopped_report_keeps_unstarted_rows_visible_and_retryable(self) -> None:
        from PySide6.QtCore import Qt

        from dmfix.core.report import FileResult, Outcome, RunReport
        from dmfix.gui.main_window import MainWindow
        from dmfix.gui.settings import Settings

        report = RunReport(
            scanned_folder="target",
            output_folder="output",
            status="stopped",
            total_items=2,
            processed_items=1,
            results=[
                FileResult(
                    source="second.nif",
                    relative_path=r"meshes\second.nif",
                    categories=["crash"],
                    outcome=Outcome.NOT_RUN,
                    verdict_before="CRASH RISK",
                )
            ],
        )
        window = MainWindow(Settings(language="en"))
        try:
            window._fix_finished(report)
            self.assertEqual(window.results_table.item(0, 1).text(), "Not run")
            self.assertEqual(
                window.results_table.item(0, 0).checkState(), Qt.CheckState.Checked
            )
            self.assertIn("Stopped safely after 1/2", window.status_label.text())
            self.assertEqual(window.progress_bar.value(), 1)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
