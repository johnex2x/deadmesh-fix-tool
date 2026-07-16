"""End-to-end fix pipeline: scan -> extract -> fix -> verify -> output.

Policy (agreed with the user): never modify originals; never repack BSA; a fix
is only written to the output folder when dmscan certifies both that the
targeted defect is gone and that nothing else got worse. Files that cannot be
certified fail closed and are listed in the report instead.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from dmfix.core.bsa import BsaArchive
from dmfix.core.report import FileResult, Outcome, RunReport
from dmfix.core.scanner import DmScan, FixCategory, ScanRecord

ProgressCallback = Callable[[str, int, int, str], None]


class PipelineEventKind(Enum):
    ITEM_STARTED = "item_started"
    ITEM_PROGRESS = "item_progress"
    ITEM_COMPLETED = "item_completed"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_STOPPED = "run_stopped"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True)
class PipelineEvent:
    kind: PipelineEventKind
    current: int
    total: int
    relative_path: str = ""
    result: FileResult | None = None
    message: str = ""


EventCallback = Callable[[PipelineEvent], None]


class StopRequested(RuntimeError):
    """Raised at a safe internal checkpoint after the user requests Stop."""


class RunControl:
    """Thread-safe cooperative pause and safe-checkpoint stop control."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False
        self._stop_requested = False

    @property
    def is_paused(self) -> bool:
        with self._condition:
            return self._paused

    @property
    def is_stop_requested(self) -> bool:
        with self._condition:
            return self._stop_requested

    def request_pause(self) -> None:
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def request_stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._paused = False
            self._condition.notify_all()

    def checkpoint(self) -> bool:
        with self._condition:
            while self._paused and not self._stop_requested:
                self._condition.wait()
            return not self._stop_requested

    def raise_if_stopped(self) -> None:
        if self.is_stop_requested:
            raise StopRequested

# Order matters: structural crash repair first, then geometry cleanups, then
# the (geometry-changing) simplification, orphan-block removal last.
CATEGORY_ORDER = [
    FixCategory.CRASH,
    FixCategory.DEGENERATE,
    FixCategory.INVERTED,
    FixCategory.HEAVY,
    FixCategory.ORPHAN_BLOCKS,
]


@dataclass
class PipelineOptions:
    deadmesh_dir: Path
    output_dir: Path
    categories: set[FixCategory] = field(
        default_factory=lambda: set(CATEGORY_ORDER)
    )
    strength: str = "normal"          # HEAVY simplification strength
    include_bsa: bool = True
    # When set, only work items whose relative_path is in this set are
    # processed (GUI re-run of selected rows). None = everything found.
    only_paths: set[str] | None = None
    # Fresh runs clean only products recorded by the tool; retries preserve them.
    clean_previous_outputs: bool = True


@dataclass
class WorkItem:
    # Lowercase Windows-style path below the selected folder. Standard mod
    # roots may retain one leading meshes\ component; output joining removes it.
    relative_path: str
    source_kind: str                  # "loose" | "bsa"
    source_path: Path                 # loose file, or the .bsa archive
    bsa_inner_path: str = ""          # set when source_kind == "bsa"
    record: ScanRecord | None = None


def ensure_mesh_output_dir(output_dir: Path) -> Path:
    """Append Meshes unless the selected output is already that directory."""
    return (
        output_dir
        if output_dir.name.casefold() == "meshes"
        else output_dir / "Meshes"
    )


def default_mesh_output_dir(target_folder: Path) -> Path:
    """Return the MO2-ready mesh root used by GUI and CLI defaults."""
    return ensure_mesh_output_dir(target_folder / "DeadMesh-Fixed")


def mesh_output_path(output_dir: Path, relative_path: str) -> Path:
    """Place every repaired NIF directly below the MO2-ready mesh root."""
    name = Path(relative_path.replace("\\", "/")).name
    if not name:
        raise ValueError(f"invalid empty mesh path: {relative_path!r}")
    return output_dir / name


def report_output_dir(output_dir: Path) -> Path:
    """Keep reports beside an MO2-ready `Meshes` folder, not inside it."""
    return output_dir.parent if output_dir.name.casefold() == "meshes" else output_dir


OUTPUT_MANIFEST_NAME = "deadmesh-fix-output-manifest.json"


def _output_manifest_path(output_dir: Path) -> Path:
    return report_output_dir(output_dir) / OUTPUT_MANIFEST_NAME


