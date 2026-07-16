from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class PipelineControlTests(unittest.TestCase):
    def test_heavy_simplification_checks_stop_before_opening_the_mesh(self) -> None:
        from dmfix.core.fixes.simplify import simplify_collision

        class Requested(Exception):
            pass

        def stop_check() -> None:
            raise Requested

        with self.assertRaises(Requested):
            simplify_collision(
                "missing-input.nif",
                "unused-output.nif",
                stop_check=stop_check,
            )

    def test_heavy_simplification_stops_after_an_internal_scan(self) -> None:
        from dmfix.core.fixes.simplify import simplify_collision
        from dmfix.core.pipeline import RunControl, StopRequested

        control = RunControl()
        layout = SimpleNamespace(payload=lambda _index: b"shape")
        collision = SimpleNamespace(
            shape_chain=("bhkMoppBvTreeShape", "bhkCompressedMeshShape"),
            shape_block_index=1,
            child_shape_block_index=2,
        )
        scanner = SimpleNamespace(
            scan_file=lambda _path: (
                control.request_stop(),
                SimpleNamespace(raw={}),
            )[1]
        )
        with (
            patch(
                "dmfix.core.fixes.simplify.NifFileLayout.read",
                return_value=layout,
            ),
            patch(
                "dmfix.core.fixes.simplify.locate_collisions",
                return_value=[collision],
            ),
            patch("dmfix.core.fixes.simplify.read_mopp"),
            patch("dmfix.core.fixes.simplify.decode_compressed_mesh"),
            patch(
                "dmfix.core.fixes.simplify._drop_degenerate",
                return_value=(
                    [(0.0, 0.0, 0.0)],
                    [(0, 0, 0)],
                    [0],
                ),
            ),
            patch(
                "dmfix.core.fixes.degenerate._scanner",
                return_value=scanner,
            ),
            patch(
                "dmfix.core.fixes.simplify._connected_face_components"
            ) as components,
        ):
            with self.assertRaises(StopRequested):
                simplify_collision(
                    "input.nif",
                    "output.nif",
                    stop_check=control.raise_if_stopped,
                )

        components.assert_not_called()

    def test_stop_during_fix_aborts_at_internal_checkpoint(self) -> None:
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
            (root / "dmscan.exe").touch()
            source = root / "heavy.nif"
            source.write_bytes(b"nif")
            scan_temp = root / "scan-temp"
            scan_temp.mkdir()
            record = ScanRecord(
                file=str(source),
                verdict="VERY HEAVY COLLISION",
                status="PROBLEM",
                raw={},
                categories=[FixCategory.HEAVY],
            )
            item = WorkItem(
                r"meshes\heavy.nif", "loose", source, record=record
            )
            control = RunControl()
            events = []

            def fix(_input, _output, *, stop_check, item_progress, **_kwargs):
                item_progress("simplification round 1/4")
                control.request_stop()
                stop_check()

            with (
                patch(
                    "dmfix.core.pipeline.collect_work_items",
                    return_value=([item], scan_temp),
                ),
                patch(
                    "dmfix.core.pipeline._fix_functions",
                    return_value={FixCategory.HEAVY: fix},
                ),
            ):
                report = run_pipeline(
                    root,
                    PipelineOptions(
                        deadmesh_dir=root,
                        output_dir=root / "output" / "Meshes",
                        categories={FixCategory.HEAVY},
                        include_bsa=False,
                    ),
                    control=control,
                    on_event=events.append,
                )

            self.assertEqual(report.status, "stopped")
            self.assertEqual(report.processed_items, 0)
            self.assertEqual([result.outcome for result in report.results], [Outcome.NOT_RUN])
            self.assertEqual(
                [event.kind for event in events],
                [
                    PipelineEventKind.ITEM_STARTED,
                    PipelineEventKind.ITEM_PROGRESS,
                    PipelineEventKind.ITEM_COMPLETED,
                    PipelineEventKind.RUN_STOPPED,
                ],
            )
            self.assertEqual(events[1].message, "simplification round 1/4")

    def test_loose_items_preserve_paths_below_selected_folder(self) -> None:
        from dmfix.core.pipeline import PipelineOptions, collect_work_items
        from dmfix.core.scanner import FixCategory, ScanRecord

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "dmscan.exe").touch()
            nested = root / "Lux (patch hub)" / "SmallHouse.nif"
            nested.parent.mkdir()
            nested.write_bytes(b"nif")
            record = ScanRecord(
                file=str(nested),
                verdict="CRASH RISK",
                status="PROBLEM",
                raw={},
                categories=[FixCategory.CRASH],
            )
            options = PipelineOptions(
                deadmesh_dir=root,
                output_dir=root / "DeadMesh-Fixed" / "Meshes",
                include_bsa=False,
            )
            with patch(
                "dmfix.core.pipeline.DmScan.scan_dir", return_value=[record]
            ):
                items, scan_temp = collect_work_items(
                    root, options, lambda *_args: None
                )
            try:
                self.assertEqual(
                    items[0].relative_path,
                    r"lux (patch hub)\smallhouse.nif",
                )
            finally:
                scan_temp.rmdir()

    def test_mesh_output_path_has_exactly_one_meshes_root(self) -> None:
        from dmfix.core.pipeline import ensure_mesh_output_dir, mesh_output_path

        output_root = Path(r"C:\Mods\Example\DeadMesh-Fixed\Meshes")
        self.assertEqual(ensure_mesh_output_dir(output_root), output_root)
        self.assertEqual(
            ensure_mesh_output_dir(Path(r"D:\My Output")),
            Path(r"D:\My Output\Meshes"),
        )
        self.assertEqual(
            mesh_output_path(output_root, r"meshes\architecture\house.nif"),
            output_root / "architecture" / "house.nif",
        )
        self.assertEqual(
            mesh_output_path(output_root, r"Lux (patch hub)\interior.nif"),
            output_root / "Lux (patch hub)" / "interior.nif",
        )

    def test_pipeline_writes_below_mesh_root_without_duplicate_folder(self) -> None:
        from dmfix.core.pipeline import PipelineOptions, WorkItem, run_pipeline
        from dmfix.core.scanner import FixCategory, ScanRecord

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "house.nif"
            source.write_bytes(b"nif")
            scan_temp = root / "scan-temp"
            scan_temp.mkdir()
            output_root = root / "DeadMesh-Fixed" / "Meshes"
            record = ScanRecord(
                file=str(source),
                verdict="CRASH RISK",
                status="PROBLEM",
                raw={},
                categories=[FixCategory.CRASH],
            )
            item = WorkItem(
                r"meshes\architecture\house.nif",
                "loose",
                source,
                record=record,
            )

            def fix(input_path, output_path, **_kwargs):
                output_path.write_bytes(input_path.read_bytes())
                return SimpleNamespace(success=True)

            clean = ScanRecord(
                file="candidate.nif",
                verdict="OK",
                status="CLEAN",
                raw={},
                categories=[],
            )
            with (
                patch(
                    "dmfix.core.pipeline.collect_work_items",
                    return_value=([item], scan_temp),
                ),
                patch(
                    "dmfix.core.pipeline._fix_functions",
                    return_value={FixCategory.CRASH: fix},
                ),
                patch("dmfix.core.pipeline.DmScan") as scanner_type,
            ):
                scanner_type.return_value.scan_file.return_value = clean
                scanner_type.return_value.vs_check.return_value = True
                report = run_pipeline(
                    root,
                    PipelineOptions(
                        deadmesh_dir=root,
                        output_dir=output_root,
                        categories={FixCategory.CRASH},
                        include_bsa=False,
                    ),
                )

            expected = output_root / "architecture" / "house.nif"
            self.assertTrue(expected.is_file())
            self.assertFalse((output_root / "meshes").exists())
            self.assertEqual(report.results[0].output_path, str(expected))
            self.assertTrue(
                (output_root.parent / "deadmesh-fix-report.json").is_file()
            )
            self.assertFalse((output_root / "deadmesh-fix-report.json").exists())

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
