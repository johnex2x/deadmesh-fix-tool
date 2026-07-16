from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class PipelineControlTests(unittest.TestCase):
    def test_stop_request_finishes_current_item_and_marks_remaining_not_run(self) -> None:
        from dmfix.core.pipeline import (
            PipelineEventKind,
            PipelineOptions,
            RunControl,
            WorkItem,
            run_pipeline,
        )
        from dmfix.core.report import Outcome
        from dmfix.core.scanner import FixCategory, ScanRecord

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deadmesh = root / "DeadMesh"
            deadmesh.mkdir()
            (deadmesh / "dmscan.exe").touch()
            output = root / "output"
            scan = ScanRecord(
                file="unused.nif",
                verdict="HEAVY COLLISION",
                status="CLEAN",
                raw={},
                categories=[FixCategory.HEAVY],
            )
            items = [
                WorkItem(f"meshes\\item-{index}.nif", "loose", root / "unused.nif", record=scan)
                for index in range(2)
            ]
            scan_temp = root / "scan-temp"
            scan_temp.mkdir()
            control = RunControl()
            events = []

            def on_event(event) -> None:
                events.append(event)
                if event.kind is PipelineEventKind.ITEM_COMPLETED:
                    control.request_stop()

            options = PipelineOptions(
                deadmesh_dir=deadmesh,
                output_dir=output,
                categories=set(),
                include_bsa=False,
            )
            with patch(
                "dmfix.core.pipeline.collect_work_items",
                return_value=(items, scan_temp),
            ):
                report = run_pipeline(
                    root,
                    options,
                    control=control,
                    on_event=on_event,
                )

            self.assertEqual(report.status, "stopped")
            self.assertEqual(report.processed_items, 1)
            self.assertEqual(report.total_items, 2)
            self.assertEqual(
                [result.outcome for result in report.results],
                [Outcome.SKIPPED, Outcome.NOT_RUN],
            )
            self.assertEqual(
                [event.kind for event in events],
                [
                    PipelineEventKind.ITEM_STARTED,
                    PipelineEventKind.ITEM_COMPLETED,
                    PipelineEventKind.RUN_STOPPED,
                ],
            )

    def test_pause_blocks_before_next_item_until_resumed(self) -> None:
        from dmfix.core.pipeline import (
            PipelineEventKind,
            PipelineOptions,
            RunControl,
            WorkItem,
            run_pipeline,
        )
        from dmfix.core.scanner import FixCategory, ScanRecord

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deadmesh = root / "DeadMesh"
            deadmesh.mkdir()
            (deadmesh / "dmscan.exe").touch()
            scan = ScanRecord(
                file="unused.nif",
                verdict="HEAVY COLLISION",
                status="CLEAN",
                raw={},
                categories=[FixCategory.HEAVY],
            )
            items = [
                WorkItem(f"meshes\\item-{index}.nif", "loose", root / "unused.nif", record=scan)
                for index in range(2)
            ]
            scan_temp = root / "scan-temp"
            scan_temp.mkdir()
            control = RunControl()
            events = []
            first_completed = threading.Event()
            report_holder = []

            def on_event(event) -> None:
                events.append(event)
                if (
                    event.kind is PipelineEventKind.ITEM_COMPLETED
                    and event.current == 1
                ):
                    control.request_pause()
                    first_completed.set()

            options = PipelineOptions(
                deadmesh_dir=deadmesh,
                output_dir=root / "output",
                categories=set(),
                include_bsa=False,
            )

            def run() -> None:
                with patch(
                    "dmfix.core.pipeline.collect_work_items",
                    return_value=(items, scan_temp),
                ):
                    report_holder.append(
                        run_pipeline(
                            root,
                            options,
                            control=control,
                            on_event=on_event,
                        )
                    )

            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(first_completed.wait(timeout=2))
            thread.join(timeout=0.05)
            self.assertTrue(thread.is_alive())
            self.assertNotIn(
                (PipelineEventKind.ITEM_STARTED, 1),
                [(event.kind, event.current) for event in events],
            )

            control.resume()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(report_holder[0].status, "completed")
            self.assertEqual(
                [event.kind for event in events],
                [
                    PipelineEventKind.ITEM_STARTED,
                    PipelineEventKind.ITEM_COMPLETED,
                    PipelineEventKind.RUN_PAUSED,
                    PipelineEventKind.RUN_RESUMED,
                    PipelineEventKind.ITEM_STARTED,
                    PipelineEventKind.ITEM_COMPLETED,
                    PipelineEventKind.RUN_FINISHED,
                ],
            )


if __name__ == "__main__":
    unittest.main()