def _write_output_manifest(output_dir: Path, names: set[str]) -> None:
    path = _output_manifest_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "files": sorted(names, key=str.casefold)}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _prepare_output_dir(output_dir: Path, *, clean: bool = True) -> set[str]:
    """Remove only prior tool products, with a one-time default-root migration."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _output_manifest_path(output_dir)
    names: set[str] = set()
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            names = {
                str(name) for name in data.get("files", [])
                if Path(str(name)).name == str(name)
            }
        except (OSError, ValueError, TypeError):
            names = set()
        if clean:
            for name in names:
                (output_dir / name).unlink(missing_ok=True)
    elif clean and output_dir.parent.name.casefold() == "deadmesh-fixed":
        # Migrate the old nested layout once: remove NIF products only.
        for nif in output_dir.rglob("*.nif"):
            if nif.parent != output_dir:
                nif.unlink(missing_ok=True)
        for folder in sorted(
            (p for p in output_dir.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                folder.rmdir()
            except OSError:
                pass
    if clean:
        _write_output_manifest(output_dir, set())
        return set()
    return names


def _record_output(output_dir: Path, names: set[str], destination: Path) -> None:
    names.add(destination.name)
    _write_output_manifest(output_dir, names)


def _fix_functions():
    """Category -> fix callable, imported lazily so missing stages degrade cleanly.

    Each callable has signature (input_path, output_path, **kwargs) and returns
    an object with a truthy `.success` (or raises). Fixes verify themselves via
    dmscan before reporting success.
    """
    from dmfix.core.fixes.mopp_rebuild import rebuild_mopp
    from dmfix.core.fixes.simplify import simplify_collision

    functions: dict[FixCategory, Callable] = {
        FixCategory.CRASH: lambda src, dst, **kw: rebuild_mopp(src, dst),
        FixCategory.HEAVY: lambda src, dst, **kw: simplify_collision(
            src, dst,
            strength=kw.get("strength", "normal"),
            deadmesh_dir=kw.get("deadmesh_dir"),
            stop_check=kw.get("stop_check"),
            item_progress=kw.get("item_progress"),
        ),
    }
    try:
        from dmfix.core.fixes.degenerate import fix_degenerate
        functions[FixCategory.DEGENERATE] = lambda src, dst, **kw: fix_degenerate(
            src, dst, deadmesh_dir=kw.get("deadmesh_dir")
        )
    except ImportError:
        pass
    try:
        from dmfix.core.fixes.winding import fix_inverted
        functions[FixCategory.INVERTED] = lambda src, dst, **kw: fix_inverted(
            src, dst, deadmesh_dir=kw.get("deadmesh_dir")
        )
    except ImportError:
        pass
    try:
        from dmfix.core.fixes.orphan import remove_orphan_collision
        functions[FixCategory.ORPHAN_BLOCKS] = lambda src, dst, **kw: remove_orphan_collision(
            src, dst, deadmesh_dir=kw.get("deadmesh_dir")
        )
    except ImportError:
        pass
    return functions


def _relative_mesh_path(file_path: str, scan_root: Path | None = None) -> str:
    """Keep paths below the meshes directory or the selected scan folder."""
    normalized = file_path.replace("/", "\\").lower()
    marker = "\\meshes\\"
    index = normalized.rfind(marker)
    if index < 0:
        if normalized.startswith("meshes\\"):
            return normalized
        if scan_root is not None:
            try:
                relative = Path(file_path).resolve().relative_to(scan_root.resolve())
                return str(relative).replace("/", "\\").lower()
            except ValueError:
                pass
        return Path(normalized).name
    return normalized[index + 1 :]


def _bsa_precedence(bsa_path: Path) -> tuple[int, str]:
    """Sort key approximating Skyrim archive precedence for same-path conflicts.

    A plugin's BSA loads with the plugin; esp loads after (overrides) esm. We
    match the BSA to a sibling plugin by name prefix. Loose files override all
    BSAs, which the pipeline handles separately.
    """
    stem = bsa_path.stem.lower()
    # "Midnight Sun - Update" matches "Midnight Sun - Update.esp"
    for suffix, rank in ((".esp", 2), (".esl", 1), (".esm", 0)):
        if (bsa_path.parent / f"{stem}{suffix}").exists():
            return (rank, stem)
        # textures/meshes split archives: "Foo - Textures.bsa" belongs to Foo.esm
        base = stem.split(" - ")[0]
        if (bsa_path.parent / f"{base}{suffix}").exists():
            return (rank, stem)
    return (0, stem)


def collect_work_items(
    target_folder: Path,
    options: PipelineOptions,
    progress: ProgressCallback,
) -> tuple[list[WorkItem], Path]:
    """Scan the target folder (loose + BSA members) and build the fix worklist.

    Returns (items needing a fix, temp dir holding extracted BSA members).
    The caller owns the returned temp dir; on exception it is already removed.
    """
    scanner = DmScan(options.deadmesh_dir)
    temp_root = Path(tempfile.mkdtemp(prefix="dmfix-bsa-"))
    try:
        return _collect_work_items(target_folder, options, progress, scanner, temp_root)
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _collect_work_items(
    target_folder: Path,
    options: PipelineOptions,
    progress: ProgressCallback,
    scanner: DmScan,
    temp_root: Path,
) -> tuple[list[WorkItem], Path]:
    progress("scan", 0, 1, str(target_folder))
    loose_records = scanner.scan_dir(target_folder)

    # winner per relative path: loose always beats BSA; higher-precedence BSA
    # beats lower.
    items: dict[str, WorkItem] = {}
    for record in loose_records:
        rel = _relative_mesh_path(record.file, target_folder)
        items[rel] = WorkItem(
            relative_path=rel,
            source_kind="loose",
            source_path=Path(record.file),
            record=record,
        )

    if options.include_bsa:
        bsa_files = sorted(
            (p for p in Path(target_folder).rglob("*.bsa")),
            key=_bsa_precedence,
        )
        for bsa_index, bsa_path in enumerate(bsa_files):
            progress("extract", bsa_index, len(bsa_files), bsa_path.name)
            extract_dir = temp_root / bsa_path.stem
            try:
                with BsaArchive(bsa_path) as archive:
                    nif_names = [n for n in archive.namelist() if n.endswith(".nif")]
                    for name in nif_names:
                        dest = (extract_dir / Path(name.replace("/", "\\"))).resolve()
                        # Defense in depth: BsaArchive rejects traversal paths
                        # at parse time, but never write outside the temp dir
                        # regardless of what the archive index claimed.
                        if not dest.is_relative_to(extract_dir.resolve()):
                            raise ValueError(f"entry escapes extraction dir: {name!r}")
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(archive.read(name))
            except ValueError as error:
                # Unreadable/unsupported archive: the loose scan still stands,
                # but surface the skip instead of dropping it silently.
                progress("extract", bsa_index, len(bsa_files),
                         f"{bsa_path.name} SKIPPED: {error}")
                continue
            if not extract_dir.is_dir():
                continue
            progress("scan", bsa_index, len(bsa_files), f"{bsa_path.name} (extracted)")
            for record in scanner.scan_dir(extract_dir):
                rel = _relative_mesh_path(record.file, extract_dir)
                existing = items.get(rel)
                if existing and existing.source_kind == "loose":
                    continue  # loose wins at runtime; the BSA copy is never loaded
                # among BSAs, later in precedence-sorted order wins
                items[rel] = WorkItem(
                    relative_path=rel,
                    source_kind="bsa",
                    source_path=bsa_path,
                    bsa_inner_path=rel.replace("\\", "/"),
                    record=record,
                )

    worklist = [item for item in items.values() if item.record and item.record.needs_fix]
    worklist.sort(key=lambda item: item.relative_path)
    return worklist, temp_root


def run_pipeline(
    target_folder: str | Path,
    options: PipelineOptions,
    progress: ProgressCallback | None = None,
    *,
    control: RunControl | None = None,
    on_event: EventCallback | None = None,
    work_items: list[WorkItem] | None = None,
) -> RunReport:
    progress = progress or (lambda *_: None)
    control = control or RunControl()
    on_event = on_event or (lambda _event: None)
    target_folder = Path(target_folder)
    scanner = DmScan(options.deadmesh_dir)
    fixes = _fix_functions()

    report = RunReport(
        scanned_folder=str(target_folder),
        output_folder=str(options.output_dir),
    )
    report.start()

    output_names: set[str] = set()
    output_names = _prepare_output_dir(
        options.output_dir, clean=options.clean_previous_outputs
    )

    temp_root: Path | None = None
    if work_items is None:
        worklist, temp_root = collect_work_items(target_folder, options, progress)
    else:
        worklist = list(work_items)
    if options.only_paths is not None:
        selected = {p.replace("/", "\\").lower() for p in options.only_paths}
        worklist = [item for item in worklist if item.relative_path in selected]
    report.total_items = len(worklist)
    # Flattening is intentionally fail-closed: two source paths with the same
    # basename cannot safely occupy one MO2 Meshes root.
    by_name: dict[str, list[WorkItem]] = {}
    for item in worklist:
        by_name.setdefault(Path(item.relative_path).name.casefold(), []).append(item)
    conflicts = {
        key: items for key, items in by_name.items() if len(items) > 1
    }
    if conflicts:
        remaining: list[WorkItem] = []
        for item in worklist:
            key = Path(item.relative_path).name.casefold()
            if key not in conflicts:
                remaining.append(item)
                continue
            result = _output_collision_result(item, conflicts[key])
            report.results.append(result)
            report.processed_items += 1
            on_event(PipelineEvent(
                PipelineEventKind.ITEM_COMPLETED,
                report.processed_items,
                report.total_items,
                item.relative_path,
                result,
            ))
        worklist = remaining
    work_root = Path(tempfile.mkdtemp(prefix="dmfix-work-"))
    try:
        for index, item in enumerate(worklist):
            was_paused = control.is_paused
            if was_paused:
                on_event(
                    PipelineEvent(
                        PipelineEventKind.RUN_PAUSED,
                        report.processed_items,
                        report.total_items,
                    )
                )
            if not control.checkpoint():
                report.status = "stopped"
                for pending in worklist[index:]:
                    report.results.append(_not_run_result(pending))
                on_event(
                    PipelineEvent(
                        PipelineEventKind.RUN_STOPPED,
                        report.processed_items,
                        report.total_items,
                    )
                )
                break
            if was_paused:
                on_event(
                    PipelineEvent(
                        PipelineEventKind.RUN_RESUMED,
                        report.processed_items,
                        report.total_items,
                    )
                )
            record = item.record
            assert record is not None
            progress("fix", report.processed_items, report.total_items, item.relative_path)
            on_event(
                PipelineEvent(
                    PipelineEventKind.ITEM_STARTED,
                    report.processed_items,
                    report.total_items,
                    item.relative_path,
                )
            )
            def item_progress(message: str) -> None:
                on_event(
                    PipelineEvent(
                        PipelineEventKind.ITEM_PROGRESS,
                        report.processed_items,
                        report.total_items,
                        item.relative_path,
                        message=message,
                    )
                )

            try:
                result = _process_item(
                    item,
                    record,
                    options,
                    fixes,
                    scanner,
                    work_root,
                    control.raise_if_stopped,
                    item_progress,
                    output_names,
                )
            except StopRequested:
                result = _not_run_result(
                    item, "run stopped during this file at a safe checkpoint"
                )
                report.results.append(result)
                on_event(
                    PipelineEvent(
                        PipelineEventKind.ITEM_COMPLETED,
                        report.processed_items,
                        report.total_items,
                        item.relative_path,
                        result,
                    )
                )
                for pending in worklist[index + 1 :]:
                    report.results.append(_not_run_result(pending))
                report.status = "stopped"
                on_event(
                    PipelineEvent(
                        PipelineEventKind.RUN_STOPPED,
                        report.processed_items,
                        report.total_items,
                    )
                )
                break
            report.results.append(result)
            report.processed_items += 1
            on_event(
                PipelineEvent(
                    PipelineEventKind.ITEM_COMPLETED,
                    report.processed_items,
                report.total_items,
                    item.relative_path,
                    result,
                )
            )
        else:
            on_event(
                PipelineEvent(
                    PipelineEventKind.RUN_FINISHED,
                    report.processed_items,
                    report.total_items,
                )
            )
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
        shutil.rmtree(work_root, ignore_errors=True)

    report.finish()
    report.save(report_output_dir(options.output_dir))
    return report


def _not_run_result(
    item: WorkItem, reason: str = "run stopped before this file started"
) -> FileResult:
    record = item.record
    assert record is not None
    return FileResult(
        source=(
            f"{item.source_path}::{item.bsa_inner_path}"
            if item.source_kind == "bsa"
            else str(item.source_path)
        ),
        relative_path=item.relative_path,
        categories=[category.value for category in record.categories],
        outcome=Outcome.NOT_RUN,
        reason=reason,
        verdict_before=record.verdict,
    )


def _output_collision_result(item: WorkItem, peers: list[WorkItem]) -> FileResult:
    record = item.record
    assert record is not None
    paths = ", ".join(sorted(peer.relative_path for peer in peers))
    return FileResult(
        source=(
            f"{item.source_path}::{item.bsa_inner_path}"
            if item.source_kind == "bsa"
            else str(item.source_path)
        ),
        relative_path=item.relative_path,
        categories=[category.value for category in record.categories],
        outcome=Outcome.FAILED,
        reason=f"output basename collision; flat Meshes output cannot choose between: {paths}",
        verdict_before=record.verdict,
    )


def _process_item(
    item: WorkItem,
    record: ScanRecord,
    options: PipelineOptions,
    fixes: dict[FixCategory, Callable],
    scanner: DmScan,
    work_root: Path,
    stop_check: Callable[[], None],
    item_progress: Callable[[str], None],
    output_names: set[str],
) -> FileResult:
    categories = [c for c in CATEGORY_ORDER if c in record.categories]
    base = FileResult(
        source=(
            f"{item.source_path}::{item.bsa_inner_path}"
            if item.source_kind == "bsa"
            else str(item.source_path)
        ),
        relative_path=item.relative_path,
        categories=[c.value for c in categories],
        outcome=Outcome.ERROR,
        verdict_before=record.verdict,
    )

    if FixCategory.UNFIXABLE in record.categories:
        base.outcome = Outcome.UNFIXABLE
        base.reason = (
            "ORPHAN MOPP: the collision tree exists but its geometry was stripped; "
            "there is nothing to rebuild from. Recreate the collision manually "
            "(Blender + PyNifly) or remove it in NifSkope."
        )
        return base

    selected = [c for c in categories if c in options.categories]
    if not selected:
        base.outcome = Outcome.SKIPPED
        base.reason = "all applicable fix categories are deselected"
        return base
    missing = [c.value for c in selected if c not in fixes]
    if missing:
        base.outcome = Outcome.ERROR
        base.reason = f"fix not implemented: {', '.join(missing)}"
        return base

    # Stage the source bytes as a loose work file.
    work_dir = work_root / f"{abs(hash(item.relative_path)):x}"
    work_dir.mkdir(parents=True, exist_ok=True)
    current = work_dir / Path(item.relative_path).name
    original = work_dir / f"original_{Path(item.relative_path).name}"
    try:
        if item.source_kind == "bsa":
            with BsaArchive(item.source_path) as archive:
                data = archive.read(item.bsa_inner_path)
            original.write_bytes(data)
        else:
            shutil.copyfile(item.source_path, original)
        shutil.copyfile(original, current)

        detail: dict = {}
        for category in selected:
            stop_check()
            step_out = work_dir / f"{category.value}_{current.name}"
            result = fixes[category](
                current,
                step_out,
                strength=options.strength,
                deadmesh_dir=options.deadmesh_dir,
                stop_check=stop_check,
                item_progress=item_progress,
            )
            stop_check()
            if not getattr(result, "success", True):
                base.outcome = Outcome.FAILED
                failures = tuple(getattr(result, "certification_failures", ()))
                failure_detail = ", ".join(failures) or getattr(
                    result, "verdict", "see detail"
                )
                reason = getattr(result, "reason", "")
                base.reason = reason or (
                    f"{category.value}: fix could not be certified "
                    f"({failure_detail})"
                )
                base.detail = {**detail, category.value: _result_detail(result)}
                return base
            detail[category.value] = _result_detail(result)
            current = step_out

        item_progress("final DeadMesh scan")
        stop_check()
        final = scanner.scan_file(current)
        base.verdict_after = final.verdict
        remaining = [c for c in final.categories if c in selected]
        if remaining:
            base.outcome = Outcome.FAILED
            base.reason = (
                "final dmscan verification still reports: "
                + ", ".join(c.value for c in remaining)
            )
            base.detail = detail
            return base
        if FixCategory.CRASH in final.categories:
            base.outcome = Outcome.FAILED
            base.reason = "fix introduced a crash-class defect; output withheld"
            base.detail = detail
            return base
        item_progress("final regression check")
        stop_check()
        if not scanner.vs_check(original, current):
            base.outcome = Outcome.FAILED
            base.reason = (
                "dmscan --vs: rebuild introduced an inverted-winding regression; "
                "output withheld"
            )
            base.detail = detail
            return base

        destination = mesh_output_path(options.output_dir, item.relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, destination)
        _record_output(options.output_dir, output_names, destination)
        base.outcome = Outcome.FIXED
        base.output_path = str(destination)
        base.detail = detail
        return base
    except StopRequested:
        raise
    except Exception as error:  # fail closed per file, keep the run going
        message = str(error)
        base.outcome = (
            Outcome.FAILED
            if message.startswith("UNSUPPORTED_MULTI_MOPP")
            else Outcome.ERROR
        )
        base.reason = f"{type(error).__name__}: {error}"
        return base
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _result_detail(result) -> dict:
    detail = {}
    for name in (
        "rounds",
        "old_triangle_count",
        "new_triangle_count",
        "old_cull_worst",
        "new_cull_worst",
        "verdict",
        "triangle_count",
        "old_block_size",
        "new_block_size",
        "certification_failures",
        "reason",
    ):
        value = getattr(result, name, None)
        if value is not None:
            detail[name] = value
    return detail
